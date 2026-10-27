"""오프라인 모델 사용량 수집기.

AGY transcript, Codex session, 위임 도구 결과(.jobs)에서 사용량을 수집하여
Core 하네스(usage.py)의 스키마에 맞춰 기록한다.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
from typing import Any

# 이 파일이 든 저장소의 harness 를 쓴다 → 그 저장소 reports/events.jsonl 에 기록 (WO-H40: 하네스 저장소의 사본을 다른 저장소에서 실행하면 하네스 쪽에 기록되던 문제)
ROOT = Path(__file__).resolve().parents[2]
HARNESS_DIR = ROOT / "harness"
if str(HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(HARNESS_DIR))

import usage  # noqa: E402


def validate_execution_contract(
    root: Path,
    execution_id: str,
    *,
    wo: str | None = None,
    role: str | None = None,
    agent: str | None = None,
    workspace: Path | str | None = None,
) -> dict[str, Any]:
    """등록된 수동 실행 계약의 유효성과 바인딩을 검증한다 (WO-H69)."""
    if not execution_id or not isinstance(execution_id, str) or not execution_id.strip():
        raise ValueError("실행 ID가 비어있습니다.")

    if "/" in execution_id or "\\" in execution_id or ".." in execution_id:
        raise ValueError(f"올바르지 않은 실행 ID입니다: {execution_id}")

    executions_dir = (root / "reports" / "executions").resolve()
    contract_file = (executions_dir / execution_id / "contract.json").resolve()

    try:
        contract_file.relative_to(executions_dir)
    except ValueError:
        raise ValueError(f"실행 ID 경로 탈출 감지: {execution_id}")

    if not contract_file.is_file():
        raise ValueError(f"등록된 실행 계약을 찾을 수 없습니다: {execution_id}")

    try:
        contract = json.loads(contract_file.read_text(encoding="utf-8"))
    except Exception as e:
        raise ValueError(f"실행 계약 JSON 읽기 실패: {e}")

    if not isinstance(contract, dict):
        raise ValueError("실행 계약 형식이 올바르지 않습니다.")

    if contract.get("run_id") != execution_id:
        raise ValueError(f"실행 ID 불일치: 계약({contract.get('run_id')}) != 요청({execution_id})")

    if contract.get("mode") != "manual":
        raise ValueError(f"수동 실행 모드가 아닙니다: {contract.get('mode')}")

    if wo is not None and contract.get("wo") != wo:
        raise ValueError(f"WO 불일치: 계약({contract.get('wo')}) != 요청({wo})")

    if role is not None and contract.get("role") != role:
        raise ValueError(f"역할 불일치: 계약({contract.get('role')}) != 요청({role})")

    if agent is not None and contract.get("agent") != agent:
        raise ValueError(f"에이전트 불일치: 계약({contract.get('agent')}) != 요청({agent})")

    if workspace is not None:
        c_ws = contract.get("workspace")
        if not c_ws:
            raise ValueError("계약에 작업 공간이 정의되지 않았습니다.")
        if Path(c_ws).resolve() != Path(workspace).resolve():
            raise ValueError(f"작업 공간 불일치: 계약({Path(c_ws).resolve()}) != 요청({Path(workspace).resolve()})")

    return contract


class UsageArgumentParser(argparse.ArgumentParser):
    """인자 파싱 오류 시 종료 코드 2로 종료하는 인자 파서."""

    def error(self, message: str) -> None:
        print(f"인자 오류: {message}", file=sys.stderr)
        sys.exit(2)


def parse_iso(ts_str: str) -> datetime:
    """ISO 8601 시각 문자열을 datetime 객체로 변환한다."""
    return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))


def parse_agy(path: Path) -> dict[str, Any]:
    """AGY transcript.jsonl 파일을 파싱한다."""
    if not path.is_file():
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {path}")

    resolved = path.resolve()
    if len(resolved.parents) < 3:
        raise ValueError("대화 폴더 상위 경로가 올바르지 않습니다.")
    run_id = resolved.parents[2].name
    if not run_id:
        raise ValueError("대화 폴더 이름을 찾을 수 없습니다.")

    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as f:
        for line in f:
            line_str = line.strip()
            if not line_str:
                continue
            rows.append(json.loads(line_str))

    if not rows:
        raise ValueError("로그가 비어있습니다.")

    total_input = 0
    total_output = 0
    total_cache_read = 0
    model_calls = 0

    for step in rows:
        has_tokens = any(
            step.get(k) is not None
            for k in ("input_tokens", "output_tokens", "cache_read_tokens")
        )
        if has_tokens:
            model_calls += 1
            total_input += int(step.get("input_tokens") or 0)
            total_output += int(step.get("output_tokens") or 0)
            total_cache_read += int(step.get("cache_read_tokens") or 0)

    tokens = None
    if model_calls > 0:
        tokens = {
            "input": total_input,
            "output": total_output,
            "cache_read": total_cache_read,
            "cache_in_input": False,
        }

    timestamps = [step["created_at"] for step in rows if "created_at" in step]
    started = timestamps[0] if timestamps else None
    ended = timestamps[-1] if timestamps else None
    seconds = None
    if started and ended:
        try:
            t0 = parse_iso(started)
            t1 = parse_iso(ended)
            seconds = round((t1 - t0).total_seconds(), 1)
        except Exception:
            seconds = None

    return {
        "agent": "agy",
        "run_id": run_id,
        "tokens": tokens,
        "model_calls": model_calls if model_calls > 0 else None,
        "started": started,
        "ended": ended,
        "seconds": seconds,
        "time_scope": "transcript_span",
    }


def parse_codex(path: Path) -> dict[str, Any]:
    """Codex rollout.jsonl 세션 파일을 파싱한다."""
    if not path.is_file():
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {path}")

    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as f:
        for line in f:
            line_str = line.strip()
            if not line_str:
                continue
            rows.append(json.loads(line_str))

    if not rows:
        raise ValueError("로그가 비어있습니다.")

    run_id = None
    model = None
    permission = None
    sandbox_type = None
    last_token_usage = None
    token_call_count = 0
    timestamps: list[str] = []

    for row in rows:
        if not isinstance(row, dict):
            continue
        ts_val = row.get("timestamp")
        if ts_val:
            timestamps.append(ts_val)

        row_type = row.get("type")
        payload = row.get("payload") or {}

        if row_type == "session_meta":
            if "id" in payload:
                run_id = payload["id"]

        elif row_type == "turn_context":
            if "model" in payload:
                model = payload["model"]
            sandbox_policy = payload.get("sandbox_policy")
            if isinstance(sandbox_policy, dict):
                sandbox_type = sandbox_policy.get("type", "")
            elif isinstance(sandbox_policy, str):
                sandbox_type = sandbox_policy

            approval_policy = payload.get("approval_policy", "")
            if approval_policy:
                permission = f"{sandbox_type}:{approval_policy}"
            else:
                permission = sandbox_type

        is_token_count = (row_type == "event_msg" and payload.get("type") == "token_count") or (row_type == "token_count")
        if is_token_count:
            token_call_count += 1
            info = payload.get("info", {}) if payload else row.get("info", {})
            if "total_token_usage" in info:
                last_token_usage = info["total_token_usage"]

    if not run_id:
        raise ValueError("session_meta.payload.id 를 찾을 수 없습니다.")

    tokens = None
    if last_token_usage:
        tokens = {
            "input": int(last_token_usage.get("input_tokens") or 0),
            "output": int(last_token_usage.get("output_tokens") or 0),
            "cache_read": int(last_token_usage.get("cached_input_tokens") or 0),
            "cache_in_input": True,
        }

    # 샌드박스가 danger-full-access 인 경우 경고 출력
    if sandbox_type == "danger-full-access":
        print("경고: 샌드박스 정책이 danger-full-access 로 설정되어 있습니다.")

    started = timestamps[0] if timestamps else None
    ended = timestamps[-1] if timestamps else None
    seconds = None
    if started and ended:
        try:
            t0 = parse_iso(started)
            t1 = parse_iso(ended)
            seconds = round((t1 - t0).total_seconds(), 1)
        except Exception:
            seconds = None

    return {
        "agent": "codex",
        "run_id": run_id,
        "model": model,
        "tokens": tokens,
        "permission": permission,
        "model_calls": token_call_count if token_call_count > 0 else None,
        "started": started,
        "ended": ended,
        "seconds": seconds,
        "time_scope": "transcript_span",
    }


def parse_delegation(job_dir: Path) -> dict[str, Any]:
    """위임 작업 폴더(.jobs/<job>)의 result.json 및 meta.json을 파싱한다."""
    if not job_dir.is_dir():
        raise FileNotFoundError(f"폴더를 찾을 수 없습니다: {job_dir}")

    result_file = job_dir / "result.json"
    meta_file = job_dir / "meta.json"
    if not result_file.is_file() or not meta_file.is_file():
        raise FileNotFoundError("result.json 또는 meta.json 파일이 없습니다.")

    with result_file.open("r", encoding="utf-8-sig") as f:
        result_data = json.load(f)
    with meta_file.open("r", encoding="utf-8-sig") as f:
        meta_data = json.load(f)

    agent = result_data.get("agent")
    run_id = result_data.get("conversation_id")
    if not agent or not run_id:
        raise ValueError("agent 또는 conversation_id가 누락되었습니다.")

    model = meta_data.get("model_reported") or meta_data.get("model")

    usage_data = result_data.get("usage", {})
    tokens = None
    if usage_data:
        tokens = {
            "input": int(usage_data.get("input_tokens") or 0),
            "output": int(usage_data.get("output_tokens") or 0),
            "cache_read": int(usage_data.get("cache_read_tokens") or 0),
            "cache_in_input": False,
        }

    seconds = result_data.get("duration_seconds")
    if seconds is not None:
        try:
            seconds = float(seconds)
        except (ValueError, TypeError):
            seconds = None

    model_calls = result_data.get("num_turns")
    if model_calls is not None:
        try:
            model_calls = int(model_calls)
        except (ValueError, TypeError):
            model_calls = None

    return {
        "agent": agent,
        "run_id": run_id,
        "model": model,
        "tokens": tokens,
        "model_calls": model_calls,
        "seconds": seconds,
        "time_scope": "delegation_total",
    }


def main() -> int:
    """CLI 인자를 파싱하고 사용량 이벤트를 기록한다."""
    parser = UsageArgumentParser(description="오프라인 모델 사용량 수집기")
    parser.add_argument("--agy-transcript", default=None, help="AGY 대화 로그 경로 (transcript.jsonl)")
    parser.add_argument("--agy-find", default=None, help="AGY 대화 내용 검색 문자열")
    parser.add_argument("--agy-root", default=None, help="AGY brain 폴더 경로 (기본값: ~/.gemini/antigravity-cli/brain)")
    parser.add_argument("--codex-session", default=None, help="Codex 세션 로그 경로 (rollout.jsonl)")
    parser.add_argument("--delegation-job", default=None, help="위임 작업 폴더 (.jobs/<job>)")
    parser.add_argument("--wo", default=None, help="작업 지시서 ID (필수)")
    parser.add_argument("--role", default=None, help="역할 (필수)")
    parser.add_argument("--task", default=None, help="작업 태스크")
    parser.add_argument("--round", type=int, default=None, help="회차")
    parser.add_argument("--tier", default=None, help="티어")
    parser.add_argument("--risk", default=None, help="위험도")
    parser.add_argument("--exec-path", default=None, help="실행 경로")
    parser.add_argument("--model", default=None, help="모델 이름")
    parser.add_argument("--cap-kind", default=None, help="상한 종류 (time, rounds, budget)")
    parser.add_argument("--cap-value", default=None, help="상한 값")
    parser.add_argument("--cap-unit", default=None, help="상한 단위")
    parser.add_argument("--stop-reason", default=None, help="중단 이유")
    parser.add_argument("--stop-by", default=None, help="중단 주체")
    parser.add_argument("--execution-id", default=None, help="실행 계약 ID")

    args = parser.parse_args()

    # 필수 인자 검증
    if not args.wo or not args.wo.strip():
        print("오류: --wo 인자는 필수입니다.", file=sys.stderr)
        return 2
    if not args.role or not args.role.strip():
        print("오류: --role 인자는 필수입니다.", file=sys.stderr)
        return 2

    # --agy-root 단독 사용 검증 (WO-H51)
    if args.agy_root is not None and args.agy_find is None:
        print("오류: --agy-root 옵션은 --agy-find 와 함께 사용해야 합니다.", file=sys.stderr)
        return 2

    # 출처 인자는 정확히 하나만 지정되어야 함
    sources = [
        args.agy_transcript is not None,
        args.agy_find is not None,
        args.codex_session is not None,
        args.delegation_job is not None,
    ]
    if sum(sources) != 1:
        print("오류: 출처 인자(--agy-transcript, --agy-find, --codex-session, --delegation-job) 중 정확히 하나만 지정해야 합니다.", file=sys.stderr)
        return 2

    # WO-H69: 실행 계약 바인딩 검증 (로그 파싱 및 기록 전)
    contract = None
    if args.execution_id:
        try:
            contract = validate_execution_contract(
                ROOT,
                args.execution_id,
                wo=args.wo,
                role=args.role,
            )
        except ValueError as e:
            print(f"인자 오류: {e}", file=sys.stderr)
            return 2

    # 로그 파일 파싱
    try:
        if args.agy_transcript:
            extracted = parse_agy(Path(args.agy_transcript))
        elif args.agy_find:
            agy_root_path = Path(args.agy_root).expanduser() if args.agy_root else Path("~/.gemini/antigravity-cli/brain").expanduser()
            transcript_files = []
            if agy_root_path.is_dir():
                for d in sorted(agy_root_path.iterdir()):
                    if d.is_dir():
                        tf = d / ".system_generated" / "logs" / "transcript.jsonl"
                        if tf.is_file():
                            transcript_files.append(tf)
            matches = []
            for tf in transcript_files:
                try:
                    content = tf.read_text(encoding="utf-8", errors="replace")
                    if args.agy_find in content:
                        matches.append(tf)
                except Exception:
                    pass
            if len(matches) == 0:
                print(f"일치하는 대화가 없습니다: '{args.agy_find}' (위치: {agy_root_path})", file=sys.stderr)
                return 2
            elif len(matches) > 1:
                print(f"일치하는 대화가 2개 이상입니다 ({len(matches)}개): '{args.agy_find}'", file=sys.stderr)
                for m in matches:
                    print(f"  후보: {m}", file=sys.stderr)
                return 2
            extracted = parse_agy(matches[0])
        elif args.codex_session:
            extracted = parse_codex(Path(args.codex_session))
        else:
            extracted = parse_delegation(Path(args.delegation_job))
    except Exception as e:
        print(f"로그 파싱 오류: {e}", file=sys.stderr)
        return 2

    agent = extracted["agent"]
    run_id = extracted["run_id"]

    # WO-H69: 파싱된 실제 에이전트와 계약 에이전트 일치 검증
    if args.execution_id and contract:
        if agent != contract.get("agent"):
            print(
                f"인자 오류: 에이전트 불일치 (계약={contract.get('agent')}, 실제={agent})",
                file=sys.stderr,
            )
            return 2
    model = args.model if args.model is not None else extracted.get("model")
    tokens = extracted.get("tokens")
    model_calls = extracted.get("model_calls")
    started = extracted.get("started")
    ended = extracted.get("ended")
    seconds = extracted.get("seconds")
    time_scope = extracted.get("time_scope")

    # 상한(cap) 구성
    cap = None
    if args.cap_kind is not None or args.cap_value is not None or args.cap_unit is not None:
        cap_val = args.cap_value
        if cap_val is not None:
            try:
                v = float(cap_val)
                cap_val = int(v) if v.is_integer() else v
            except (ValueError, TypeError):
                print("오류: --cap-value 는 숫자여야 합니다.", file=sys.stderr)
                return 2
        cap = {
            "kind": args.cap_kind,
            "value": cap_val,
            "unit": args.cap_unit,
        }

    # 중단(stop) 구성
    stop = None
    if args.stop_reason is not None or args.stop_by is not None:
        stop = {
            "reason": args.stop_reason,
            "by": args.stop_by,
        }

    # 조건(condition: exec_path, permission) 구성
    permission = extracted.get("permission")
    condition = {
        "exec_path": args.exec_path,
        "permission": permission,
    }

    record_fields: dict[str, Any] = {
        "wo": args.wo,
        "role": args.role,
        "agent": agent,
        "run_id": run_id,
        "task": args.task,
        "round": args.round,
        "tier": args.tier,
        "risk": args.risk,
        "model": model,
        "tokens": tokens,
        "model_calls": model_calls,
        "started": started,
        "ended": ended,
        "seconds": seconds,
        "time_scope": time_scope,
        "cap": cap,
        "stop": stop,
        "condition": condition,
    }
    if args.execution_id:
        record_fields["execution_id"] = args.execution_id

    # usage.record 호출을 통해 이벤트 기록
    try:
        result = usage.record(**record_fields)
    except ValueError as e:
        print(f"기록 검증 오류: {e}", file=sys.stderr)
        return 2

    if result is None:
        print("중복: 이미 기록된 실행입니다.")
        return 0

    print(f"기록 완료: agent={result.get('agent')} run_id={result.get('run_id')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
