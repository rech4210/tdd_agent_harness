"""하네스 자기 검사: G0 가 git 이 출력한 경로를 실제 경로로 읽는지 (프로젝트 인계 S04, WO-H16).

G0(check_protected_paths.py)는 git 출력에서 바뀐 파일 목록을 만든다. 출력을 그대로 공백으로 자르면
- 비 ASCII 경로는 git 이 따옴표 · 8진수로 인용해(core.quotePath) 역할 glob 과 맞지 않는다 → 거짓 권한 위반,
  그리고 그 파일이 "없는 파일"로 보여 우회 패턴 검사(검사 2)를 건너뛴다
- 공백이 든 경로는 여러 조각으로 잘린다
- 이름 바꾸기 감지(diff.renames)가 켜져 있으면 옛 경로가 목록에서 빠져, 금지 경로의 파일을 허용 경로로 옮겨도 통과한다

기대: changed_files 는 실제 경로(인용 · 분할 없음)이고, 이름 바꾸기는 옛 경로 · 새 경로를 모두 검사한다.
P01 ~ P07 은 WO-H16 전 Red, P08 · P09 는 회귀 방지, P10 (UTF-8 이 아닌 이름은 실패로 닫힌다) 은 WO-H16 판정에서 찾아 WO-H17 전 Red.

임시 git 저장소에 하네스(harness/ · adapters/)를 복사하고 G0 를 실제로 실행한다.
사용: python harness/selftest/check_g0_paths_cases.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

GATES = '[harness]\nadapter = "python"\nlocked = ["tests/**"]\n'
ROLES = """
[roles.test_author]
allow = ["tests/**", "reports/**"]
deny  = ["src/**", "config/**"]

[roles.implementer]
allow = ["src/**", "reports/**"]
deny  = ["tests/**", "config/**"]

[separation]
pairs = [["test_author", "implementer"]]
"""

NON_ASCII = "메모"  # 비 ASCII 두 글자 (git 이 8진수로 인용하는 이름)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, encoding="utf-8", check=True).stdout


def make_repo() -> Path:
    repo = Path(tempfile.mkdtemp(prefix="g0-paths-"))
    shutil.copytree(ROOT / "harness", repo / "harness", ignore=shutil.ignore_patterns("__pycache__", "UPSTREAM"))
    shutil.copytree(ROOT / "adapters", repo / "adapters", ignore=shutil.ignore_patterns("__pycache__"))
    files = {
        "config/gates.toml": GATES,
        "config/roles.toml": ROLES,
        "config/policy.toml": "limit = 1\n" * 20,
        "tests/unit/test_x.py": "def test_x() -> None:\n    assert True\n",
        "src/demo/__init__.py": "",
        "reports/.keep": "",
        ".gitignore": "__pycache__/\n*.pyc\nreports/g0_report.json\n",
    }
    for rel, text in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "selftest@example.invalid")
    git(repo, "config", "user.name", "selftest")
    # 기본값을 명시한다: 인용(core.quotePath)과 이름 바꾸기 감지(diff.renames)가 켜진 보통의 git
    git(repo, "config", "core.quotePath", "true")
    git(repo, "config", "diff.renames", "true")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    subprocess.run([sys.executable, "harness/check_protected_paths.py", "--write-lock"], cwd=repo,
                   capture_output=True, text=True, check=True)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "lock")
    git(repo, "tag", "base")
    return repo


def reset(repo: Path) -> None:
    git(repo, "reset", "-q", "--hard", "base")
    git(repo, "clean", "-qfdx", "-e", "harness", "-e", "adapters")


def write(repo: Path, rel: str, text: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="\n")


def g0(repo: Path, role: str = "implementer") -> tuple[bool, list[str], list[str]]:
    p = subprocess.run([sys.executable, "harness/check_protected_paths.py", "--base", "base", "--role", role],
                       cwd=repo, capture_output=True, text=True, encoding="utf-8")
    report_path = repo / "reports" / "g0_report.json"
    if not report_path.exists():
        return False, [f"보고서 없음 (exit={p.returncode}) {p.stderr.strip()[-200:]}"], []
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report_path.unlink()
    return p.returncode == 0 and report["passed"], report["violations"], report["changed_files"]


def main() -> int:
    results: list[tuple[str, bool, str]] = []
    repo = make_repo()
    try:
        # P01: 허용 경로(reports/) 안의 비 ASCII 이름 새 파일 (커밋 안 함) → 통과, 목록에 실제 경로
        reset(repo)
        name = f"reports/{NON_ASCII}.md"
        write(repo, name, "x\n")
        ok, bad, files = g0(repo)
        results.append(("P01 허용 경로의 비 ASCII 새 파일(추적 안 됨) → 통과, changed_files 에 실제 경로",
                         ok and name in files, f"files={files} bad={bad[:2]}"))

        # P02: 같은 파일을 커밋한 뒤 (git diff 경로) → 통과
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "non-ascii report")
        ok, bad, files = g0(repo)
        results.append(("P02 허용 경로의 비 ASCII 파일(커밋됨) → 통과, changed_files 에 실제 경로",
                         ok and name in files, f"files={files} bad={bad[:2]}"))

        # P03: 공백이 든 이름 → 한 경로로 읽고 통과
        reset(repo)
        name = "reports/two words.md"
        write(repo, name, "x\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "space")
        ok, bad, files = g0(repo)
        results.append(("P03 허용 경로의 공백 든 파일 → 한 경로로 읽고 통과",
                         ok and files == [name], f"files={files} bad={bad[:2]}"))

        # P04: 허용 경로 밖의 비 ASCII 파일 → 위반 메시지에 실제 경로 (인용 · 8진수 아님)
        reset(repo)
        name = f"docs/{NON_ASCII}.md"
        write(repo, name, "x\n")
        ok, bad, files = g0(repo)
        results.append(("P04 허용 경로 밖의 비 ASCII 파일 → [권한] 위반, 메시지에 실제 경로",
                         not ok and any("[권한]" in b and name in b for b in bad), "; ".join(bad[:2])))

        # P05: deny 경로의 비 ASCII 파일 → deny 로 판정 ("바꿀 수 없다")
        reset(repo)
        name = f"tests/unit/test_{NON_ASCII}.py"
        write(repo, name, "def test_y() -> None:\n    assert True\n")
        ok, bad, files = g0(repo)
        results.append(("P05 deny 경로(tests/)의 비 ASCII 파일 → deny 위반 ('바꿀 수 없다')",
                         not ok and any("[권한]" in b and name in b and "바꿀 수 없다" in b for b in bad), "; ".join(bad[:2])))

        # P06: 허용 경로의 비 ASCII · 공백 소스 파일에 우회 패턴 → [우회] 를 잡는다 (검사 2 를 건너뛰지 않는다)
        reset(repo)
        names = [f"src/demo/{NON_ASCII}.py", "src/demo/two words.py"]
        for n in names:
            write(repo, n, "def f() -> int:  # pragma: no cover\n    return 1\n")
        ok, bad, files = g0(repo)
        caught = [n for n in names if any("[우회]" in b and n in b for b in bad)]
        results.append(("P06 비 ASCII · 공백 이름 소스 파일의 우회 패턴 → 두 파일 모두 [우회]",
                         not ok and caught == names and not any("[권한]" in b for b in bad), "; ".join(bad[:3])))

        # P07: 금지 경로(config/)의 파일을 허용 경로(reports/)로 옮긴다 → 옛 경로 삭제가 [권한] 위반
        reset(repo)
        git(repo, "mv", "config/policy.toml", "reports/policy.toml")
        git(repo, "commit", "-qm", "move protected file")
        ok, bad, files = g0(repo)
        results.append(("P07 config/ 파일을 reports/ 로 이름 바꾸기(커밋) → 옛 경로 config/policy.toml [권한] 위반",
                         not ok and "config/policy.toml" in files
                         and any("[권한]" in b and "config/policy.toml" in b for b in bad), f"files={files} bad={bad[:2]}"))

        # P10 (WO-H16 판정에서 발견): UTF-8 이 아닌 파일 이름. 디코딩을 대체 문자로 넘기면 실제 파일을 찾지 못해
        # 우회 패턴 검사(검사 2)를 건너뛰고 허용 경로 glob 은 통과한다 → 실패로 닫혀야 한다 ([경로] 위반).
        # 파일 시스템이 그런 이름을 거부하면(Windows · macOS) 이 우회 자체가 불가능하므로 건너뛴다 (결과에 표시)
        reset(repo)
        raw = b"src/demo/bad\xff.py"
        try:
            with open(bytes(repo) + b"/" + raw, "wb") as fh:
                fh.write(b"def f() -> int:  # pragma: no cover\n    return 1\n")
            created = True
        except (OSError, UnicodeError, ValueError):
            # Windows 는 바이트 경로를 UTF-8 로 해석하려다 UnicodeDecodeError 를 낸다 (OSError 가 아니다)
            created = False
        if created:
            ok, bad, files = g0(repo)
            results.append(("P10 UTF-8 이 아닌 소스 파일 이름 + 우회 패턴 → 실패 ([경로] 해석할 수 없는 이름)",
                            not ok and any("[경로]" in b for b in bad), f"files={files} bad={bad[:2]}"))
        else:
            results.append(("P10 UTF-8 이 아닌 파일 이름 (파일 시스템이 거부해 건너뜀)", True, "건너뜀"))

        # P08 (회귀 방지): ASCII 경로는 지금처럼 판정한다
        reset(repo)
        write(repo, "reports/plain.md", "x\n")
        write(repo, "src/demo/core.py", "X = 1\n")
        ok, bad, files = g0(repo)
        results.append(("P08 ASCII 허용 경로 변경 → 통과 (회귀 방지)",
                         ok and files == ["reports/plain.md", "src/demo/core.py"], f"files={files} bad={bad[:2]}"))

        # P09 (회귀 방지): 금지 경로 삭제(이름 바꾸기 아님)는 지금처럼 위반
        reset(repo)
        git(repo, "rm", "-q", "config/policy.toml")
        git(repo, "commit", "-qm", "delete protected file")
        ok, bad, files = g0(repo)
        results.append(("P09 config/ 파일 삭제 → [권한] 위반 (회귀 방지)",
                         not ok and any("[권한]" in b and "config/policy.toml" in b for b in bad), "; ".join(bad[:2])))
    finally:
        shutil.rmtree(repo, ignore_errors=True)

    all_good = True
    for name, good, detail in results:
        all_good &= good
        print(f"{'OK ' if good else 'BAD'} {name}{('  — ' + detail) if detail else ''}")
    print("결과: 모든 사례가 기대와 일치" if all_good else "결과: 기대와 다른 사례가 있다")
    return 0 if all_good else 1


if __name__ == "__main__":
    sys.exit(main())
