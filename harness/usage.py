"""모델 사용량 이벤트 기록 스키마 (Core).

에이전트나 공급자의 고유 로그 형식은 이 모듈 밖에서 정규화하고,
Core 는 정규화된 model_usage 이벤트의 유효성을 검증해 reports/events.jsonl 에 기록한다.
"""

from __future__ import annotations

from typing import Any
from events import log_event, read_events

ALLOWED_CAP_KINDS = {"time", "rounds", "budget"}


def record(**fields: Any) -> dict[str, Any] | None:
    """model_usage 이벤트를 검증해 기록한다.

    필수: wo, role, agent, run_id (비어있지 않은 문자열).
    unknown 값(tokens, model, seconds, model_calls 등)은 None 허용.
    cap 은 None 또는 {kind: time|rounds|budget, value, unit}.
    stop 은 None 또는 {reason: 비어있지 않은 문자열, by}.
    stop.reason == "cap" 이면 cap 필수.
    유효하지 않은 입력은 ValueError 를 발생시키고 아무것도 기록하지 않는다.
    같은 (agent, run_id) 의 이벤트가 이미 있으면 None 을 반환하고 기록하지 않는다.
    """
    for req in ("wo", "role", "agent", "run_id"):
        val = fields.get(req)
        if val is None or not isinstance(val, str) or not val.strip():
            raise ValueError(f"필수 필드가 누락되었거나 비어있습니다: {req}")

    agent = fields["agent"]
    run_id = fields["run_id"]

    cap = fields.get("cap")
    if cap is not None:
        if not isinstance(cap, dict):
            raise ValueError("cap은 dict여야 합니다")
        if cap.get("kind") not in ALLOWED_CAP_KINDS:
            raise ValueError(f"알 수 없는 cap 종류입니다: {cap.get('kind')}")
        if "value" not in cap or cap["value"] is None:
            raise ValueError("cap value가 누락되었습니다")
        if not isinstance(cap["value"], (int, float)) or cap["value"] < 0:
            raise ValueError("cap value는 0 이상의 숫자여야 합니다")
        if "unit" not in cap or not isinstance(cap.get("unit"), str) or not cap["unit"].strip():
            raise ValueError("cap unit은 비어있지 않은 문자열이어야 합니다")

    stop = fields.get("stop")
    if stop is not None:
        if not isinstance(stop, dict):
            raise ValueError("stop은 dict여야 합니다")
        reason = stop.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("stop reason은 비어있지 않은 문자열이어야 합니다")
        if reason == "cap" and not cap:
            raise ValueError("stop.reason이 'cap'인 경우 cap이 필수입니다")

    tokens = fields.get("tokens")
    if tokens is not None and not isinstance(tokens, dict):
        raise ValueError("tokens는 dict여야 합니다")

    seconds = fields.get("seconds")
    if seconds is not None and (not isinstance(seconds, (int, float)) or seconds < 0):
        raise ValueError("seconds는 0 이상의 숫자여야 합니다")

    model_calls = fields.get("model_calls")
    if model_calls is not None and (not isinstance(model_calls, int) or model_calls < 0):
        raise ValueError("model_calls는 0 이상의 정수여야 합니다")

    round_val = fields.get("round")
    if round_val is not None and (not isinstance(round_val, int) or round_val < 0):
        raise ValueError("round는 0 이상의 정수여야 합니다")

    existing = read_events()
    for ev in existing:
        if ev.get("kind") == "model_usage" and ev.get("agent") == agent and ev.get("run_id") == run_id:
            return None

    payload = dict(fields)
    payload.pop("kind", None)
    return log_event("model_usage", **payload)
