"""하네스 자기 검사 (S3): Core(harness/*.py)에 언어 · 도구 이름이 없는가.

기본값과 도구 명령은 설정(*.toml)과 어댑터(adapters/)에만 둔다. Core 코드 경로에 아래 식별자가 있으면 실패한다.
주석 · 문서 문자열도 검사한다(이름이 섞여 들어오는 첫 경로이기 때문). 예외가 필요하면 이 목록을 사람이 고친다(H10).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN = [
    "pytest", "mutmut", "mypy", "xenon", "bandit", "hypothesis", "coverage.py", "num_branches",
    "pyproject", ".venv", "wsl",
]
# 사용: python harness/selftest/check_core_agnostic.py [프로젝트 · 도메인 단어 ...]   (추가 단어도 금지한다)


def main() -> int:
    forbidden = FORBIDDEN + sys.argv[1:]
    files = sorted((ROOT / "harness").glob("*.py"))
    if not files:
        print("검사할 Core 파일이 없다 (공허한 참 방지)")
        return 1
    bad = []
    for f in files:
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            for word in forbidden:
                if re.search(re.escape(word), line, re.IGNORECASE):
                    bad.append(f"{f.relative_to(ROOT).as_posix()}:{n}: '{word}' :: {line.strip()[:90]}")
    print(f"Core 파일 {len(files)}개 검사, 위반 {len(bad)}건")
    for b in bad:
        print("  " + b)
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
