"""이벤트 기록: KPI 의 원천 데이터.

사람의 기억이나 자기 보고가 아니라, 하네스 스크립트가 실행될 때 자동으로 남긴다.
reports/events.jsonl 에 한 줄씩 추가만 한다 (수정 · 삭제하지 않는다).

자동 기록: gate_run (run_gates.py), test_lock (check_protected_paths.py --write-lock)
수동 보조: mark.py 로 남기는 사람 이벤트 (없어도 KPI 의 핵심 지표는 계산된다)
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVENTS = ROOT / "reports" / "events.jsonl"


def _git(*args: str) -> str:
    p = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 else ""


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log_event(kind: str, **fields: object) -> dict:
    event = {
        "ts": now_utc(),
        "kind": kind,
        "head": _git("rev-parse", "--short", "HEAD"),
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        **fields,
    }
    EVENTS.parent.mkdir(exist_ok=True)
    with EVENTS.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
    return event


def read_events() -> list[dict]:
    if not EVENTS.exists():
        return []
    return [json.loads(line) for line in EVENTS.read_text(encoding="utf-8").splitlines() if line.strip()]
