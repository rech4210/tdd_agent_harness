"""하네스 자기 검사: 명세(specs/**)의 쓰기 권한 · 잠금 판정 사례 (G0, SDD 통합).

명세는 H3 에서 테스트와 함께 잠긴다. 잠금 뒤 명세가 바뀌면 권위 순서(잠긴 테스트 > 결정 > 불변식 > 명세 산문)가
기계적으로 지켜지지 않는다. 명세를 쓰는 역할(spec_author)은 첫 잠금 전에 일하므로 테스트 작성자처럼 잠금 검사에서 빠진다.

임시 git 저장소에 하네스(harness/ · adapters/)를 복사하고 G0(check_protected_paths.py)을 실제로 실행한다.
사용: python harness/selftest/check_g0_spec_cases.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

GATES = '[harness]\nadapter = "python"\nlocked = ["tests/**", "specs/**"]\n'
ROLES = """
[roles.spec_author]
allow = ["specs/**", "reports/**"]
deny  = ["src/**", "tests/**", "config/**"]
lock_exempt = true

[roles.test_author]
allow = ["tests/**", "reports/**"]
deny  = ["src/**", "specs/**", "config/**"]

[roles.implementer]
allow = ["src/**", "reports/**"]
deny  = ["tests/**", "specs/**", "config/**"]

[separation]
pairs = [["test_author", "implementer"]]
"""


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout


def make_repo() -> Path:
    repo = Path(tempfile.mkdtemp(prefix="g0-spec-"))
    shutil.copytree(ROOT / "harness", repo / "harness", ignore=shutil.ignore_patterns("__pycache__", "UPSTREAM"))
    shutil.copytree(ROOT / "adapters", repo / "adapters", ignore=shutil.ignore_patterns("__pycache__"))
    (repo / "config").mkdir()
    (repo / "config" / "gates.toml").write_text(GATES, encoding="utf-8")
    (repo / "config" / "roles.toml").write_text(ROLES, encoding="utf-8")
    for rel, text in {"specs/001-money/spec.md": "- **FR-001**: 음수 거부\n",
                      "tests/unit/test_money.py": "def test_x() -> None:\n    assert True\n",
                      "src/demo/__init__.py": "",
                      # 실제 프로젝트처럼 바이트코드를 무시한다. 없으면 G0 자신의 import 가 만든 .pyc 가 역할의 변경으로 잡힌다
                      ".gitignore": "__pycache__/\n*.pyc\n"}.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "selftest@example.invalid")
    git(repo, "config", "user.name", "selftest")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    git(repo, "tag", "base")
    return repo


def g0(repo: Path, role: str) -> tuple[bool, list[str]]:
    p = subprocess.run([sys.executable, "harness/check_protected_paths.py", "--base", "base", "--role", role],
                       cwd=repo, capture_output=True, text=True)
    report = json.loads((repo / "reports" / "g0_report.json").read_text(encoding="utf-8"))
    return p.returncode == 0 and report["passed"], report["violations"]


def write_lock(repo: Path) -> dict:
    subprocess.run([sys.executable, "harness/check_protected_paths.py", "--write-lock"], cwd=repo,
                   capture_output=True, text=True, check=True)
    return json.loads((repo / "config" / "test_lock.json").read_text(encoding="utf-8"))


def main() -> int:
    results: list[tuple[str, bool, str]] = []
    repo = make_repo()
    try:
        # S01: 첫 잠금 전, 명세 작성자가 명세를 고친다 → 통과 (잠금 검사 제외 역할)
        (repo / "specs/001-money/spec.md").write_text("- **FR-001**: 음수 거부\n- **FR-002**: 합\n", encoding="utf-8")
        ok, bad = g0(repo, "spec_author")
        results.append(("S01 첫 잠금 전 spec_author 가 명세 수정 → 통과", ok, "; ".join(bad[:2])))

        # S02: 잠금이 명세를 포함한다
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "spec")
        lock = write_lock(repo)
        results.append(("S02 잠금에 specs/** 가 들어간다", any(k.startswith("specs/") for k in lock), str(sorted(lock))[:90]))
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "lock")
        git(repo, "tag", "-f", "base")

        # S03: 잠금 뒤 구현자가 명세를 고친다 → 실패 (권한 + 잠금)
        (repo / "specs/001-money/spec.md").write_text("- **FR-001**: 음수 허용\n", encoding="utf-8")
        ok, bad = g0(repo, "implementer")
        results.append(("S03 잠금 뒤 implementer 가 명세 수정 → 실패", not ok and any("[권한]" in b for b in bad)
                        and any("[잠금]" in b and "specs/" in b for b in bad), "; ".join(bad[:3])))
        git(repo, "checkout", "-q", "--", "specs")

        # S04: 잠금 뒤 명세 작성자가 명세를 고치면, 다음 구현자 판정의 잠금 검사가 잡는다
        (repo / "specs/001-money/spec.md").write_text("- **FR-001**: 음수 허용\n", encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "spec change after lock")
        git(repo, "tag", "-f", "base")
        (repo / "src/demo/__init__.py").write_text("X = 1\n", encoding="utf-8")
        ok, bad = g0(repo, "implementer")
        results.append(("S04 잠금 뒤 명세 변경 → 구현자 판정에서 [잠금] 실패", not ok
                        and any("[잠금]" in b and "specs/" in b for b in bad), "; ".join(bad[:2])))

        # S05: 잠금 검사 제외는 설정한 역할만. 이름이 같아도 lock_exempt 가 없으면 검사한다 (test_author 는 예외 유지)
        roles = ROLES.replace("lock_exempt = true\n", "")
        (repo / "config" / "roles.toml").write_text(roles, encoding="utf-8")
        git(repo, "checkout", "-q", "--", "src")
        (repo / "specs/001-money/spec.md").write_text("- **FR-001**: 또 바뀜\n", encoding="utf-8")
        ok, bad = g0(repo, "spec_author")
        results.append(("S05 lock_exempt 없는 spec_author 는 잠금 검사 대상 → 실패", not ok
                        and any("[잠금]" in b for b in bad), "; ".join(bad[:2])))

        # S06: lock_exempt 가 참 · 거짓(boolean)이 아니면 설정 오류로 실패 (문자열 "false" 를 참으로 읽지 않는다, 레드팀 G0-01)
        (repo / "config" / "roles.toml").write_text(ROLES.replace("lock_exempt = true", 'lock_exempt = "false"'),
                                                     encoding="utf-8")
        ok, bad = g0(repo, "spec_author")
        results.append(("S06 lock_exempt = \"false\"(문자열) → [설정] 실패", not ok and any("[설정]" in b for b in bad),
                        "; ".join(bad[:2])))
        # H-07: 지시서 발행은 lead의 test_author 역할.
        git(repo, "checkout", "-q", "--", "specs")
        (repo / "config/roles.toml").write_bytes((ROOT / "config/roles.toml").read_bytes())
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", "actual roles")
        git(repo, "tag", "-f", "base")
        order = repo / "work_orders/WO-14.md"
        order.parent.mkdir()
        order.write_text("# WO-14\n최소 사례\n", encoding="utf-8")
        git(repo, "add", "work_orders")
        git(repo, "commit", "-qm", "issue order")
        ok, bad = g0(repo, "test_author")
        results.append(("H07-02 실제 roles의 test_author가 지시서 커밋 → G0 통과", ok, "; ".join(bad)))
        ok, bad = g0(repo, "implementer")
        results.append(("H07-R01 implementer의 지시서 변경은 여전히 권한 위반", not ok and any("[권한]" in b and "work_orders/" in b for b in bad), "; ".join(bad)))
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
