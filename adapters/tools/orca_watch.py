"""Orca 워커 감시 도구 (WO-H43, WO-H73, WO-H78).

지정된 간격으로 워커 화면과 작업 공간 변경 상태를 감시하고 중간 확인을 기록하며,
상한 도달 시 워커를 자동 중단하고 사용량을 기록한다.
--save-final 옵션으로 모든 종료 경로에서 최종 응답 및 작업 사본 변경 상태를 저장한다.

사용법:
  python adapters/tools/orca_watch.py --terminal <핸들> --workspace <작업공간> \\
    --agent agy|codex|claude --wo <WO> --role <역할> --cap-seconds <초> [옵션]
"""

from __future__ import annotations

import argparse
import builtins
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[2]

# 같은 디렉터리 또는 상류 도구 경로에서 collect_usage 모듈 가져오기
tools_dir = str(Path(__file__).resolve().parent)
if tools_dir not in sys.path:
    sys.path.insert(0, tools_dir)

from collect_usage import parse_agy, parse_codex, validate_execution_contract

GUIDE_SUBSTRINGS = (
    "esc to cancel",
    "for shortcuts",
    "Accept-edits mode",
    "accept-edits ·",
)

PROMPT_CHARS = (">", "›", "❯")


class WatchArgumentParser(argparse.ArgumentParser):
    """인자 파싱 오류 시 종료 코드 2로 종료하는 인자 파서."""

    def error(self, message: str) -> None:
        print(f"인자 오류: {message}", file=sys.stderr)
        sys.exit(2)


def is_separator_line(line: str) -> bool:
    """구분선(─ 등 상자 문자와 공백만)인지 확인한다."""
    s = line.strip()
    if not s:
        return False
    for c in s:
        if c in " \t-=_~+|*":
            continue
        code = ord(c)
        if 0x2500 <= code <= 0x257F:
            continue
        return False
    return True


def is_tip_line(line: str) -> bool:
    """앞의 상자 문자(└ ├ │ 등)와 공백을 지운 뒤 Tip: 으로 시작하는 줄인지 확인한다."""
    i = 0
    while i < len(line):
        c = line[i]
        if c in " \t" or 0x2500 <= ord(c) <= 0x257F:
            i += 1
        else:
            break
    return line[i:].startswith("Tip:")


def is_codex_placeholder(line: str) -> bool:
    """입력 표시 문자 뒤 Codex 자리 문구(`› Ask Codex to do anything` 처럼 Ask Codex 로 시작)인지 확인한다."""
    s = line.lstrip(" \t>›❯").strip()
    return s.startswith("Ask Codex")


def extract_screen_line(screen_text: str) -> str:
    """화면 텍스트에서 비어있지 않은 유효한 마지막 줄을 최대 120자까지 추출한다."""
    lines = screen_text.splitlines()
    for raw in reversed(lines):
        line = raw.strip()
        if not line:
            continue
        if any(sub in raw for sub in GUIDE_SUBSTRINGS):
            continue
        if is_separator_line(raw):
            continue
        if line in PROMPT_CHARS:
            continue
        if is_codex_placeholder(line):
            continue
        if is_tip_line(line):
            continue
        return line[:120]
    return ""


def get_dirty_count(workspace: Path) -> int:
    """작업 공간의 git status --porcelain 줄 수를 반환한다."""
    try:
        res = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=workspace,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if res.returncode == 0:
            lines = [l for l in res.stdout.splitlines() if l.strip()]
            return len(lines)
    except Exception:
        pass
    return 0


def porcelain_lines(workspace: Path) -> list[str] | None:
    """작업 공간의 git status --porcelain --ignored 결과 비어 있지 않은 줄 목록을 반환한다.
    종료 코드가 0이 아니거나 예외 발생 시 None을 반환한다.
    """
    try:
        res = subprocess.run(
            ["git", "status", "--porcelain", "--ignored"],
            cwd=workspace,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if res.returncode == 0:
            return [l for l in res.stdout.splitlines() if l.strip()]
        return None
    except Exception:
        return None


def final_response(agent: str, log_path: Path | None) -> tuple[str | None, bool]:
    """작업자 로그에서 최종 응답 본문과 잘림 여부를 추출한다.
    예외 발생 시 또는 응답이 없으면 (None, False)를 반환한다.
    """
    if log_path is None or not isinstance(log_path, Path):
        return None, False

    try:
        if agent == "agy":
            full_path = log_path.parent / "transcript_full.jsonl"
            from_full = False
            read_path = log_path
            if full_path.is_file():
                read_path = full_path
                from_full = True
            elif not log_path.is_file():
                return None, False

            last_content: str | None = None
            is_truncated = False

            with read_path.open("r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    s = line.strip()
                    if not s:
                        continue
                    try:
                        row = json.loads(s)
                    except Exception:
                        continue
                    if not isinstance(row, dict):
                        continue
                    if row.get("source") != "MODEL" or row.get("type") != "PLANNER_RESPONSE":
                        continue
                    content = row.get("content")
                    if not isinstance(content, str) or not content.strip():
                        continue
                    tool_calls = row.get("tool_calls")
                    if tool_calls:
                        continue

                    last_content = content
                    if not from_full and "content" in str(row.get("truncated_fields") or ""):
                        is_truncated = True
                    else:
                        is_truncated = False

            if last_content is not None:
                return last_content, is_truncated
            return None, False

        elif agent == "codex":
            if not log_path.is_file():
                return None, False

            last_content = None
            with log_path.open("r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    s = line.strip()
                    if not s:
                        continue
                    try:
                        row = json.loads(s)
                    except Exception:
                        continue
                    if not isinstance(row, dict):
                        continue
                    if row.get("type") != "event_msg":
                        continue
                    payload = row.get("payload")
                    if not isinstance(payload, dict):
                        continue
                    if payload.get("type") != "task_complete":
                        continue
                    msg = payload.get("last_agent_message")
                    if isinstance(msg, str) and msg.strip():
                        last_content = msg

            if last_content is not None:
                return last_content, False
            return None, False

        else:
            return None, False

    except Exception:
        return None, False


def compute_checkpoints(cap: int, checkpoints_arg: str | None) -> list[int]:
    """중간 확인 시점 목록을 계산한다."""
    if checkpoints_arg:
        return [int(x.strip()) for x in checkpoints_arg.split(",") if x.strip()]
    if cap <= 600:
        return [builtins.round(cap * 0.3), builtins.round(cap * 0.6), cap]
    cps = list(range(300, cap, 300))
    if not cps or cps[-1] != cap:
        cps.append(cap)
    return cps


def format_elapsed(seconds: float) -> str:
    """경과 초를 [mm:ss] 형태의 문자열로 변환한다."""
    s = int(seconds)
    mm = s // 60
    ss = s % 60
    return f"{mm:02d}:{ss:02d}"


def get_default_orca() -> str:
    """기본 orca 실행 파일 경로를 반환한다.
    환경변수(ORCA_CLI_COMMAND, ORCA_CLI) 또는 .local-tools.json 설정이 있으면 이를 우선 반영한다.
    """
    candidate = os.environ.get("ORCA_CLI_COMMAND") or os.environ.get("ORCA_CLI")
    if candidate:
        return candidate
    local_tools_path = ROOT / ".local-tools.json"
    if local_tools_path.is_file():
        try:
            local_tools = json.loads(local_tools_path.read_text(encoding="utf-8"))
            candidate = local_tools.get("orca_cli")
            if candidate:
                return candidate
        except Exception:
            pass
    local_app = os.environ.get("LOCALAPPDATA")
    if local_app:
        return str(Path(local_app) / "Programs" / "orca" / "resources" / "bin" / "orca.exe")
    return "orca.exe"


def run_orca(orca_cmd: str, subargs: list[str]) -> subprocess.CompletedProcess[str]:
    """orca 명령을 실행한다."""
    if orca_cmd.lower().endswith(".py"):
        cmd = [sys.executable, orca_cmd, *subargs]
    else:
        cmd = [orca_cmd, *subargs]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def parse_iso(ts_str: str) -> datetime:
    """ISO 8601 시각 문자열을 UTC timezone-aware datetime으로 변환한다."""
    dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _norm_path(s: str) -> str:
    """경로 문자열을 정규화한다: 역슬래시 변환, 중복 슬래시 제거, 끝 슬래시 제거, 소문자화."""
    s = s.replace("\\", "/")
    s = re.sub(r"/+", "/", s)
    s = s.rstrip("/")
    return s.casefold()


def find_worker_log(
    agent: str,
    transcript_root: Path | None,
    workspace: Path,
    start_time_utc: datetime,
) -> Path | None:
    """작업자 로그 파일을 검색한다."""
    if transcript_root is None:
        user_home = Path(os.environ.get("USERPROFILE", str(Path.home())))
        if agent == "agy":
            transcript_root = user_home / ".gemini" / "antigravity-cli" / "brain"
        else:
            transcript_root = user_home / ".codex" / "sessions"

    if not transcript_root.exists():
        return None

    ws = _norm_path(str(workspace.resolve()))
    pat = re.compile(re.escape(ws) + r"(?=[/\"'\s,\]}]|$)")

    candidates: list[Path] = []
    if agent == "agy":
        candidates = list(transcript_root.glob("*/.system_generated/logs/transcript.jsonl"))
        if not candidates:
            candidates = list(transcript_root.rglob("transcript.jsonl"))
    else:
        candidates = list(transcript_root.rglob("rollout-*.jsonl"))

    valid_candidates: list[tuple[datetime, Path]] = []
    threshold = start_time_utc - timedelta(seconds=120)

    for cand in candidates:
        try:
            # 감시 시작 전 마지막으로 바뀐 로그는 읽지 않는다 (세션 폴더가 커져도 상한 처리가 느려지지 않게, 리드 보정)
            if datetime.fromtimestamp(cand.stat().st_mtime, timezone.utc) < threshold:
                continue

            first_row: dict[str, Any] | None = None
            first_dt: datetime | None = None
            matched = False

            with cand.open("r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    s = line.strip()
                    if not s:
                        continue
                    try:
                        row = json.loads(s)
                    except Exception:
                        continue
                    if not isinstance(row, dict):
                        continue

                    if first_row is None:
                        first_row = row

                        if agent == "agy":
                            if first_row.get("source") != "USER_EXPLICIT":
                                break
                        elif agent == "codex":
                            if first_row.get("type") != "session_meta":
                                break
                            payload = first_row.get("payload")
                            if not isinstance(payload, dict):
                                break
                            cwd = payload.get("cwd")
                            if not isinstance(cwd, str) or _norm_path(cwd) != ws:
                                break
                        else:
                            break

                        ts_str = first_row.get("created_at") or first_row.get("timestamp")
                        if not ts_str or not isinstance(ts_str, str):
                            break
                        try:
                            first_dt = parse_iso(ts_str)
                        except Exception:
                            break
                        if first_dt < threshold:
                            break

                        if agent == "codex":
                            matched = True
                            break

                    if agent == "agy" and row.get("source") == "MODEL" and row.get("tool_calls"):
                        try:
                            tc_str = _norm_path(json.dumps(row["tool_calls"], ensure_ascii=False))
                            if pat.search(tc_str):
                                matched = True
                                break
                        except Exception:
                            pass

            if matched and first_dt is not None:
                valid_candidates.append((first_dt, cand))

        except Exception:
            continue

    if not valid_candidates:
        return None

    valid_candidates.sort(
        key=lambda x: (abs((x[0] - start_time_utc).total_seconds()), str(x[1]))
    )
    return valid_candidates[0][1]


def estimate_round(root: Path, wo: str, role: str, agent: str) -> int:
    """reports/events.jsonl 의 model_usage 중 wo, role, agent 가 같은 기록의 서로 다른 run_id 수 + 1 을 계산한다."""
    events_file = root / "reports" / "events.jsonl"
    if not events_file.exists():
        return 1
    run_ids: set[str] = set()
    try:
        with events_file.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                s = line.strip()
                if not s:
                    continue
                try:
                    ev = json.loads(s)
                except Exception:
                    continue
                if not isinstance(ev, dict):
                    continue
                if ev.get("kind") != "model_usage":
                    continue
                if ev.get("wo") == wo and ev.get("role") == role and ev.get("agent") == agent:
                    run_id = ev.get("run_id")
                    if run_id is not None and str(run_id).strip():
                        run_ids.add(str(run_id))
    except Exception:
        pass
    return len(run_ids) + 1


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    if hasattr(sys.stderr, "reconfigure"):
        try:
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass

    start_time_utc = datetime.now(timezone.utc)
    start_iso = start_time_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    start_mono = time.monotonic()

    parser = WatchArgumentParser(description="Orca 워커 감시 도구")
    parser.add_argument("--terminal", default=None, help="Orca 터미널 핸들")
    parser.add_argument("--workspace", default=None, help="작업 공간 경로")
    parser.add_argument("--agent", default=None, help="에이전트 (agy, codex 또는 claude)")
    parser.add_argument("--wo", default=None, help="작업 지시서 ID")
    parser.add_argument("--role", default=None, help="역할")
    parser.add_argument("--cap-seconds", type=int, default=None, help="상한 초 (≥1)")
    parser.add_argument("--interval", type=int, default=60, help="감시 간격 초 (기본 60, ≥1)")
    parser.add_argument("--checkpoints", default=None, help="중간 확인 시점 (초,초,...)")
    parser.add_argument("--orca", default=None, help="orca 실행 파일 경로")
    parser.add_argument("--log", default=None, help="로그 파일 경로")
    parser.add_argument("--transcript-root", default=None, help="작업자 로그 루트 경로")
    parser.add_argument("--model", default=None, help="모델 이름")
    parser.add_argument("--tier", default=None, help="티어")
    parser.add_argument("--risk", default=None, help="위험도")
    parser.add_argument("--round", type=int, default=None, help="회차")
    parser.add_argument("--print-plan", action="store_true", help="중간 확인 시점 목록만 출력하고 종료")
    parser.add_argument("--execution-id", default=None, help="실행 계약 ID")
    parser.add_argument("--collect-on-exit", action="store_true", help="종료 시 사용량 수집 수행 (WO-H73)")
    parser.add_argument("--save-final", action="store_true", help="종료 시 최종 응답 저장 (WO-H78)")

    args = parser.parse_args()

    # 인자 검증
    if args.cap_seconds is None or args.cap_seconds < 1:
        parser.error("--cap-seconds 는 1 이상의 정수여야 합니다.")
    if args.interval is None or args.interval < 1:
        parser.error("--interval 은 1 이상의 정수여야 합니다.")
    if args.agent not in ("agy", "codex", "claude"):
        parser.error("--agent 는 agy, codex 또는 claude 여야 합니다.")

    if args.checkpoints:
        try:
            [int(x.strip()) for x in args.checkpoints.split(",") if x.strip()]
        except ValueError:
            parser.error("--checkpoints 는 쉼표로 구분된 정수여야 합니다.")

    checkpoints = compute_checkpoints(args.cap_seconds, args.checkpoints)

    if args.print_plan:
        print(",".join(str(c) for c in checkpoints))
        return 0

    for req in ("terminal", "workspace", "wo", "role"):
        if not getattr(args, req, None):
            parser.error(f"--{req} 인자는 필수입니다.")

    workspace = Path(args.workspace)
    orca_path = args.orca if args.orca else get_default_orca()

    # WO-H69: 실행 계약 바인딩 검증 (로그 파일 생성이나 Orca 호출 전에 수행)
    if args.execution_id:
        try:
            validate_execution_contract(
                ROOT,
                args.execution_id,
                wo=args.wo,
                role=args.role,
                agent=args.agent,
                workspace=workspace,
            )
        except ValueError as e:
            print(f"인자 오류: {e}", file=sys.stderr)
            sys.exit(2)

    if args.log:
        log_path = Path(args.log)
    else:
        term_prefix = args.terminal[:12] if args.terminal else "unknown"
        log_path = ROOT / "reports" / "watch" / f"{args.wo}-{term_prefix}.log"

    log_path.parent.mkdir(parents=True, exist_ok=True)

    def write_log(msg: str, elapsed_sec: float) -> None:
        line = f"[{format_elapsed(elapsed_sec)}] {msg}\n"
        with log_path.open("a", encoding="utf-8") as f:
            f.write(line)

    def emit_observation(outcome: str, provider_session_id: str | None = None) -> None:
        if not args.execution_id:
            return
        wall_seconds = max(0.0, builtins.round(time.monotonic() - start_mono, 3))
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        obs: dict[str, Any] = {
            "execution_id": args.execution_id,
            "wo": args.wo,
            "role": args.role,
            "workspace": str(workspace.resolve()),
            "terminal": args.terminal,
            "outcome": outcome,
            "started": start_iso,
            "ended": now_iso,
            "wall_seconds": wall_seconds,
            "time_scope": "watcher_wall",
            "time_source": "watcher_monotonic",
            "model_activity_seconds": None,
            "observed_model": None,
            "permission": None,
        }
        if provider_session_id is not None:
            obs["provider_session_id"] = provider_session_id
        try:
            harness_dir = str(ROOT / "harness")
            if harness_dir not in sys.path:
                sys.path.insert(0, harness_dir)
            import events
            events.log_event("execution_observation", **obs)
        except Exception as e:
            write_log(f"관측 기록 실패: {e}", time.monotonic() - start_mono)

    transcript_root = Path(args.transcript_root) if args.transcript_root else None

    def do_collect(is_cap: bool) -> str | None:
        """종료 시 사용량을 수집한다 (WO-H73)."""
        if args.agent == "claude":
            write_log("claude usage collection unsupported; skipping", time.monotonic() - start_mono)
            return None

        round_val = args.round
        if round_val is None:
            round_val = estimate_round(ROOT, args.wo, args.role, args.agent)
            write_log(f"회차 추정 {round_val}", time.monotonic() - start_mono)

        worker_log = find_worker_log(args.agent, transcript_root, workspace, start_time_utc)
        provider_session_id = None
        if worker_log:
            try:
                if args.agent == "agy":
                    provider_session_id = parse_agy(worker_log).get("run_id")
                else:
                    provider_session_id = parse_codex(worker_log).get("run_id")
            except Exception:
                pass

            collect_tool = Path(__file__).resolve().parent / "collect_usage.py"
            if not collect_tool.is_file():
                collect_tool = ROOT / "adapters" / "tools" / "collect_usage.py"

            source_arg = "--agy-transcript" if args.agent == "agy" else "--codex-session"
            cmd = [
                sys.executable,
                str(collect_tool),
                source_arg,
                str(worker_log),
                "--wo",
                args.wo,
                "--role",
                args.role,
                "--exec-path",
                "orca-worktree",
            ]
            if is_cap:
                cmd.extend([
                    "--cap-kind",
                    "time",
                    "--cap-value",
                    str(args.cap_seconds),
                    "--cap-unit",
                    "s",
                    "--stop-reason",
                    "cap",
                    "--stop-by",
                    "watcher",
                ])
            if args.model:
                cmd.extend(["--model", args.model])
            if args.tier:
                cmd.extend(["--tier", args.tier])
            if args.risk:
                cmd.extend(["--risk", args.risk])
            if round_val is not None:
                cmd.extend(["--round", str(round_val)])
            if args.execution_id:
                cmd.extend(["--execution-id", args.execution_id])

            col = subprocess.run(
                cmd,
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if col.returncode == 0:
                write_log(f"기록: {col.stdout.strip()[:160]} ({worker_log})", time.monotonic() - start_mono)
            else:
                write_log(f"기록 실패: {(col.stderr or col.stdout).strip()[:160]} ({worker_log})",
                          time.monotonic() - start_mono)
        else:
            if is_cap and not args.execution_id:
                harness_dir = str(ROOT / "harness")
                if harness_dir not in sys.path:
                    sys.path.insert(0, harness_dir)
                import usage

                record_kwargs: dict[str, Any] = {
                    "wo": args.wo,
                    "role": args.role,
                    "agent": args.agent,
                    "run_id": f"orca:{args.terminal}",
                    "tokens": None,
                    "model": args.model,
                    "tier": args.tier,
                    "risk": args.risk,
                    "round": round_val,
                    "cap": {"kind": "time", "value": args.cap_seconds, "unit": "s"},
                    "stop": {"reason": "cap", "by": "watcher"},
                    "condition": {"exec_path": "orca-worktree", "permission": None},
                }
                try:
                    res = usage.record(**record_kwargs)
                    note = "중복 — 기록 안 함" if res is None else f"run_id={record_kwargs['run_id']} (작업자 로그 없음, 토큰 미상)"
                    write_log(f"기록: {note}", time.monotonic() - start_mono)
                except Exception as e:
                    write_log(f"기록 실패: {e}", time.monotonic() - start_mono)
            else:
                write_log("작업자 로그 없음 — 사용량 기록 생략 (이후 수집 가능)", time.monotonic() - start_mono)

        return provider_session_id

    def save_final(outcome: str) -> None:
        # 유휴 경로는 바깥 try/except 안에서 불린다 — 여기서 예외가 새면 감시가 끝나지 않으므로 모두 여기서 막는다 (리드 보정)
        try:
            _save_final(outcome)
        except Exception as e:
            write_log(f"최종 응답 저장 실패: {e}", time.monotonic() - start_mono)

    def _save_final(outcome: str) -> None:
        final_path = log_path.with_name(log_path.stem + ".final.md")
        worker_log = (
            find_worker_log(args.agent, transcript_root, workspace, start_time_utc)
            if args.agent != "claude"
            else None
        )
        worker_log_str = str(worker_log) if worker_log is not None else "없음"

        lines = porcelain_lines(workspace)
        if lines is None:
            change_str = "확인 실패"
            change_items = ""
        else:
            change_str = str(len(lines))
            if lines:
                change_items = "\n" + "\n".join(f"  - {l}" for l in lines[:20])
            else:
                change_items = ""

        resp, is_truncated = final_response(args.agent, worker_log)
        if resp is not None and resp.strip():
            body_part = resp
            if is_truncated:
                body_part += "\n\n(잘림: transcript_full.jsonl 없음)"
        else:
            body_part = "(최종 응답 없음)"

        content = (
            f"# 최종 응답 · {args.wo} · {args.role} · {args.agent}\n"
            f"- 종료: {outcome}\n"
            f"- 작업자 로그: {worker_log_str}\n"
            f"- 작업 사본 변경(--ignored 포함): {change_str}{change_items}\n\n"
            f"---\n\n"
            f"{body_part}\n"
        )

        try:
            final_path.parent.mkdir(parents=True, exist_ok=True)
            # await-final 이 반쯤 쓴 파일을 읽지 않도록 임시 파일에 쓴 뒤 한 번에 바꾼다 (리드 보정)
            tmp_path = final_path.with_name(final_path.name + ".tmp")
            tmp_path.write_text(content, encoding="utf-8")
            os.replace(tmp_path, final_path)
            write_log(f"최종 응답 저장: {final_path} (변경 {change_str})", time.monotonic() - start_mono)
        except Exception as e:
            write_log(f"최종 응답 저장 실패: {e}", time.monotonic() - start_mono)

    n = len(checkpoints)
    logged_checkpoints: set[int] = set()

    while True:
        elapsed = time.monotonic() - start_mono

        # 1. terminal read
        read_res = run_orca(orca_path, ["terminal", "read", "--terminal", args.terminal])
        if read_res.returncode != 0:
            write_log("종료(터미널 없음)", elapsed)
            provider_session_id = None
            if args.collect_on_exit:
                provider_session_id = do_collect(is_cap=False)
            if args.save_final:
                save_final("terminal_unavailable")
            emit_observation("terminal_unavailable", provider_session_id=provider_session_id)
            return 0

        # 2. 화면 줄 추출
        screen_line = extract_screen_line(read_res.stdout)

        # 3. 작업 공간 변경 파일 수
        dirty_count = get_dirty_count(workspace)

        # 4. 콘솔 한 줄 출력
        print(f"[{format_elapsed(elapsed)}] 변경 파일 {dirty_count}개 · {screen_line}", flush=True)

        # 5. 끝남 감지: terminal wait --for tui-idle --timeout-ms 1000 --json
        #    시작 직후(첫 간격 전)의 유휴는 지시를 받기 전일 수 있어 종료로 보지 않는다 (리드 보정)
        wait_res = None
        if elapsed >= min(args.interval, args.cap_seconds):
            wait_res = run_orca(
                orca_path,
                ["terminal", "wait", "--terminal", args.terminal, "--for", "tui-idle", "--timeout-ms", "1000", "--json"],
            )
        if wait_res is not None and wait_res.returncode == 0:
            try:
                wait_json = json.loads(wait_res.stdout)
                if args.execution_id:
                    is_idle = (wait_json.get("ok") is True and
                               wait_json.get("result", {}).get("wait", {}).get("satisfied") is True)
                else:
                    is_idle = wait_json.get("ok") is True
                if is_idle:
                    write_log("종료(유휴)", time.monotonic() - start_mono)
                    provider_session_id = None
                    if args.collect_on_exit:
                        provider_session_id = do_collect(is_cap=False)
                    if args.save_final:
                        save_final("idle_only")
                    emit_observation("idle_only", provider_session_id=provider_session_id)
                    return 0
            except Exception:
                pass

        # 6. 지난 중간 확인 시점 기록
        current_elapsed = time.monotonic() - start_mono
        for idx, cp in enumerate(checkpoints, start=1):
            if idx not in logged_checkpoints and current_elapsed >= cp:
                write_log(f"중간 확인 {idx}/{n}: 변경 파일 {dirty_count}개 · {screen_line}", current_elapsed)
                logged_checkpoints.add(idx)

        # 7. 상한 도달 확인
        if current_elapsed >= args.cap_seconds:
            # terminal send --terminal H --interrupt 한 번
            send_res = run_orca(orca_path, ["terminal", "send", "--terminal", args.terminal, "--interrupt"])
            if send_res.returncode == 0:
                outcome = "interrupt_requested"
                write_log(f"상한 도달 {args.cap_seconds}초: 작업자 중단 (변경 파일 {dirty_count}개)", current_elapsed)
            else:
                outcome = "stop_unverified"
                write_log(f"상한 도달 {args.cap_seconds}초: 중단 신호 실패 — 리드가 직접 멈춘다 "
                          f"(변경 파일 {dirty_count}개, {send_res.stderr.strip()[:80]})", current_elapsed)

            provider_session_id = do_collect(is_cap=True)
            if args.save_final:
                save_final(outcome)
            emit_observation(outcome, provider_session_id=provider_session_id)
            return 3

        # 8. 간격 대기는 다음 시점이나 cap 을 넘지 않게 잘라서
        next_milestone = args.cap_seconds
        for idx, cp in enumerate(checkpoints, start=1):
            if idx not in logged_checkpoints and cp > current_elapsed:
                next_milestone = min(next_milestone, cp)
                break

        time_to_milestone = max(0.0, next_milestone - current_elapsed)
        sleep_time = min(float(args.interval), time_to_milestone)
        if sleep_time > 0:
            time.sleep(sleep_time)


if __name__ == "__main__":
    sys.exit(main())
