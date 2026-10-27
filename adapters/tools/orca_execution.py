"""Orca 실행 어댑터 (WO-H63, WO-H70 P2, WO-H73).

설치된 Orca CLI (v1.4.229) 환경을 확인하고 격리 실행 계약을 관리한다.
설치본 지원 명령:
  - worktree create --repo <selector> --name <name> --base-branch <sha> --setup skip --agent <id> --model <id> --prompt <text> --json
  - worktree show --worktree <selector> --json
  - agent launch --agent <id> --model <id> --worktree <selector> --prompt <text> --json
  - terminal list --worktree <selector> --json
  - terminal read --terminal <exact-handle> --json
  - terminal wait --terminal <handle> --for exit|tui-idle --timeout-ms <bound> --json
  - terminal close --terminal <exact-handle> --json

미지원 명령:
  - session start, session status, session stop, worker-stop

실제 terminal list 필드(handle, worktreePath, agentIdentity, connected 등)에는
적용 model, family, sandbox, approval 관측 정보가 없으며, idle 상태 역시 작업 완료를 보장하지 않는다.
따라서 기본 OrcaRunner.launch 는 필수 권한/모델/완료 관측 API 가 없음을 확인하고
작업 공간(worktree) 생성 전에 명시적으로 unsupported 로 안전하게 실행을 차단한다.
임의 API 호출, 가짜 live 성공 경로, 또는 preflight 권한 둔갑은 엄격히 금지된다.
"""

from __future__ import annotations

import argparse
import builtins
import contextlib
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

# 고정 저장소 루트 설정 (부모 탐색 금지, adapters/tools 위치 기준 parents[2])
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_REPO_ROOT / "harness") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "harness"))

from adapters.tools import preflight_agents
from harness import execution


class OrcaRunner:
    """Orca CLI 실행기 어댑터 (Core의 Runner 프로토콜 구현)."""

    id = "orca"

    def __init__(
        self,
        root: Path,
        workspace: Path | None = None,
        profile: str = "local-default",
    ):
        self.root = Path(root).resolve()
        self.workspace = Path(workspace).resolve() if workspace else None
        self.profile = profile
        self.stopped = False

    def _resolve_cli(self) -> str:
        """환경변수 ORCA_CLI_COMMAND / ORCA_CLI 또는 .local-tools.json 으로부터 실행 경로를 확인한다.
        scripts/orca.ps1 과 동일한 해석 순서를 준수하며, 명시 경로 부재 시 대체 파일(bare orca 등) fallback 은 금지한다.
        """
        candidate = os.environ.get("ORCA_CLI_COMMAND") or os.environ.get("ORCA_CLI")

        # Windows Process/User 환경변수 확인 (scripts/orca.ps1 호환)
        if not candidate and sys.platform == "win32":
            try:
                import winreg
                for key_name in ("ORCA_CLI_COMMAND", "ORCA_CLI"):
                    try:
                        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as env_key:
                            val, _ = winreg.QueryValueEx(env_key, key_name)
                            if val and str(val).strip():
                                candidate = str(val).strip()
                                break
                    except OSError:
                        pass
            except Exception:
                pass

        # .local-tools.json 확인 (의도적으로 없는 경우를 처리)
        if not candidate:
            local_tools_path = self.root / ".local-tools.json"
            if local_tools_path.is_file():
                try:
                    local_tools = json.loads(local_tools_path.read_text(encoding="utf-8"))
                    candidate = local_tools.get("orca_cli")
                except Exception:
                    pass

        if not candidate:
            raise RuntimeError(
                "Orca CLI is not configured. See dev/LOCAL-TOOLS.md; set ORCA_CLI_COMMAND to the CLI executable."
            )

        candidate_path = Path(candidate)
        if not candidate_path.is_file():
            raise RuntimeError(
                f"Configured Orca CLI does not exist: {candidate}. No alternate executable was selected."
            )

        return str(candidate_path)

    def launch(self, contract: dict) -> dict[str, Any]:
        """Orca CLI 환경을 검증하고, 실제 권한/모델 관측 API 부재 시 실행 전 명시 차단한다.
        관측 API가 없는 상황에서 worktree를 먼저 생성하지 않으며, 미지원 라이브 런타임 오류를 반환한다.
        """
        cli = self._resolve_cli()

        if not self.workspace:
            raise RuntimeError("Orca 실행을 위한 격리 작업 공간(workspace)이 지정되지 않았습니다")

        if self.workspace.resolve() == self.root.resolve():
            raise RuntimeError("격리 작업 공간이 저장소 원본(root)과 분리되지 않았습니다")

        # 설치본 Orca CLI(v1.4.229)는 실제 권한/모델 관측 및 session API를 지원하지 않음.
        # 가짜 live 성공 반환 및 불완전 worktree 생성을 방지하기 위해 생성 전 unsupported 로 안전 차단.
        raise RuntimeError(
            f"Orca CLI ({cli}): v1.4.229 설치본은 필수 권한/모델/완료 관측 API(session start/status/stop)를 지원하지 않습니다 "
            "(unsupported live runtime). 안전을 위해 작업 공간 생성 전 명시적으로 실행을 차단합니다."
        )

    def observe(self, contract: dict) -> dict[str, Any]:
        """Orca 세션 상태를 관측한다. 관측 API 부재 시 fail closed 로 예외를 발생시킨다."""
        cli = self._resolve_cli()
        raise RuntimeError(
            f"Orca CLI ({cli}): 라이브 권한 및 모델 관측 API 가 지원되지 않습니다 (unsupported live runtime)."
        )

    def stop(self, contract: dict) -> None:
        """Orca 세션을 중지한다. 설치본 미지원 session stop/worker-stop 은 호출하지 않는다."""
        self.stopped = True


def get_runner(runner_id: str, root: Path, workspace: Path | None = None) -> Any:
    """지정된 runner_id에 대응하는 런타임 러너 인스턴스를 반환한다."""
    if runner_id in ("orca", "orca-cli"):
        return OrcaRunner(root=root, workspace=workspace)
    raise ValueError(f"지원되지 않는 러너 식별자입니다: {runner_id}")


def manual_launch(
    root: Path | str,
    task: dict,
    workspace: Path | str,
    worktree: str,
    prompt_file: Path | str,
    note: str,
) -> dict[str, Any]:
    """검증된 수동 등록 후 단일 회차 Orca 에이전트 실행을 수행한다."""
    root_path = Path(root).resolve()
    workspace_path = Path(workspace).resolve()
    prompt_path = Path(prompt_file).resolve()

    # 1. 프롬프트 파일 검증 (누락 또는 빈 내용 차단)
    if not prompt_path.is_file():
        raise FileNotFoundError(f"프롬프트 파일이 존재하지 않습니다: {prompt_path}")
    prompt_text = prompt_path.read_text(encoding="utf-8")
    if not prompt_text.strip():
        raise ValueError(f"프롬프트 파일 내용이 비어 있습니다: {prompt_path}")

    # 2. 에이전트 환경 사전 검증 (preflight FAIL 차단)
    rc = preflight_agents.main([])
    if rc != 0:
        raise RuntimeError(f"에이전트 preflight 검증 실패 (exit code {rc})")

    # 3. CLI 경로 해석 및 worktree show 검증
    runner = OrcaRunner(root=root_path, workspace=workspace_path)
    cli = runner._resolve_cli()
    cmd_prefix = [sys.executable, cli] if cli.endswith(".py") else [cli]

    show_cmd = [*cmd_prefix, "worktree", "show", "--worktree", worktree, "--json"]
    p_show = subprocess.run(
        show_cmd,
        cwd=root_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    if p_show.returncode != 0:
        raise RuntimeError(f"Orca worktree show 실행 실패 ({p_show.returncode}): {p_show.stderr.strip() or p_show.stdout.strip()}")

    try:
        show_data = json.loads(p_show.stdout)
    except Exception as e:
        raise RuntimeError(f"Orca worktree show 응답 JSON 파싱 실패: {e}")

    if not show_data.get("ok"):
        raise RuntimeError(f"Orca worktree show 오류 응답: {show_data.get('error')}")

    actual_ws = show_data.get("result", {}).get("worktree", {}).get("path")
    if not actual_ws:
        raise ValueError(f"Orca worktree show 결과에 작업 공간 경로가 없습니다: {show_data}")

    if Path(actual_ws).resolve() != workspace_path:
        raise ValueError(
            f"작업 공간 경로 불일치: worktree show='{Path(actual_ws).resolve()}' != 지정 workspace='{workspace_path}'"
        )

    # 4. Core manual execution 등록 (권한/배정/역할상태/스냅샷 검증 포함)
    execution_id = execution.start_manual(root_path, task, workspace_path, note)

    # 5. 등록된 계약에서 요청 에이전트 및 모델 추출 (agy -> antigravity 매핑)
    contract_file = root_path / "reports" / "executions" / execution_id / "contract.json"
    contract_data = json.loads(contract_file.read_text(encoding="utf-8"))
    agent = contract_data.get("agent")
    model = contract_data.get("model")

    agent_id = "antigravity" if agent == "agy" else agent

    # 6. 단일 agent launch 호출 (자동 재시도 없음, 타임아웃 제한)
    launch_cmd = [
        *cmd_prefix,
        "agent",
        "launch",
        "--agent",
        agent_id,
        "--model",
        model,
        "--worktree",
        worktree,
        "--prompt",
        prompt_text,
        "--json",
    ]

    receipt: dict[str, Any] = {}
    launch_err: str | None = None
    p_launch = None
    try:
        p_launch = subprocess.run(
            launch_cmd,
            cwd=root_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
    except subprocess.TimeoutExpired as te:
        launch_err = f"agent launch 타임아웃 발생: {te}"
    except Exception as e:
        launch_err = f"agent launch 실행 중 예외 발생: {e}"

    terminal: str | None = None
    status: str = "launch_unverified"

    if p_launch is not None:
        try:
            receipt = json.loads(p_launch.stdout)
        except Exception:
            receipt = {}

        if p_launch.returncode != 0 or receipt.get("ok") is False:
            status = "launch_failed"
        elif receipt.get("ok") is True:
            res = receipt.get("result", {})
            outcome = res.get("outcome", {})
            prompt_res = res.get("prompt", {})
            if outcome.get("kind") == "terminal" and outcome.get("handle") and prompt_res.get("outcome") == "handed-to-terminal":
                status = "launched"
                terminal = outcome.get("handle")
            else:
                status = "launch_unverified"
        else:
            status = "launch_unverified"
    else:
        status = "launch_unverified"

    # 7. 실행 기록 reports/executions/<id>/orca-launch.json 영구 저장
    launch_record: dict[str, Any] = {
        "execution_id": execution_id,
        "status": status,
        "terminal": terminal,
        "receipt": receipt,
    }
    if launch_err:
        launch_record["error"] = launch_err
    if p_launch is not None and not receipt:
        launch_record["stdout"] = p_launch.stdout
        launch_record["stderr"] = p_launch.stderr

    launch_record_file = root_path / "reports" / "executions" / execution_id / "orca-launch.json"
    launch_record_file.write_text(json.dumps(launch_record, indent=2, ensure_ascii=False), encoding="utf-8")

    return {
        "execution_id": execution_id,
        "status": status,
        "terminal": terminal,
    }


def _validate_positive_int(val: Any, name: str) -> int:
    """유한한 양의 정수 여부를 검증한다."""
    if isinstance(val, bool) or not isinstance(val, int) or val <= 0:
        raise ValueError(f"{name} 은 1 이상의 유한한 정수여야 합니다: {val}")
    return val


def _request_terminal_interrupt(root: Path, workspace: Path, terminal: str) -> None:
    """감독 실패 또는 관측 누락 시 기존 터미널에 중단 신호를 1회 요청한다."""
    try:
        runner = OrcaRunner(root=root, workspace=workspace)
        cli = runner._resolve_cli()
        cmd_prefix = [sys.executable, cli] if cli.endswith(".py") else [cli]
        cmd = [*cmd_prefix, "terminal", "send", "--terminal", terminal, "--interrupt"]
        subprocess.run(
            cmd,
            cwd=root,
            capture_output=True,
            timeout=15,
        )
    except Exception:
        pass


def _find_matching_observation(
    root: Path, execution_id: str, terminal: str, workspace: Path
) -> dict[str, Any] | None:
    """reports/events.jsonl 에서 ID + terminal + workspace 가 일치하는 관측 이벤트를 검색한다."""
    events_file = root / "reports" / "events.jsonl"
    if not events_file.is_file():
        return None
    matching = None
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
                if ev.get("kind") != "execution_observation":
                    continue
                if ev.get("execution_id") != execution_id:
                    continue
                if ev.get("terminal") != terminal:
                    continue
                ev_ws = ev.get("workspace")
                if not ev_ws:
                    continue
                try:
                    if Path(ev_ws).resolve() != workspace.resolve():
                        continue
                except Exception:
                    continue
                matching = ev
    except Exception:
        pass
    return matching


def _has_matching_usage(root: Path, execution_id: str) -> bool:
    """reports/events.jsonl 에서 해당 execution_id 로 기록된 model_usage 이벤트가 있는지 확인한다."""
    events_file = root / "reports" / "events.jsonl"
    if not events_file.is_file():
        return False
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
                if ev.get("kind") == "model_usage" and ev.get("execution_id") == execution_id:
                    return True
    except Exception:
        pass
    return False


def manual_run(
    root: Path | str,
    task: dict,
    workspace: Path | str,
    worktree: str,
    prompt_file: Path | str,
    note: str,
    *,
    cap_seconds: int,
    interval: int = 60,
    round: int = 1,
    transcript_root: Path | str | None = None,
) -> dict[str, Any]:
    """검증된 수동 등록 후 단일 회차 실행 및 감시/수집 전체 수명주기를 관리한다 (WO-H73)."""
    start_mono = time.monotonic()
    started_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # 1. 인자 유효성 검증 (유한한 양의 정수)
    _validate_positive_int(cap_seconds, "cap_seconds")
    _validate_positive_int(interval, "interval")
    _validate_positive_int(round, "round")

    root_path = Path(root).resolve()
    workspace_path = Path(workspace).resolve()
    prompt_path = Path(prompt_file).resolve()

    # 2. 감시 도구 및 수집기 파일 존재 검증
    watcher_file = root_path / "adapters" / "tools" / "orca_watch.py"
    collector_file = root_path / "adapters" / "tools" / "collect_usage.py"
    if not watcher_file.is_file():
        raise FileNotFoundError(f"감시 도구 파일이 존재하지 않습니다: {watcher_file}")
    if not collector_file.is_file():
        raise FileNotFoundError(f"사용량 수집기 파일이 존재하지 않습니다: {collector_file}")

    # 3. 지원되는 에이전트 사전 검증 (agy/codex/claude)
    # F3: 고정 승인 정보(_read_execution_approval)가 존재하면 항상 승인 정보에서 agent/model을 파생
    app: dict[str, Any] = {}
    if task.get("approval_ref"):
        try:
            app = execution._read_execution_approval(root_path, task["approval_ref"], task)
        except Exception as e:
            raise ValueError(f"승인 정보에서 에이전트를 확인할 수 없습니다: {e}")

    declared_agent = app.get("agent") if "agent" in app else task.get("agent")
    declared_model = app.get("model") if "model" in app else task.get("model")
    declared_tier = app.get("tier") if "tier" in app else task.get("tier")
    declared_risk = app.get("risk") if "risk" in app else task.get("risk")

    if not declared_agent or declared_agent not in ("agy", "codex", "claude"):
        raise ValueError(f"지원되지 않는 에이전트입니다: {declared_agent}")

    # 4. 검증된 수동 등록 및 단일 agent launch 호출 (F5: 사전 검증/진행 출력은 stderr로 리다이렉트)
    with contextlib.redirect_stdout(sys.stderr):
        launch_res = manual_launch(
            root=root_path,
            task=task,
            workspace=workspace_path,
            worktree=worktree,
            prompt_file=prompt_path,
            note=note,
        )

    execution_id = launch_res.get("execution_id")
    terminal = launch_res.get("terminal")
    launch_status = launch_res.get("status")

    if launch_status != "launched" or not terminal:
        ended_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        wall_seconds = max(0.0, builtins.round(time.monotonic() - start_mono, 3))
        status = launch_status if launch_status in ("launch_failed", "launch_unverified") else "launch_failed"
        run_result = {
            "execution_id": execution_id,
            "status": status,
            "terminal": terminal,
            "outcome": None,
            "watcher_exit": None,
            "usage_status": "missing",
            "requires_manual_confirmation": True,
            "started": started_utc,
            "ended": ended_utc,
            "wall_seconds": wall_seconds,
            "time_scope": "runner_wall",
            "advice": f"에이전트 실행이 확인되지 않아 감시를 시작하지 않았습니다 ({status}).",
        }
        if execution_id:
            run_record_file = root_path / "reports" / "executions" / execution_id / "orca-run.json"
            run_record_file.parent.mkdir(parents=True, exist_ok=True)
            run_record_file.write_text(json.dumps(run_result, indent=2, ensure_ascii=False), encoding="utf-8")
        return run_result

    # 5. 계약 정보 추출 및 감시 프로세스 실행 (F3: 등록 계약에서 실제 식별자 파생)
    contract_file = root_path / "reports" / "executions" / execution_id / "contract.json"
    contract_data: dict[str, Any] = {}
    if contract_file.is_file():
        try:
            contract_data = json.loads(contract_file.read_text(encoding="utf-8"))
        except Exception:
            contract_data = {}

    agent = contract_data.get("agent") or declared_agent
    model = contract_data.get("model") or declared_model
    wo = contract_data.get("wo") or task.get("wo")
    role = contract_data.get("role") or task.get("role")

    # F2: OrcaRunner._resolve_cli() 경로를 --orca 인자로 감시기에 정확히 전달
    runner = OrcaRunner(root=root_path, workspace=workspace_path)
    cli = runner._resolve_cli()

    watcher_cmd = [
        sys.executable,
        str(watcher_file),
        "--terminal",
        terminal,
        "--workspace",
        str(workspace_path),
        "--agent",
        agent,
        "--wo",
        wo,
        "--role",
        role,
        "--cap-seconds",
        str(cap_seconds),
        "--interval",
        str(interval),
        "--round",
        str(round),
        "--execution-id",
        execution_id,
        "--orca",
        cli,
        "--log",
        str(root_path / "reports" / "executions" / execution_id / "orca-watch.log"),
        "--collect-on-exit",
    ]
    if model:
        watcher_cmd.extend(["--model", str(model)])
    if declared_tier:
        watcher_cmd.extend(["--tier", str(declared_tier)])
    if declared_risk:
        watcher_cmd.extend(["--risk", str(declared_risk)])
    if transcript_root:
        watcher_cmd.extend(["--transcript-root", str(transcript_root)])

    watcher_exit = None
    watcher_failed = False
    try:
        p_watch = subprocess.run(
            watcher_cmd,
            cwd=root_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=cap_seconds + 60,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        if p_watch.stdout:
            sys.stderr.write(p_watch.stdout)
            sys.stderr.flush()
        if p_watch.stderr:
            sys.stderr.write(p_watch.stderr)
            sys.stderr.flush()
        watcher_exit = p_watch.returncode
        if watcher_exit not in (0, 3):
            watcher_failed = True
    except Exception:
        watcher_failed = True

    # 6. 관측 결과 확인 및 상태 판정
    matching_obs = _find_matching_observation(root_path, execution_id, terminal, workspace_path)
    outcome = None
    runner_interrupt_sent = False

    if watcher_failed or matching_obs is None:
        status = "monitoring_unverified"
        outcome = matching_obs.get("outcome") if matching_obs else None
        _request_terminal_interrupt(root_path, workspace_path, terminal)
        runner_interrupt_sent = True
    else:
        outcome = matching_obs.get("outcome")
        if outcome == "idle_only" and watcher_exit == 0:
            status = "observed"
        elif outcome in ("interrupt_requested", "stop_unverified") and watcher_exit == 3:
            status = "capped"
        elif outcome == "terminal_unavailable":
            status = "monitoring_unverified"
        else:
            status = "monitoring_unverified"
            _request_terminal_interrupt(root_path, workspace_path, terminal)
            runner_interrupt_sent = True

    # 7. 사용량 기록 여부 확인
    has_usage = _has_matching_usage(root_path, execution_id)
    usage_status = "recorded" if has_usage else "missing"

    ended_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    wall_seconds = max(0.0, builtins.round(time.monotonic() - start_mono, 3))

    # 8. 조언(advice) 구성 (F4: 실제 관측/요청/미확인 상태를 정확히 기록)
    advice_parts = []
    if status == "observed":
        advice_parts.append("워커 유휴 상태(idle_only)가 관측되었습니다 (작업 완료 보장 아님, 수동 확인 필요).")
    elif status == "capped":
        if outcome == "stop_unverified":
            advice_parts.append(
                f"상한 시간({cap_seconds}초)에 도달했으나 중단 요청 실패로 워커 중단 여부는 미검증 상태입니다."
            )
        else:
            advice_parts.append(
                f"상한 시간({cap_seconds}초)에 도달하여 터미널 중단 신호가 요청되었습니다 (실제 프로세스 중단 여부는 미확인)."
            )
    elif status == "monitoring_unverified":
        if outcome == "terminal_unavailable":
            advice_parts.append(
                "터미널을 사용할 수 없어 감시가 종료되었으며, 터미널 부재로 중단 신호는 전송되지 않았습니다."
            )
        elif runner_interrupt_sent:
            advice_parts.append(
                "감시 실패 또는 관측 누락으로 미검증 상태이며, 알려진 터미널에 중단 신호가 1회 요청되었습니다."
            )
        else:
            advice_parts.append("감시 결과가 미검증 상태입니다.")
    else:
        advice_parts.append(f"실행 상태: {status}.")

    if usage_status == "recorded":
        advice_parts.append("모델 사용량이 기록되었습니다.")
    else:
        advice_parts.append("이 실행에 결합된 모델 사용량이 없습니다 (추후 수집 가능).")

    advice = " ".join(advice_parts)

    # 9. reports/executions/<id>/orca-run.json 저장 및 결과 반환
    run_result = {
        "execution_id": execution_id,
        "status": status,
        "terminal": terminal,
        "outcome": outcome,
        "watcher_exit": watcher_exit,
        "usage_status": usage_status,
        "requires_manual_confirmation": True,
        "started": started_utc,
        "ended": ended_utc,
        "wall_seconds": wall_seconds,
        "time_scope": "runner_wall",
        "advice": advice,
    }
    run_record_file = root_path / "reports" / "executions" / execution_id / "orca-run.json"
    run_record_file.parent.mkdir(parents=True, exist_ok=True)
    run_record_file.write_text(json.dumps(run_result, indent=2, ensure_ascii=False), encoding="utf-8")

    return run_result


def main(argv: list[str] | None = None) -> int:
    """CLI 진입점: manual-launch 및 manual-run 명령 지원."""
    if argv is None:
        argv = sys.argv[1:]

    parser = argparse.ArgumentParser(description="Orca 실행 어댑터 CLI")
    subparsers = parser.add_subparsers(dest="subcommand")

    # manual-launch
    ml_parser = subparsers.add_parser("manual-launch")
    ml_parser.add_argument("--root", default=str(_REPO_ROOT))
    ml_parser.add_argument("--wo", required=True)
    ml_parser.add_argument("--slice", required=True)
    ml_parser.add_argument("--role", required=True)
    ml_parser.add_argument("--base", required=True)
    ml_parser.add_argument("--approval-ref", required=True)
    ml_parser.add_argument("--workspace", required=True)
    ml_parser.add_argument("--worktree", required=True)
    ml_parser.add_argument("--prompt-file", required=True)
    ml_parser.add_argument("--note", required=True)

    # manual-run
    mr_parser = subparsers.add_parser("manual-run")
    mr_parser.add_argument("--root", default=str(_REPO_ROOT))
    mr_parser.add_argument("--wo", required=True)
    mr_parser.add_argument("--slice", required=True)
    mr_parser.add_argument("--role", required=True)
    mr_parser.add_argument("--base", required=True)
    mr_parser.add_argument("--approval-ref", required=True)
    mr_parser.add_argument("--workspace", required=True)
    mr_parser.add_argument("--worktree", required=True)
    mr_parser.add_argument("--prompt-file", required=True)
    mr_parser.add_argument("--note", required=True)
    mr_parser.add_argument("--cap-seconds", type=int, required=True)
    mr_parser.add_argument("--interval", type=int, default=60)
    mr_parser.add_argument("--round", type=int, default=1)
    mr_parser.add_argument("--transcript-root", default=None)

    args = parser.parse_args(argv)

    if args.subcommand == "manual-launch":
        task = {
            "wo": args.wo,
            "slice": args.slice,
            "role": args.role,
            "base": args.base,
            "approval_ref": args.approval_ref,
        }
        try:
            res = manual_launch(
                root=args.root,
                task=task,
                workspace=args.workspace,
                worktree=args.worktree,
                prompt_file=args.prompt_file,
                note=args.note,
            )
        except (ValueError, RuntimeError, OSError) as e:
            print(f"사전 검증 또는 등록 실패: {e}", file=sys.stderr)
            print("복구 가이드: 작업 지시서 파라미터, worktree show 상태 및 승인 참조를 확인하십시오.", file=sys.stderr)
            return 1

        print(json.dumps(res, indent=2, ensure_ascii=False))
        if res.get("status") != "launched":
            return 1
        return 0

    elif args.subcommand == "manual-run":
        task = {
            "wo": args.wo,
            "slice": args.slice,
            "role": args.role,
            "base": args.base,
            "approval_ref": args.approval_ref,
        }
        try:
            res = manual_run(
                root=args.root,
                task=task,
                workspace=args.workspace,
                worktree=args.worktree,
                prompt_file=args.prompt_file,
                note=args.note,
                cap_seconds=args.cap_seconds,
                interval=args.interval,
                round=args.round,
                transcript_root=args.transcript_root,
            )
        except (ValueError, RuntimeError, OSError) as e:
            print(f"사전 검증 또는 등록 실패: {e}", file=sys.stderr)
            return 1

        print(json.dumps(res, indent=2, ensure_ascii=False))
        if res.get("status") == "observed":
            return 0
        elif res.get("status") == "capped":
            return 3
        else:
            return 1

    parser.print_help(file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
