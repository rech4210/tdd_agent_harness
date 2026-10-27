"""하네스 자기 검사: 문서 낡음 점검 (WO-H41).

계약 (scripts/check_docs_fresh.py, Core 밖 · 비공개 도구, 읽기 전용)
  사용: python scripts/check_docs_fresh.py [--root <저장소>]   (기본: 이 도구가 든 저장소)
  목록 파일: <root>/dev/doc-rules.toml — [[stale]] text · since, [[required]] file · text · since,
            [scope] stale_exclude = [glob …] (C3 를 보지 않는 이력 파일, 루트 기준 / 구분 경로).
  점검 대상 문서: <root>/*.md, dev/**/*.md, docs/**/*.md, templates/**/*.md (C1 · C2 · C3).
  C1 결정 번호: dev/DECISIONS.md 의 “### HD-<n>” 제목이 중복이거나 앞 제목보다 작으면 FAIL.
     정의된 번호 = 그 제목들 + 표 행 “| HD-<n> |”. 점검 대상 문서가 정의되지 않은 HD-<n> 을 쓰면 FAIL (내용에 번호).
  C2 링크: 마크다운 링크 [..](대상) 중 http(s): · mailto: · # 로 시작하지 않는 대상의 파일(“#” 앞)이 없으면 FAIL
     (내용에 문서 경로와 대상). ``` 코드 블록 안은 보지 않는다.
  C3 낡은 문구: [[stale]] text 가 stale_exclude 밖 점검 대상 문서에 있으면 FAIL (내용에 경로:줄 번호와 since).
  C4 필수 문구: [[required]] file 이 없거나 text 를 담지 않으면 FAIL (내용에 file 과 text).
  C5 현황판: dev/WO-H<n>.md 중 n 이 가장 큰 것이 HARNESS_STATUS.md 에 없으면 WARN (숫자 비교: H10 > H9).
     HARNESS_STATUS.md 의 한 줄에 “WO-H<n>” 과 “수용” 이 함께 있는데 dev/review/JUDGE-wo-h<n>.md 가 없으면 FAIL.
  C6 공개 금지 단어: public/export.toml [scan] deny_words 를 [export] include 경로(작업 트리, git 무시 파일 제외)에서
     대소문자 무시로 찾으면 FAIL (내용에 경로). harness/selftest/check_export_cases.py 는 제외(금지 단어 사례를 담음).
  출력: “| 점검 | 결과 | 내용 |” 표 (점검 칸은 C1 ~ C6 로 시작, 결과 PASS · WARN · FAIL), 점검마다 문제가 없으면 PASS 한 행.
        끝에 “판정:” 줄. 종료: FAIL 이 있으면 1, 아니면 0. 파일을 바꾸지 않는다.

사용: python harness/selftest/check_docs_fresh_cases.py
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable
TOOL = ROOT / "scripts" / "check_docs_fresh.py"

RULES = """[scope]
stale_exclude = ["dev/review/**", "dev/DECISIONS.md", "dev/doc-rules.toml"]

[[stale]]
text = "옛 보류 문구"
since = "HD-02"

[[required]]
file = "templates/roles/lead/CLAUDE.md"
text = "필수 단계"
since = "HD-01"
"""
EXPORT = """[export]
include = ["harness/**", "docs/**"]

[scan]
deny_words = ["secretname", "zzforbid"]
"""


def write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="\n")


def fixture(base: Path, name: str) -> Path:
    r = base / name
    write(r, "dev/DECISIONS.md", "# 원장\n\n| HD-01 | 첫 결정 |\n\n### HD-02 (둘째)\n\n### HD-03 (셋째)\n")
    write(r, "dev/doc-rules.toml", RULES)
    write(r, "public/export.toml", EXPORT)
    write(r, "HARNESS_STATUS.md", "# 현황\n\n> WO-H01 수용. [판정](dev/review/JUDGE-wo-h01.md)\n> WO-H02 진행.\n")
    write(r, "dev/WO-H01.md", "# WO-H01\n")
    write(r, "dev/WO-H02.md", "# WO-H02\n")
    write(r, "dev/review/JUDGE-wo-h01.md", "# 판정 (옛 보류 문구는 이력이라 괜찮다)\n")
    write(r, "docs/a.md", "# A\n\n[B](b.md#절) · [밖](https://example.com) · [위](#a) · HD-03 참고\n")
    write(r, "docs/b.md", "# B\n\n```\n[코드 안](없음.md)\n```\n")
    write(r, "templates/roles/lead/CLAUDE.md", "## 안내\n- 필수 단계\n")
    write(r, "harness/core.py", "VALUE = 1\n")
    write(r, "dev/note.md", "zzforbid 는 비공개 경로라 괜찮다\n")
    subprocess.run(["git", "init", "-q"], cwd=r, capture_output=True)
    return r


def run(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([PY, str(TOOL), "--root", str(root)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def rows(out: str, check: str, result: str) -> list[str]:
    return [l for l in out.splitlines() if l.startswith(f"| {check}") and f"| {result} |" in l]


def digest(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file() and ".git" not in p.parts}


def main() -> int:
    if not TOOL.exists() and not (ROOT / "dev").exists():
        print("알림: 비공개 도구가 없는 공개본이라 건너뜁니다.")
        return 0
    has = TOOL.exists()
    out_rows: list[tuple[str, bool, str]] = []

    def case(name: str, good: bool, detail: str = "") -> None:
        out_rows.append((name, bool(good), detail.replace("\n", " | ")[:220]))

    base = Path(tempfile.mkdtemp(prefix="docs-fresh-"))
    try:
        r0 = fixture(base, "d01")
        before = digest(r0)
        p = run(r0) if has else None
        o = p.stdout if p else ""
        case("D01 깨끗한 저장소 → 종료 0, FAIL 없음, C1~C6 마다 행, 판정 줄",
             has and p.returncode == 0 and not any(rows(o, f"C{i}", "FAIL") for i in range(1, 7))
             and all(any(l.startswith(f"| C{i}") for l in o.splitlines()) for i in range(1, 7)) and "판정:" in o,
             (o + (p.stderr if p else ""))[-300:])
        case("D08 읽기 전용 (파일 그대로)", has and digest(r0) == before)

        r = fixture(base, "d02")
        write(r, "dev/DECISIONS.md", "| HD-01 | 첫 |\n\n### HD-03 (셋째)\n\n### HD-02 (둘째)\n\n### HD-03 (다시)\n")
        write(r, "docs/c.md", "HD-99 를 따른다\n")
        p = run(r) if has else None
        f = rows(p.stdout, "C1", "FAIL") if p else []
        case("D02 C1: 중복 · 감소 제목, 원장에 없는 HD-99 → FAIL, 종료 1",
             has and p.returncode == 1 and any("HD-03" in l for l in f) and any("HD-02" in l for l in f)
             and any("HD-99" in l for l in f), str(f)[:220])

        r = fixture(base, "d03")
        write(r, "docs/a.md", "[없는 곳](missing.md) · [있는 곳](b.md)\n")
        p = run(r) if has else None
        f = rows(p.stdout, "C2", "FAIL") if p else []
        case("D03 C2: 없는 대상 링크 → FAIL(경로 · 대상), 코드 블록 · 외부 · # 링크는 무시",
             has and len(f) == 1 and "docs/a.md" in f[0] and "missing.md" in f[0], str(f)[:220])

        r = fixture(base, "d04")
        write(r, "docs/a.md", "# A\n\n둘째 줄\n여기 옛 보류 문구 남음\n")
        p = run(r) if has else None
        f = rows(p.stdout, "C3", "FAIL") if p else []
        case("D04 C3: 낡은 문구 → FAIL(경로:줄 · since), 제외 경로(dev/review)의 같은 문구는 무시",
             has and len(f) == 1 and "docs/a.md:4" in f[0] and "HD-02" in f[0], str(f)[:220])

        r = fixture(base, "d05")
        write(r, "templates/roles/lead/CLAUDE.md", "## 안내\n")
        p1 = run(r) if has else None
        (r / "templates" / "roles" / "lead" / "CLAUDE.md").unlink()
        p2 = run(r) if has else None
        f1 = rows(p1.stdout, "C4", "FAIL") if p1 else []
        f2 = rows(p2.stdout, "C4", "FAIL") if p2 else []
        case("D05 C4: 필수 문구 없음 · 파일 없음 → FAIL(파일 · 문구)",
             has and len(f1) == 1 and "필수 단계" in f1[0] and "templates/roles/lead/CLAUDE.md" in f1[0] and len(f2) == 1,
             str(f1 + f2)[:220])

        r = fixture(base, "d06")
        write(r, "dev/WO-H9.md", "# 9\n")
        write(r, "dev/WO-H10.md", "# 10\n")
        write(r, "HARNESS_STATUS.md", "# 현황\n\n> WO-H09 진행\n> WO-H02 수용 (판정 문서 없음)\n")
        p = run(r) if has else None
        w = rows(p.stdout, "C5", "WARN") if p else []
        f = rows(p.stdout, "C5", "FAIL") if p else []
        case("D06 C5: 최신 WO(H10, 숫자 비교)가 현황판에 없음 → WARN, 수용인데 판정 없음(H02) → FAIL",
             has and any("WO-H10" in l for l in w) and any("WO-H02" in l for l in f), str(w + f)[:220])

        r = fixture(base, "d07")
        write(r, "harness/leak.py", "# ZZForbid 이름이 새어 나감\n")
        write(r, "harness/selftest/check_export_cases.py", "WORDS = ['secretname']\n")
        p = run(r) if has else None
        f = rows(p.stdout, "C6", "FAIL") if p else []
        case("D07 C6: 공개 경로의 금지 단어(대소문자 무시) → FAIL, 비공개 경로 · 제외 파일은 무시",
             has and len(f) == 1 and "harness/leak.py" in f[0], str(f)[:220])

        # WO-H41 실제 저장소 첫 실행에서 나온 오탐 3종 (리드 보정)
        r = fixture(base, "d11")
        write(r, "dev/DECISIONS.md", "| HD-01 | 첫 |\n\n### HD-02 (둘째)\n\n### HD-03 (셋째)\n\n### HD-02 후속 집행\n\n"
              "### HD-01 역할 경계\n\n### HD-02 (충돌)\n")
        with (r / "dev" / "doc-rules.toml").open("a", encoding="utf-8", newline="\n") as fh:
            fh.write('\n[c1]\nfollowup_headings = ["### HD-01 역할 경계"]\n')
        p = run(r) if has else None
        f = rows(p.stdout, "C1", "FAIL") if p else []
        case("D11 C1: 제목에 후속 · 보강 또는 허용 목록이면 재사용 허용, 그 밖의 재사용(충돌)은 FAIL",
             has and len(f) == 1 and "HD-02" in f[0] and ":11:" in f[0], str(f)[:220])
        r = fixture(base, "d12")
        write(r, "dev/archive/copy.md", "[바깥 저장소 경로](../../other/x.md)\n")
        with (r / "dev" / "doc-rules.toml").open("a", encoding="utf-8", newline="\n") as fh:
            fh.write("")
        rules = (r / "dev" / "doc-rules.toml").read_text(encoding="utf-8").replace(
            "[scope]\n", '[scope]\nlink_exclude = ["dev/archive/**"]\n', 1)
        write(r, "dev/doc-rules.toml", rules)
        p = run(r) if has else None
        case("D12 C2: [scope] link_exclude 경로의 링크는 보지 않는다",
             has and not rows(p.stdout, "C2", "FAIL"), p.stdout[-200:] if p else "")
        r = fixture(base, "d13")
        (r / "dev" / "review" / "JUDGE-wo-h01.md").rename(r / "dev" / "review" / "JUDGE-wo-h01-final.md")
        p = run(r) if has else None
        case("D13 C5: 단계별 판정 문서(JUDGE-wo-h<n>-*.md)도 판정 문서로 인정",
             has and not rows(p.stdout, "C5", "FAIL"), p.stdout[-200:] if p else "")

        p = run(ROOT) if has else None
        case("D09 실제 저장소: 오류 없이 표와 판정 줄 (결과는 묻지 않음)",
             has and p.returncode in (0, 1) and "판정:" in p.stdout and "Traceback" not in p.stderr,
             (p.stdout + p.stderr)[-200:] if p else "")
        case("D10 도구는 Core 밖 (harness/ 에 없다)", has and not list((ROOT / "harness").glob("check_docs_fresh*.py")))
    finally:
        shutil.rmtree(base, ignore_errors=True)

    all_good = True
    for name, good, detail in out_rows:
        all_good &= good
        print(f"{'OK ' if good else 'BAD'} {name}  — {detail}")
    print("결과: 모든 사례가 기대와 일치" if all_good else "결과: 기대와 다른 사례가 있다")
    return 0 if all_good else 1


if __name__ == "__main__":
    sys.exit(main())
