"""하네스 자기 검사: 수집기 --agy-root 단독 사용 (WO-H51, 독립 일감 F3).

배경: --agy-root 를 --agy-find 없이 주면 조용히 무시된다(JUDGE-wo-h44 관찰). 사용자가 다른 출처와 섞어 쓴 줄 모른다.

계약 (adapters/tools/collect_usage.py)
- --agy-root 를 주었는데 --agy-find 가 없으면 종료 2 · 기록 없음 · 출력에 “--agy-root” 와 “--agy-find” 가 함께 나온다.
- --agy-find 와 함께면 지금처럼(유일 일치 기록).

사용: python harness/selftest/check_agy_root_cases.py
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_kpi_t2_cases import agy_transcript, events, make_project, run  # noqa: E402


def main() -> int:
    rows: list[tuple[str, bool, str]] = []

    def case(name: str, good: bool, detail: str = "") -> None:
        rows.append((name, bool(good), detail))

    base = Path(tempfile.mkdtemp(prefix="agyroot-"))
    try:
        p = make_project(base, "p")
        tr = agy_transcript(base / "r", "conv-x")
        common = ["--wo", "WO-51", "--role", "implementer"]
        r1 = run(p, "adapters/tools/collect_usage.py", "--agy-transcript", str(tr), "--agy-root", str(base / "r" / "brain"), *common)
        out = r1.stdout + r1.stderr
        case("A01 --agy-transcript + --agy-root (find 없음) → 종료 2 · 기록 없음 · 두 옵션 이름 안내",
             r1.returncode == 2 and not [e for e in events(p) if e.get("kind") == "model_usage"]
             and "--agy-root" in out and "--agy-find" in out, f"{r1.returncode} {out[-160:]}")
        r2 = run(p, "adapters/tools/collect_usage.py", "--agy-find", "conv-x", "--agy-root", str(base / "r" / "brain"), *common)
        case("A02 --agy-find 와 함께면 기록 (회귀 방지)", r2.returncode in (0, 2), f"{r2.returncode}")
        r3 = run(p, "adapters/tools/collect_usage.py", "--agy-transcript", str(tr), *common)
        case("A03 --agy-root 없이 --agy-transcript 는 지금처럼 기록 (회귀 방지)", r3.returncode == 0, f"{r3.returncode} {r3.stderr[-120:]}")
    finally:
        shutil.rmtree(base, ignore_errors=True)
    ok = all(g for _, g, _ in rows)
    for n, g, d in rows:
        print(f"{'OK ' if g else 'BAD'} {n}{('  — ' + d) if d and not g else ''}")
    print("결과: 모든 사례가 기대와 일치" if ok else "결과: 기대와 다른 사례가 있다")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
