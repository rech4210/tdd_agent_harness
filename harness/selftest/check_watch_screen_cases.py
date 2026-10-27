"""하네스 자기 검사: 감시 도구 화면 줄 고르기 (WO-H56, 독립 일감 F10).

배경: 감시 로그의 화면 줄이 빈 입력줄(`>`)이나 팁(`└ Tip: ...`)을 골라 정보가 없었다(WO-H42 · H45 · H48 로그).

계약 (adapters/tools/orca_watch.py 의 extract_screen_line(text) -> str)
- 지금 규칙(빈 줄 · 구분선 · 하단 안내 제외, 120자) 에 더해, 다음 줄도 건너뛴다:
  (a) 앞뒤 공백을 지운 내용이 입력 표시 문자 하나뿐인 줄: `>` · `›` · `❯`
  (b) 입력 표시 문자 뒤 Codex 자리 문구: `› Ask Codex to do anything` 처럼 `Ask Codex` 로 시작하는 줄
  (c) 앞의 상자 문자(└ ├ │ 등)와 공백을 지운 뒤 `Tip:` 으로 시작하는 줄
- `> 지시서를 읽고 …` 처럼 입력 표시 뒤에 실제 글이 있는 줄은 그대로 고른다. 모두 건너뛰면 빈 문자열.

사용: python harness/selftest/check_watch_screen_cases.py
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "adapters" / "tools"))


def main() -> int:
    rows: list[tuple[str, bool, str]] = []

    def case(name: str, good: bool, detail: str = "") -> None:
        rows.append((name, bool(good), detail))

    w = importlib.import_module("orca_watch")
    pick = w.extract_screen_line
    body = "● Edited 2 files (step.py, mark.py)\n"
    r1 = pick(body + ">\n")
    r1b = pick(body + "  >  \n")
    case("S01 마지막이 빈 입력줄 `>` → 그 위 줄", r1 == "● Edited 2 files (step.py, mark.py)" and r1b == r1, f"{r1!r} {r1b!r}")
    r2 = pick(body + "› Ask Codex to do anything\n")
    r2b = pick(body + "❯\n")
    r2c = pick(body + "›\n")
    case("S02 Codex 자리 문구 · `❯` · `›` 단독 → 그 위 줄", r2 == r2b == r2c == "● Edited 2 files (step.py, mark.py)", f"{r2!r} {r2b!r} {r2c!r}")
    r3 = pick(body + "└ Tip: Press ? to see keyboard shortcuts.\n")
    r3b = pick(body + "Tip: Use /voice to record\n")
    case("S03 팁 줄(`└ Tip:` · `Tip:`) → 그 위 줄", r3 == r3b == "● Edited 2 files (step.py, mark.py)", f"{r3!r} {r3b!r}")
    r4 = pick("> 지시서를 읽고 고치세요\n")
    case("S04 입력 표시 뒤에 글이 있으면 고른다", r4 == "> 지시서를 읽고 고치세요", repr(r4))
    r5 = pick(body + "> Accept-edits mode: file edits auto-approved\n" + "─" * 20 + "\nesc to cancel   accept-edits · Gemini\n")
    case("S05 기존 규칙 유지: 하단 안내 · 구분선 제외", r5 == "● Edited 2 files (step.py, mark.py)", repr(r5))
    r6 = pick(">\n└ Tip: x\n" + "─" * 10 + "\n")
    case("S06 모두 건너뛰면 빈 문자열", r6 == "", repr(r6))
    ok = all(g for _, g, _ in rows)
    for n, g, d in rows:
        print(f"{'OK ' if g else 'BAD'} {n}{('  — ' + d) if d and not g else ''}")
    print("결과: 모든 사례가 기대와 일치" if ok else "결과: 기대와 다른 사례가 있다")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
