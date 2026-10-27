"""하네스 자기 검사: 감시 도구 상한 기록의 회차 (WO-H50, 독립 일감 F2).

배경: 감시 도구를 --round 없이 띄우면 상한 중지 기록(model_usage)의 round 가 비어(WO-H45 AGY 2회차), 회차별 집계에서 빠진다.

계약 (adapters/tools/orca_watch.py)
- --round 를 주면 지금처럼 그 값.
- 없으면 상한 기록 직전에 자기 저장소 reports/events.jsonl 의 model_usage 중 wo · role · agent 가 같은 기록의 서로 다른 run_id
  수 + 1 을 회차로 쓰고, 로그에 “회차 추정 N” 한 줄을 남긴다. 다른 WO · 역할 · 에이전트 기록은 세지 않는다.
- 그 밖의 동작(중단 신호 · 종료 코드 · 로그 형식)은 그대로.

사용: python harness/selftest/check_watch_round_cases.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_orca_watch_cases import FAKE_ORCA, H, PY, SCREEN, TOOL, agy_tr, make_project, make_workspace  # noqa: E402


def prior(proj: Path, rows: list[tuple[str, str, str, str]]) -> None:
    lines = [json.dumps({"ts": "2026-10-20T00:00:00Z", "kind": "model_usage", "wo": wo, "role": role, "agent": agent,
                         "run_id": run, "round": None}) for wo, role, agent, run in rows]
    (proj / "reports" / "events.jsonl").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def last_usage(proj: Path) -> dict:
    ev = [json.loads(l) for l in (proj / "reports" / "events.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    return [e for e in ev if e.get("kind") == "model_usage"][-1]


def main() -> int:
    rows: list[tuple[str, bool, str]] = []

    def case(name: str, good: bool, detail: str = "") -> None:
        rows.append((name, bool(good), detail.replace("\n", " | ")[:260]))

    base = Path(tempfile.mkdtemp(prefix="watch-round-"))
    try:
        fake = base / "fake_orca.py"
        fake.write_text(FAKE_ORCA, encoding="utf-8")

        def capped(name: str, prior_rows: list, *extra: str) -> tuple[dict, str]:
            d = base / name
            d.mkdir()
            proj = make_project(d)
            prior(proj, prior_rows)
            root = d / "brain"
            agy_tr(root, f"conv-{name}", datetime.now(timezone.utc) - timedelta(seconds=30), str(d / f"wo-x9-{name}"))
            ws = make_workspace(d, f"wo-x9-{name}", 1)
            st = d / "state"
            st.mkdir()
            (st / "screen.txt").write_text(SCREEN, encoding="utf-8")
            args = [PY, str(proj / TOOL), "--terminal", H, "--workspace", str(ws), "--agent", "agy", "--wo", "WO-X9",
                    "--role", "implementer", "--orca", str(fake), "--log", str(st / "watch.log"), "--transcript-root",
                    str(root), "--cap-seconds", "2", "--interval", "1", "--checkpoints", "1,2", *extra]
            subprocess.run(args, cwd=proj, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
                           env={**os.environ, "FAKE_ORCA_STATE": str(st), "PYTHONIOENCODING": "utf-8"})
            log = (st / "watch.log").read_text(encoding="utf-8") if (st / "watch.log").exists() else ""
            try:
                return last_usage(proj), log
            except Exception:
                return {}, log

        e, lg = capped("r1", [("WO-X9", "implementer", "agy", "a"), ("WO-X9", "implementer", "agy", "b")])
        case("R01 --round 없음 · 같은 WO/역할/에이전트 기록 2개 → 상한 기록 round 3, 로그 “회차 추정 3”",
             e.get("round") == 3 and "회차 추정 3" in lg, f"{e.get('round')} {lg[-200:]}")
        e, lg = capped("r2", [("WO-X9", "implementer", "agy", "a")], "--round", "5")
        case("R02 --round 5 → round 5, “회차 추정” 없음", e.get("round") == 5 and "회차 추정" not in lg, f"{e.get('round')}")
        e, lg = capped("r3", [])
        case("R03 기록 없음 → round 1", e.get("round") == 1, f"{e.get('round')}")
        e, lg = capped("r4", [("WO-X9", "adversary", "agy", "a"), ("WO-X9", "implementer", "codex", "b"),
                              ("WO-Y1", "implementer", "agy", "c")])
        case("R04 다른 역할 · 에이전트 · WO 기록은 세지 않음 → round 1", e.get("round") == 1, f"{e.get('round')}")
        e, lg = capped("r5", [("WO-X9", "implementer", "agy", "a"), ("WO-X9", "implementer", "agy", "a")])
        case("R05 같은 run_id 중복은 한 번 → round 2", e.get("round") == 2, f"{e.get('round')}")
    finally:
        shutil.rmtree(base, ignore_errors=True)

    ok = all(g for _, g, _ in rows)
    for n, g, d in rows:
        print(f"{'OK ' if g else 'BAD'} {n}{('  — ' + d) if d and not g else ''}")
    print("결과: 모든 사례가 기대와 일치" if ok else "결과: 기대와 다른 사례가 있다")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
