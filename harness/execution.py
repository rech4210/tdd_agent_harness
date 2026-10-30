"""실행 관리자 및 검증기 (Core Execution Boundary, WO-H63).

지정된 격리 런타임(Runner)을 통한 작업 위임(start), 산출물 검증(check), 원본 채택(accept)을 관리한다.
수용 범위는 role allow ∩ task paths (deny 우선) 규칙을 엄격히 적용하며,
.git 만 제외한 작업 트리의 모든 변경(ignored/삭제/생성)을 감지하고,
검사 이후의 변경, 원본 드리프트, 중복 채택을 차단한다.

[주의: 보안 및 경계 고지]
이 모듈의 검증과 거부는 운영체제(OS) 수준의 사용자/역할별 파일 쓰기 차단(OS sandbox/권한)이 아니며,
하네스 실행 경계 검증 및 git diff/snapshot 대조를 통한 소프트웨어적 통제 메커니즘이다.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# 바이트코드 생성 차단
sys.dont_write_bytecode = True

# harness 디렉터리 및 저장소 루트를 sys.path에 등록
HARNESS_DIR = Path(__file__).resolve().parent
REPO_ROOT = HARNESS_DIR.parent
if str(HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(HARNESS_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import role_state
from workspace import build_plan, is_valid_exact_path


def _git(root: Path, *args: str) -> str:
    """Git 명령어 실행 헬퍼."""
    p = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    if p.returncode != 0:
        raise RuntimeError(f"Git 명령 실패 ({' '.join(args)}): {p.stderr.strip()}")
    return p.stdout.strip()


def _git_bytes(root: Path, *args: str) -> bytes:
    """Git show 등 바이너리 결과용 헬퍼."""
    p = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    if p.returncode != 0:
        raise RuntimeError(f"Git 바이너리 명령 실패 ({' '.join(args)})")
    return p.stdout


def _robust_rmtree(path: Path) -> None:
    """Windows 파일 잠금 및 읽기 전용 속성을 극복하는 안전한 rmtree."""
    if not path.exists():
        return

    def on_error(func, p, exc_info):
        try:
            os.chmod(p, 0o777)
            func(p)
        except Exception:
            pass

    shutil.rmtree(path, onerror=on_error)


def _match_any(path: str, globs: list[str]) -> bool:
    """단일 경로가 glob 목록 중 하나라도 일치하는지 검사한다."""
    return any(fnmatch.fnmatch(path, g) for g in globs)


def _hash_tree(dir_path: Path) -> dict[str, str]:
    """디렉터리 내 모든 일반 파일의 상대 경로별 SHA-256 해시를 계산한다."""
    hashes: dict[str, str] = {}
    if not dir_path.is_dir():
        return hashes
    for p in sorted(dir_path.rglob("*")):
        if p.is_file() and ".git" not in p.parts:
            rel = p.relative_to(dir_path).as_posix()
            hashes[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return hashes


def _read_execution_approval(root: Path, approval_ref: str, task: dict) -> dict:
    """고정 git commit blob JSON에서 kind=execution 승인을 읽고 검증한다."""
    if not approval_ref:
        raise ValueError("승인 참조가 비어 있습니다.")

    m = re.fullmatch(r"([0-9a-fA-F]{7,40}):([^#]+)#([A-Za-z0-9_-]+)", approval_ref)
    if not m:
        raise ValueError(
            f"승인 참조 형식이 올바르지 않습니다: '{approval_ref}'. "
            "<commit>:<path>#<id> 형식이어야 합니다."
        )

    commit_sha, rel_path, approval_id = m.group(1), m.group(2), m.group(3)
    blob_bytes = _git_bytes(root, "show", f"{commit_sha}:{rel_path}")

    try:
        doc = json.loads(blob_bytes.decode("utf-8"))
    except Exception as e:
        raise ValueError(f"승인 blob JSON 파싱 실패: {e}")

    if doc.get("schema") != 1:
        raise ValueError(f"승인 문서 schema={doc.get('schema')} 지원되지 않습니다 (기대값: 1)")

    approvals = doc.get("approvals", {})
    if approval_id not in approvals:
        raise ValueError(f"승인 ID '{approval_id}' 가 승인 문서에 없습니다.")

    app = approvals[approval_id]
    if app.get("by") != "user":
        raise ValueError(f"승인자(by)가 'user'가 아닙니다: '{app.get('by')}'")

    if app.get("kind") != "execution":
        raise ValueError(f"승인 종류(kind)가 'execution'이 아닙니다: '{app.get('kind')}'")

    for k in ("wo", "slice", "role"):
        if app.get(k) != task.get(k):
            raise ValueError(
                f"승인 정보 불일치 ({k}): 승인 '{app.get(k)}' != 작업 '{task.get(k)}'"
            )

    paths = app.get("paths", [])
    if not isinstance(paths, list) or not paths:
        raise ValueError("승인에 유효한 paths 목록이 없습니다.")

    for p in paths:
        pp = Path(p)
        if pp.is_absolute() or ".." in pp.parts:
            raise ValueError(f"승인 경로 탈출 감지: {p}")

    return app


def _validate_observation(obs: Any, contract: dict, is_completion: bool = False) -> str | None:
    """러너 관측치(observation) 공통 계약 검증.
    필수 키, sandbox(workspace-write/read-only), approval(never/on-request),
    agent/model/family 일치, workspace 분리 및 디렉터리 존재 여부를 엄격히 확인한다.
    완료 시점에는 source, agent, model, family, workspace, session_id, permission 의 불변성을 재검증한다.
    """
    if not isinstance(obs, dict):
        return "러너 관측치(observation)가 딕셔너리가 아닙니다"

    required_keys = (
        "workspace",
        "session_id",
        "agent",
        "model",
        "family",
        "permission",
        "status",
        "source",
    )
    for k in required_keys:
        if k not in obs:
            return f"관측치에 필수 키 '{k}' 가 누락되었습니다"
        val = obs[k]
        if val is None or val == "":
            return f"관측치의 필수 키 '{k}' 가 비어 있습니다"

    perm = obs.get("permission")
    if not isinstance(perm, dict) or not perm:
        return "러너 권한(permission) 정보가 딕셔너리가 아니거나 비어 있습니다"

    sandbox = perm.get("sandbox")
    if sandbox not in ("workspace-write", "read-only"):
        return f"허용되지 않은 샌드박스 정책입니다: '{sandbox}' (허용: 'workspace-write', 'read-only')"

    approval = perm.get("approval")
    if approval not in ("never", "on-request"):
        return f"허용되지 않은 승인 정책입니다: '{approval}' (허용: 'never', 'on-request')"

    if obs.get("agent") != contract.get("agent"):
        return f"에이전트 불일치: 관측 '{obs.get('agent')}' != 계약 '{contract.get('agent')}'"
    if obs.get("model") != contract.get("model"):
        return f"모델 불일치: 관측 '{obs.get('model')}' != 계약 '{contract.get('model')}'"
    if obs.get("family") != contract.get("family"):
        return f"계열 불일치: 관측 '{obs.get('family')}' != 계약 '{contract.get('family')}'"

    try:
        ws_path = Path(obs["workspace"]).resolve()
    except Exception as e:
        return f"작업 공간 경로 해석 실패: {e}"

    if not ws_path.is_dir():
        return f"작업 공간 디렉터리가 실제로 존재하지 않습니다: {ws_path}"

    origin_root = Path(contract["origin_root"]).resolve() if "origin_root" in contract else None
    if origin_root:
        if ws_path == origin_root:
            return f"작업 공간(workspace)이 원본(root)과 분리되지 않았습니다: {ws_path}"
        if origin_root in ws_path.parents:
            return f"작업 공간(workspace)이 원본(root) 내부에 중첩되어 있습니다: {ws_path}"
        if ws_path in origin_root.parents:
            return f"원본(root)이 작업 공간(workspace) 내부에 중첩되어 있습니다: {origin_root}"
        try:
            if ws_path.is_relative_to(origin_root):
                return f"작업 공간(workspace)이 원본(root) 내부에 중첩되어 있습니다: {ws_path}"
            if origin_root.is_relative_to(ws_path):
                return f"원본(root)이 작업 공간(workspace) 내부에 중첩되어 있습니다: {origin_root}"
        except Exception:
            pass

    if is_completion:
        if obs.get("status") != "completed":
            return f"실행이 완료되지 않았습니다 (현재 상태: {obs.get('status')})"
        if "workspace" in contract and str(ws_path) != str(Path(contract["workspace"]).resolve()):
            return f"완료 시점 작업 공간 드리프트: 관측 '{ws_path}' != 시작 '{contract['workspace']}'"
        if "session_id" in contract and obs.get("session_id") != contract.get("session_id"):
            return f"완료 시점 세션 ID 드리프트: 관측 '{obs.get('session_id')}' != 시작 '{contract.get('session_id')}'"
        if "permission" in contract and obs.get("permission") != contract.get("permission"):
            return f"완료 시점 권한 드리프트: 관측 {obs.get('permission')} != 시작 {contract.get('permission')}"
        if "source" in contract and obs.get("source") != contract.get("source"):
            return f"완료 시점 source 드리프트: 관측 '{obs.get('source')}' != 시작 '{contract.get('source')}'"

    return None


def _snapshot_directory(dir_path: Path) -> dict[str, dict[str, Any]]:
    """디렉터리 내 전체 파일의 바이트 해시, 크기, 모드 및 유형을 기록한다.
    .git 만 제외하며, 심볼릭 링크나 경로 탈출은 즉시 거부한다.
    """
    dir_path = dir_path.resolve()
    snapshot: dict[str, dict[str, Any]] = {}
    for p in sorted(dir_path.rglob("*")):
        try:
            rel = p.relative_to(dir_path).as_posix()
        except ValueError:
            raise ValueError(f"경로 탈출 감지: {p}")

        if ".git" in p.relative_to(dir_path).parts:
            continue
        if p.is_symlink():
            raise ValueError(f"심볼릭 링크는 허용되지 않습니다: {rel}")
        if p.is_file():
            content = p.read_bytes()
            st = p.stat()
            snapshot[rel] = {
                "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content),
                "mode": stat.S_IMODE(st.st_mode),
                "type": "file",
            }
    return snapshot


def _diff_snapshots(
    start_snap: dict[str, dict[str, Any]],
    cur_snap: dict[str, dict[str, Any]],
) -> dict[str, str]:
    """시작 스냅샷과 현재 스냅샷 간의 변경 사항(delete, add, modify)을 산출한다."""
    changed: dict[str, str] = {}
    for f, info in start_snap.items():
        if f not in cur_snap:
            changed[f] = "delete"
        elif (
            cur_snap[f]["sha256"] != info["sha256"]
            or cur_snap[f].get("mode") != info.get("mode")
            or cur_snap[f].get("type") != info.get("type")
            or cur_snap[f].get("size") != info.get("size")
        ):
            changed[f] = "modify"

    for f in cur_snap.keys():
        if f not in start_snap:
            changed[f] = "add"

    return changed


def _run_trusted_gates(
    root: Path,
    base_sha: str,
    role: str,
    changed_files: dict[str, str],
    ws_path: Path,
    *,
    run_id: str | None = None,
    wo: str | None = None,
    slice_id: str | None = None,
    full_slice: bool = False,
    extra_args: list[str] | None = None,
) -> dict[str, Any]:
    """임시 검사용 repo에서 원본의 신뢰 source/config와 worker diff를 대조하여 게이트를 실행한다."""
    ws_path = ws_path.resolve()
    snap_before = _snapshot_directory(ws_path)
    candidate_digest = hashlib.sha256(
        json.dumps(snap_before, sort_keys=True).encode("utf-8")
    ).hexdigest()

    tmp_dir = Path(tempfile.mkdtemp(prefix="h63-verify-"))
    try:
        tmp_repo = tmp_dir / "repo"
        _git(root, "clone", "--quiet", "--no-hardlinks", str(root), str(tmp_repo))
        _git(tmp_repo, "checkout", "--quiet", base_sha)

        pinned_config = tmp_dir / "pinned-config"
        if (tmp_repo / "config").is_dir():
            shutil.copytree(tmp_repo / "config", pinned_config)

        # Worker diff 반영
        for f, action in changed_files.items():
            target_p = tmp_repo / f
            if action == "delete":
                if target_p.exists():
                    target_p.unlink()
            else:
                target_p.parent.mkdir(parents=True, exist_ok=True)
                target_p.write_bytes((ws_path / f).read_bytes())
                if "mode" in snap_before.get(f, {}):
                    try:
                        os.chmod(target_p, stat.S_IMODE(snap_before[f]["mode"]))
                    except Exception:
                        pass

        # 후보쪽 harness/config/templates/adapters 신뢰 금지: 원본의 pinned trusted source/config로 복원
        for d in ("harness", "config", "templates", "adapters"):
            src_d = pinned_config if (d == "config" and pinned_config.is_dir()) else (root / d)
            dst_d = tmp_repo / d
            if src_d.is_dir():
                shutil.rmtree(dst_d, ignore_errors=True)
                shutil.copytree(src_d, dst_d)

        snap_mid = _snapshot_directory(ws_path)
        if snap_mid != snap_before:
            raise ValueError("게이트 실행 준비 중 워커 작업 공간 변경 감지")

        # 게이트 실행 전 시작 경계 캡처 (후보 diff 복사 완료 후, 자식 실행 직전)
        scratch_events_file = tmp_repo / "reports" / "events.jsonl"
        scratch_lines_before: list[str] = []
        if scratch_events_file.is_file():
            try:
                scratch_lines_before = [
                    line
                    for line in scratch_events_file.read_text(
                        encoding="utf-8", errors="replace"
                    ).splitlines()
                    if line.strip()
                ]
            except Exception:
                scratch_lines_before = []
        start_line_count = len(scratch_lines_before)

        # 후보가 제공한 기존 게이트 보고서가 신뢰 판정으로 오인되지 않도록 사전 정리
        tmp_rep_file = tmp_repo / "reports" / "gate_report.json"
        if tmp_rep_file.exists():
            tmp_rep_file.unlink()

        if full_slice:
            # 전체 slice 게이트 실제 수행
            run_script = tmp_repo / "harness" / "run_gates.py"
            if not run_script.is_file():
                raise ValueError("게이트 실행 스크립트(harness/run_gates.py)가 없습니다")

            cmd = [
                sys.executable,
                "-B",
                str(run_script),
                "--role",
                role,
                "--base",
                base_sha,
            ]
            if wo:
                cmd.extend(["--wo", wo])
            if slice_id:
                cmd.extend(["--slice", slice_id])
            if extra_args:
                cmd.extend(extra_args)

            p_run = subprocess.run(
                cmd,
                cwd=tmp_repo,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )

            if p_run.stdout:
                print(p_run.stdout, end="")
            if p_run.stderr:
                print(p_run.stderr, end="", file=sys.stderr)

            snap_after = _snapshot_directory(ws_path)
            if snap_after != snap_before:
                raise ValueError("게이트 실행 중 워커 작업 공간 변경 감지")

            had_fresh_report = tmp_rep_file.is_file()
            rep_data: dict[str, Any] = {}
            if had_fresh_report:
                try:
                    rep_data = json.loads(tmp_rep_file.read_text(encoding="utf-8"))
                except Exception:
                    rep_data = {}

            if not rep_data:
                rep_data = {
                    "wo": wo or "",
                    "slice": slice_id or "",
                    "role": role,
                    "base": base_sha,
                    "passed": False,
                    "gates": [],
                    "evidence": {
                        "schema": 1,
                        "exit_code": p_run.returncode,
                        "verdict": "FAIL",
                    },
                }

            # 실행 ID, base_sha, role, wo, slice 및 후보 digest 필수 결합
            rep_data["execution_id"] = run_id
            rep_data["base_sha"] = base_sha
            rep_data["candidate_digest"] = candidate_digest
            rep_data["role"] = role
            if wo:
                rep_data["wo"] = wo
            if slice_id:
                rep_data["slice"] = slice_id

            if "evidence" not in rep_data or not isinstance(rep_data["evidence"], dict):
                rep_data["evidence"] = {}
            ev = rep_data["evidence"]
            ev["execution_id"] = run_id
            ev["base_sha"] = base_sha
            ev["candidate_digest"] = candidate_digest
            ev["role"] = role
            if wo:
                ev["wo"] = wo
            if slice_id:
                ev["slice"] = slice_id

            rep_json = json.dumps(rep_data, indent=2, ensure_ascii=False)

            if run_id:
                run_rep = root / "reports" / "executions" / run_id / "gate_report.json"
                run_rep.parent.mkdir(parents=True, exist_ok=True)
                run_rep.write_text(rep_json, encoding="utf-8", newline="\n")

            global_rep = root / "reports" / "gate_report.json"
            global_rep.parent.mkdir(parents=True, exist_ok=True)
            global_rep.write_text(rep_json, encoding="utf-8", newline="\n")

            is_feedback = bool(extra_args and "--feedback" in extra_args)

            # 신선한 최종 보고서가 실제로 생성된 정상/실패 게이트 실행에 대해서만 실제 이벤트 및 아티팩트 전달
            if not is_feedback and had_fresh_report:
                if not scratch_events_file.is_file():
                    raise RuntimeError("게이트 실행 이벤트 파일(evidence events file)이 누락되었습니다")

                try:
                    scratch_raw_bytes = scratch_events_file.read_bytes()
                    scratch_text = scratch_raw_bytes.decode("utf-8")
                except UnicodeError as ue:
                    raise RuntimeError(f"게이트 실행 이벤트 파일(evidence events file) UTF-8 디코딩 실패: {ue}")

                scratch_lines = [line for line in scratch_text.splitlines() if line.strip()]
                if not scratch_lines:
                    raise RuntimeError("게이트 실행 이벤트 로그(evidence event log)가 비어 있습니다")

                if len(scratch_lines) <= start_line_count:
                    raise RuntimeError("새로 생성된 게이트 실행 이벤트(new gate_run evidence event)가 없습니다")

                raw_last_line = scratch_lines[-1]
                try:
                    child_event = json.loads(raw_last_line)
                except Exception as je:
                    raise RuntimeError(f"게이트 실행 이벤트(evidence event) JSON 파싱 실패: {je}")

                if not isinstance(child_event, dict):
                    raise RuntimeError("게이트 실행 이벤트(evidence event) 형식이 JSON 객체가 아닙니다")

                if child_event.get("kind") != "gate_run":
                    raise RuntimeError(f"게이트 실행 이벤트 종류(kind)가 gate_run 이 아닙니다: {child_event.get('kind')}")

                # 이벤트 필수 필드 및 호출/보고서 일치 검증
                for req_f in ("wo", "slice", "role", "base", "passed", "seconds", "ts", "run_file", "gates", "purpose"):
                    if req_f not in child_event:
                        raise ValueError(f"게이트 실행 이벤트 증거(evidence event) 필수 필드 누락: {req_f}")

                expected_wo = rep_data.get("wo") or wo
                if expected_wo and child_event.get("wo") != expected_wo:
                    raise ValueError(f"게이트 실행 이벤트 증거(evidence event) wo 불일치: {child_event.get('wo')} != {expected_wo}")

                expected_slice = rep_data.get("slice") or slice_id
                if expected_slice and child_event.get("slice") != expected_slice:
                    raise ValueError(f"게이트 실행 이벤트 증거(evidence event) slice 불일치: {child_event.get('slice')} != {expected_slice}")

                expected_role = rep_data.get("role") or role
                if expected_role and child_event.get("role") != expected_role:
                    raise ValueError(f"게이트 실행 이벤트 증거(evidence event) role 불일치: {child_event.get('role')} != {expected_role}")

                expected_base = rep_data.get("base") or base_sha
                if expected_base and child_event.get("base") != expected_base:
                    raise ValueError(f"게이트 실행 이벤트 증거(evidence event) base 불일치: {child_event.get('base')} != {expected_base}")

                if child_event.get("passed") != rep_data.get("passed"):
                    raise ValueError(f"게이트 실행 이벤트 증거(evidence event) passed 불일치: {child_event.get('passed')} != {rep_data.get('passed')}")

                rep_ev = rep_data.get("evidence") or {}
                if not isinstance(rep_ev, dict):
                    rep_ev = {}
                expected_target = rep_ev.get("target_sha")
                if expected_target and child_event.get("target_sha") != expected_target:
                    raise ValueError(f"게이트 실행 이벤트 증거(evidence event) target_sha 불일치: {child_event.get('target_sha')} != {expected_target}")

                expected_first_failed = next(
                    (r["id"] for r in rep_data.get("gates", []) if r.get("passed") is False),
                    None,
                )
                if child_event.get("first_failed") != expected_first_failed:
                    raise ValueError(f"게이트 실행 이벤트 증거(evidence event) first_failed 불일치: {child_event.get('first_failed')} != {expected_first_failed}")

                scope = rep_ev.get("scope")
                expected_purpose = "self_check" if (isinstance(scope, dict) and "only" in scope) else "verdict"
                if child_event.get("purpose") != expected_purpose:
                    raise ValueError(f"게이트 실행 이벤트 증거(evidence event) purpose 불일치: {child_event.get('purpose')} != {expected_purpose}")

                expected_gates = {r["id"]: r.get("passed") for r in rep_data.get("gates", [])}
                if child_event.get("gates") != expected_gates:
                    raise ValueError(f"게이트 실행 이벤트 증거(evidence event) gates 불일치: {child_event.get('gates')} != {expected_gates}")

                incoming_exec_id = child_event.get("execution_id")
                if incoming_exec_id is not None and run_id is not None and incoming_exec_id != run_id:
                    raise ValueError(f"게이트 실행 이벤트 증거(evidence event) execution_id 모순: {incoming_exec_id} != {run_id}")

                incoming_digest = child_event.get("candidate_digest")
                if incoming_digest is not None and incoming_digest != candidate_digest:
                    raise ValueError(f"게이트 실행 이벤트 증거(evidence event) candidate_digest 모순: {incoming_digest} != {candidate_digest}")

                child_run_file = child_event.get("run_file")
                if not child_run_file or not isinstance(child_run_file, str):
                    raise ValueError("게이트 실행 이벤트 증거(evidence event)에 run_file 이 없거나 올바르지 않습니다")

                child_p = Path(child_run_file)
                if child_p.is_absolute() or ".." in child_p.parts:
                    raise ValueError(f"게이트 실행 이벤트 run_file 경로 탈출 감지: {child_run_file}")

                # 실행 결과 아티팩트(run artifact) 검증
                scratch_artifact_path = tmp_repo / child_run_file
                if not scratch_artifact_path.is_file():
                    raise RuntimeError(f"게이트 실행 결과 아티팩트 증거(evidence run artifact) 파일이 없습니다: {child_run_file}")

                try:
                    artifact_raw = scratch_artifact_path.read_bytes()
                    artifact_data = json.loads(artifact_raw.decode("utf-8"))
                except Exception as ae:
                    raise RuntimeError(f"게이트 실행 결과 아티팩트 증거(evidence run artifact) 파싱 실패: {ae}")

                if not isinstance(artifact_data, dict):
                    raise RuntimeError("게이트 실행 결과 아티팩트 증거(evidence run artifact) 형식이 객체가 아닙니다")

                if artifact_data.get("passed") != child_event.get("passed"):
                    raise ValueError(f"아티팩트 증거(evidence artifact) passed 불일치: {artifact_data.get('passed')} != {child_event.get('passed')}")

                if expected_wo and artifact_data.get("wo") != expected_wo:
                    raise ValueError(f"아티팩트 증거(evidence artifact) wo 불일치: {artifact_data.get('wo')} != {expected_wo}")

                if expected_slice and artifact_data.get("slice") != expected_slice:
                    raise ValueError(f"아티팩트 증거(evidence artifact) slice 불일치: {artifact_data.get('slice')} != {expected_slice}")

                if expected_role and artifact_data.get("role") != expected_role:
                    raise ValueError(f"아티팩트 증거(evidence artifact) role 불일치: {artifact_data.get('role')} != {expected_role}")

                art_ev = artifact_data.get("evidence")
                if not isinstance(art_ev, dict):
                    art_ev = {}
                    artifact_data["evidence"] = art_ev

                if expected_target and art_ev.get("target_sha") != expected_target:
                    raise ValueError(f"아티팩트 증거(evidence artifact) target_sha 불일치: {art_ev.get('target_sha')} != {expected_target}")

                art_exec_id = artifact_data.get("execution_id") or art_ev.get("execution_id")
                if art_exec_id is not None and run_id is not None and art_exec_id != run_id:
                    raise ValueError(f"아티팩트 증거(evidence artifact) execution_id 모순: {art_exec_id} != {run_id}")

                art_digest = artifact_data.get("candidate_digest") or art_ev.get("candidate_digest")
                if art_digest is not None and art_digest != candidate_digest:
                    raise ValueError(f"아티팩트 증거(evidence artifact) candidate_digest 모순: {art_digest} != {candidate_digest}")

                # 아티팩트에 컨텍스트 바인딩 및 충돌 방지 보관(archive)
                if run_id:
                    artifact_data["execution_id"] = run_id
                    art_ev["execution_id"] = run_id
                artifact_data["candidate_digest"] = candidate_digest
                art_ev["candidate_digest"] = candidate_digest

                origin_runs_dir = root / "reports" / "runs"
                origin_runs_dir.mkdir(parents=True, exist_ok=True)

                stem = child_p.stem
                ext = child_p.suffix or ".json"

                target_name = f"{stem}{ext}"
                if (origin_runs_dir / target_name).exists():
                    n = 2
                    while (origin_runs_dir / f"{stem}-{n}{ext}").exists():
                        n += 1
                    target_name = f"{stem}-{n}{ext}"

                archived_path = origin_runs_dir / target_name
                archived_path.write_text(
                    json.dumps(artifact_data, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                    newline="\n",
                )

                # 이벤트에 컨텍스트 바인딩 및 원본 reports/events.jsonl 에 추가
                bound_event = dict(child_event)
                if run_id:
                    bound_event["execution_id"] = run_id
                bound_event["candidate_digest"] = candidate_digest
                bound_event["run_file"] = f"reports/runs/{target_name}"

                origin_events_path = root / "reports" / "events.jsonl"
                origin_events_path.parent.mkdir(parents=True, exist_ok=True)

                needs_prefix_newline = False
                if origin_events_path.is_file():
                    size = origin_events_path.stat().st_size
                    if size > 0:
                        with origin_events_path.open("rb") as ef:
                            ef.seek(max(0, size - 1))
                            last_byte = ef.read(1)
                            if last_byte != b"\n":
                                needs_prefix_newline = True

                prefix = b"\n" if needs_prefix_newline else b""
                event_bytes = prefix + (json.dumps(bound_event, ensure_ascii=False) + "\n").encode("utf-8")
                with origin_events_path.open("ab") as ef:
                    ef.write(event_bytes)

            return {
                "returncode": p_run.returncode,
                "stdout": p_run.stdout,
                "stderr": p_run.stderr,
                "report": rep_data,
                "ga_output": "",
                "g0_output": "",
            }

        else:
            # 1. GA (check_assignments.py)
            ga_script = tmp_repo / "harness" / "check_assignments.py"
            if not ga_script.is_file():
                raise ValueError("GA 검사 스크립트(harness/check_assignments.py)가 없습니다")

            p_ga = subprocess.run(
                [sys.executable, "-B", str(ga_script)],
                cwd=tmp_repo,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
            ga_output = (p_ga.stdout + p_ga.stderr).strip()
            if p_ga.returncode != 0:
                raise ValueError(f"GA (역할 배정 제약) 검사 실패:\n{ga_output}")

            # 2. G0 (check_protected_paths.py)
            g0_script = tmp_repo / "harness" / "check_protected_paths.py"
            if not g0_script.is_file():
                raise ValueError("G0 검사 스크립트(harness/check_protected_paths.py)가 없습니다")

            p_g0 = subprocess.run(
                [sys.executable, "-B", str(g0_script), "--base", base_sha, "--role", role],
                cwd=tmp_repo,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
            g0_output = (p_g0.stdout + p_g0.stderr).strip()
            if p_g0.returncode != 0:
                raise ValueError(f"G0 (보호 경로 · 우회 패턴) 검사 실패:\n{g0_output}")

            snap_after = _snapshot_directory(ws_path)
            if snap_after != snap_before:
                raise ValueError("게이트 실행 중 워커 작업 공간 변경 감지")

            return {
                "ga_passed": True,
                "ga_output": ga_output,
                "g0_passed": True,
                "g0_output": g0_output,
                "returncode": 0,
            }
    finally:
        _robust_rmtree(tmp_dir)


def _run_trusted_ga_in_copy(root: Path) -> None:
    """공유 락 안에서 신뢰 복사본을 통해 GA (check_assignments.py) 를 사전 실행한다.
    원본 작업 트리에 출력이나 __pycache__ 를 만들지 않는다.
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="h63-ga-"))
    try:
        tmp_repo = tmp_dir / "repo"
        _git(root, "clone", "--quiet", "--no-hardlinks", str(root), str(tmp_repo))
        for d in ("harness", "config", "templates", "adapters"):
            src_d = root / d
            dst_d = tmp_repo / d
            if src_d.is_dir():
                shutil.rmtree(dst_d, ignore_errors=True)
                shutil.copytree(src_d, dst_d)

        ga_script = tmp_repo / "harness" / "check_assignments.py"
        if not ga_script.is_file():
            raise ValueError("GA 검사 스크립트(harness/check_assignments.py)가 없습니다")

        p_ga = subprocess.run(
            [sys.executable, "-B", str(ga_script)],
            cwd=tmp_repo,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        if p_ga.returncode != 0:
            raise ValueError(
                f"GA (역할 배정 제약) 사전 검사 실패:\n{(p_ga.stdout + p_ga.stderr).strip()}"
            )
    finally:
        _robust_rmtree(tmp_dir)


def _validate_start_common(
    root: Path,
    task: dict,
    *,
    is_manual: bool,
    runner_id: str | None = None,
) -> dict[str, Any]:
    """start 및 start_manual 의 공통 사전 검증 (작업 명세, 역할, 원본 clean/HEAD, 상태, 승인, 배정, 계열, GA)."""
    # 1. 태스크 필수 키 검증 (pre-launch)
    for key in ("wo", "slice", "role", "base", "approval_ref"):
        if not task.get(key):
            raise ValueError(f"작업 명세에 필수 키 '{key}' 가 누락되었거나 비어 있습니다.")

    for key in ("wo", "slice"):
        val = str(task[key])
        if "/" in val or "\\" in val or ".." in val:
            raise ValueError(f"경로 탈출 감지 ({key}): '{val}'")

    role = task["role"]
    base = task["base"]

    # 2. 역할 및 기준 커밋 사전 검증 (pre-launch)
    roles_file = root / "config" / "roles.toml"
    if not roles_file.is_file():
        raise ValueError(f"roles.toml 파일이 없습니다: {roles_file}")

    roles_data = tomllib.loads(roles_file.read_text(encoding="utf-8"))
    defined_roles = roles_data.get("roles", {})
    if role not in defined_roles:
        raise ValueError(
            f"지정된 역할 '{role}' 은 config/roles.toml 에 정의된 작업 역할이 아닙니다."
        )
    if role == "lead":
        raise ValueError("lead 역할은 실행 계약으로 위임할 수 없습니다.")

    # 원본 작업 트리 clean 검사
    origin_status = _git(root, "status", "--porcelain")
    if origin_status != "":
        raise ValueError(f"원본 작업 트리가 깨끗하지 않습니다 (dirty origin):\n{origin_status}")

    # 원본 HEAD 확인 및 base 대조
    head_sha = _git(root, "rev-parse", "HEAD")
    try:
        base_verify = _git(root, "rev-parse", "--verify", f"{base}^{{commit}}")
    except Exception:
        raise ValueError(f"기준 커밋을 찾을 수 없습니다: {base}")

    if base != head_sha or base_verify != head_sha:
        raise ValueError(
            f"기준 커밋 불일치: task base '{base}' != origin HEAD '{head_sha}'"
        )

    # 활성 상태 파일(config/role-state.json) 확인
    state_file = root / "config" / "role-state.json"
    if not state_file.is_file():
        raise ValueError("활성 역할 상태 파일(config/role-state.json)이 없습니다.")

    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except Exception as se:
        raise ValueError(f"손상된 역할 상태 파일 감지: {se}")

    if state.get("phase") != "ready":
        raise ValueError(f"역할 상태 phase 가 'ready' 가 아닙니다: '{state.get('phase')}'")

    # 상태 및 생성 문서 정합성 검사
    errs = role_state.internal_check(root, state)
    if errs:
        raise ValueError(f"활성 역할 상태/문서 불일치: {'; '.join(errs)}")

    # 3. 승인 객체 검증
    app = _read_execution_approval(root, task["approval_ref"], task)

    # runner 식별자 확인
    if is_manual:
        if app.get("runner") != "manual":
            raise ValueError(
                f"수동 실행 승인 runner가 'manual'이 아닙니다: '{app.get('runner')}'"
            )
    else:
        if not runner_id or runner_id != app.get("runner") or app.get("runner") == "manual":
            raise ValueError(
                f"러너 불일치: runner.id '{runner_id}' != 승인 runner '{app.get('runner')}'"
            )

    # 확정 배정 확인 (config/assignments.toml)
    assignments_file = root / "config" / "assignments.toml"
    if not assignments_file.is_file():
        raise ValueError("config/assignments.toml 파일이 없습니다.")

    asg_doc = tomllib.loads(assignments_file.read_text(encoding="utf-8")).get("slice", {})
    if task["slice"] not in asg_doc:
        raise ValueError(f"슬라이스 '{task['slice']}' 배정이 config/assignments.toml 에 없습니다.")
    slice_cfg = asg_doc[task["slice"]]
    if role not in slice_cfg:
        raise ValueError(f"역할 '{role}' 배정이 슬라이스 '{task['slice']}' 에 없습니다.")
    assigned_agent = slice_cfg[role]
    if assigned_agent != app.get("agent"):
        raise ValueError(
            f"배정 에이전트 불일치: slice 배정 '{assigned_agent}' != 승인 에이전트 '{app.get('agent')}'"
        )

    # 등록 계열 확인 (config/agents.toml)
    agents_file = root / "config" / "agents.toml"
    if not agents_file.is_file():
        raise ValueError("config/agents.toml 파일이 없습니다.")

    ag_doc = tomllib.loads(agents_file.read_text(encoding="utf-8")).get("agents", {})
    agent_name = app.get("agent")
    if agent_name not in ag_doc:
        raise ValueError(f"에이전트 '{agent_name}' 가 config/agents.toml 에 등록되어 있지 않습니다.")

    reg_family = ag_doc[agent_name].get("family")
    if not reg_family:
        raise ValueError(f"에이전트 '{agent_name}' 에 등록된 family 정보가 없습니다.")
    if reg_family != app.get("family"):
        raise ValueError(
            f"에이전트 계열 불일치: 등록 '{reg_family}' != 승인 '{app.get('family')}'"
        )

    # 모델 바인딩 확인 (상태의 model_bindings 와 승인 일치 대조)
    model_bindings = state.get("model_bindings", {})
    if agent_name not in model_bindings:
        raise ValueError(f"에이전트 '{agent_name}' 의 모델 바인딩이 역할 상태에 없습니다.")
    binding = model_bindings[agent_name]
    if binding.get("model") != app.get("model") or binding.get("family") != app.get("family"):
        raise ValueError(
            f"모델 바인딩 불일치: 상태 {binding} != 승인 (model={app.get('model')}, family={app.get('family')})"
        )

    # 신뢰 복사본에서 GA 실행하여 슬라이스 분리 위반 사전 차단
    _run_trusted_ga_in_copy(root)

    # implementer의 경우 step delegate 검증 필수 (건너뛰기 금지)
    if role == "implementer":
        step_script = root / "harness" / "step.py"
        slices_file = root / "config" / "slices.toml"
        if not step_script.is_file():
            raise ValueError("구현자(implementer) 실행에는 harness/step.py 가 필수입니다.")
        if not slices_file.is_file():
            raise ValueError("구현자(implementer) 실행에는 config/slices.toml 이 필수입니다.")

        sl_doc = tomllib.loads(slices_file.read_text(encoding="utf-8")).get("slice", {})
        if task["slice"] not in sl_doc:
            raise ValueError(f"구현자 슬라이스 정의가 config/slices.toml 에 없습니다: {task['slice']}")

        p_step = subprocess.run(
            [sys.executable, "-B", str(step_script), "--slice", task["slice"], "--check", "delegate"],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        if p_step.returncode != 0:
            raise ValueError(f"step delegate 검증 실패:\n{(p_step.stdout + p_step.stderr).strip()}")

    # 원본 동시변경 보호용 별도 시작 snapshot 획득
    origin_start_snapshot = _snapshot_directory(root)

    # 원본 신뢰 디렉터리 해시 고정
    trusted_sources_hash = {
        d: _hash_tree(root / d)
        for d in ("harness", "config", "adapters", "templates")
        if (root / d).is_dir()
    }

    origin_roles_hash = hashlib.sha256(roles_file.read_bytes()).hexdigest()
    registered_agents_hash = hashlib.sha256(agents_file.read_bytes()).hexdigest()
    assignments_hash = hashlib.sha256(assignments_file.read_bytes()).hexdigest()
    origin_state_raw_hash = hashlib.sha256(state_file.read_bytes()).hexdigest()

    return {
        "role": role,
        "base_verify": base_verify,
        "head_sha": head_sha,
        "app": app,
        "state": state,
        "origin_roles_hash": origin_roles_hash,
        "registered_agents_hash": registered_agents_hash,
        "assignments_hash": assignments_hash,
        "origin_state_raw_hash": origin_state_raw_hash,
        "trusted_sources_hash": trusted_sources_hash,
        "origin_start_snapshot": origin_start_snapshot,
    }


def start(root: Path, task: dict, runner: Any) -> str:
    """새 실행 계약을 생성하고 격리 러너를 시작한다."""
    root = Path(root).resolve()
    runner_id = getattr(runner, "id", None)

    # 공유 락 획득 아래에서 원본 clean, 상태, 배정, 모델 바인딩 검증 및 실행 예약
    with role_state.role_lock(root):
        ctx = _validate_start_common(root, task, is_manual=False, runner_id=runner_id)

        role = ctx["role"]
        app = ctx["app"]
        state = ctx["state"]
        base_verify = ctx["base_verify"]
        head_sha = ctx["head_sha"]

        # 4. 실행 예약 및 계약 생성
        run_id = f"run_{int(time.time()*1000)}_{task['slice']}_{role}"
        run_dir = root / "reports" / "executions" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        contract = {
            "schema": 1,
            "run_id": run_id,
            "wo": task["wo"],
            "slice": task["slice"],
            "role": role,
            "base": base_verify,
            "approval_ref": task["approval_ref"],
            "agent": app.get("agent"),
            "model": app.get("model"),
            "family": app.get("family"),
            "runner": runner_id,
            "task_paths": app.get("paths", []),
            "status": "running",
            "accepted": False,
            "mode": "strict",
            "origin_head": head_sha,
            "origin_root": str(root),
            "origin_roles_hash": ctx["origin_roles_hash"],
            "registered_agents_hash": ctx["registered_agents_hash"],
            "assignments_hash": ctx["assignments_hash"],
            "origin_state_raw_hash": ctx["origin_state_raw_hash"],
            "trusted_sources_hash": ctx["trusted_sources_hash"],
            "snapshot_mode": "content_sha256",
            "generation": state.get("generation"),
            "config_version": state.get("config_version"),
            "documents_hash": state.get("documents", {}),
            "approval_blob_hash": state.get("approval_blob_hash"),
            "origin_start_snapshot": ctx["origin_start_snapshot"],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        contract_file = run_dir / "contract.json"
        status_file = run_dir / "status.json"

        status_data = {"run_id": run_id, "status": "running", "accepted": False, "mode": "strict"}
        contract_file.write_text(json.dumps(contract, indent=2, ensure_ascii=False), encoding="utf-8")
        status_file.write_text(json.dumps(status_data, indent=2, ensure_ascii=False), encoding="utf-8")

        # 5. 러너 launch 실행 및 관측치 검증 (post-launch)
        obs = None
        try:
            obs = runner.launch(contract)
        except Exception as e:
            stop_err = None
            try:
                runner.stop(contract)
            except Exception as se:
                stop_err = se
            status_data["status"] = "failed"
            status_data["error"] = f"launch failed: {e}"
            if stop_err:
                status_data["stop_error"] = str(stop_err)
            status_file.write_text(json.dumps(status_data, indent=2, ensure_ascii=False), encoding="utf-8")
            contract["status"] = "failed"
            contract_file.write_text(json.dumps(contract, indent=2, ensure_ascii=False), encoding="utf-8")
            raise

        # 관측치(observation) 검증
        obs_err = _validate_observation(obs, contract, is_completion=False)
        if obs_err:
            stop_err = None
            try:
                runner.stop(contract)
            except Exception as se:
                stop_err = se
            status_data["status"] = "failed"
            status_data["error"] = obs_err
            if stop_err:
                status_data["stop_error"] = str(stop_err)
            status_file.write_text(json.dumps(status_data, indent=2, ensure_ascii=False), encoding="utf-8")
            contract["status"] = "failed"
            contract_file.write_text(json.dumps(contract, indent=2, ensure_ascii=False), encoding="utf-8")
            raise ValueError(obs_err)

        # 워커 작업 공간 유효성, HEAD==base 및 Git clean 검증 후 실제 시작 파일 고정
        ws_path = Path(obs["workspace"]).resolve()
        try:
            worker_head = _git(ws_path, "rev-parse", "HEAD")
            if worker_head != base_verify:
                raise ValueError(f"워커 HEAD 불일치: {worker_head} != {base_verify}")
            worker_clean = _git(ws_path, "status", "--porcelain")
            if worker_clean != "":
                raise ValueError(f"워커 작업 트리가 시작 시점에 깨끗하지 않습니다: {worker_clean}")
            worker_start_snapshot = _snapshot_directory(ws_path)
        except Exception as we:
            stop_err = None
            try:
                runner.stop(contract)
            except Exception as se:
                stop_err = se
            status_data["status"] = "failed"
            status_data["error"] = str(we)
            if stop_err:
                status_data["stop_error"] = str(stop_err)
            status_file.write_text(json.dumps(status_data, indent=2, ensure_ascii=False), encoding="utf-8")
            contract["status"] = "failed"
            contract_file.write_text(json.dumps(contract, indent=2, ensure_ascii=False), encoding="utf-8")
            raise

        # 관측 성공 및 시작 스냅샷 영구 보존
        contract["workspace"] = str(ws_path)
        contract["session_id"] = obs.get("session_id")
        contract["permission"] = obs.get("permission")
        contract["source"] = obs.get("source")
        contract["worker_start_snapshot"] = worker_start_snapshot
        contract_file.write_text(json.dumps(contract, indent=2, ensure_ascii=False), encoding="utf-8")

    return run_id


def start_manual(root: Path, task: dict, workspace: Path | str, note: str) -> str:
    """수동 실행 경로를 위한 실행 계약을 등록한다. (외부 워커/Orca 실행 없음)"""
    root = Path(root).resolve()

    if not note or not isinstance(note, str) or not note.strip():
        raise ValueError("수동 실행 시작 note는 비어 있거나 공백만으로 구성될 수 없습니다.")

    if not workspace:
        raise ValueError("작업 공간(workspace) 경로가 지정되지 않았습니다.")

    ws_path = Path(workspace).resolve()
    if not ws_path.is_dir():
        raise ValueError(f"작업 공간 디렉터리가 실제로 존재하지 않습니다: {ws_path}")
    if ws_path == root:
        raise ValueError(f"작업 공간(workspace)이 원본(root)과 분리되지 않았습니다: {ws_path}")

    # 중첩된 작업 공간(nested clone) 거부 (다단계 중첩 포함)
    if root in ws_path.parents:
        raise ValueError(f"작업 공간(workspace)이 원본(root) 내부에 중첩되어 있습니다: {ws_path}")
    if ws_path in root.parents:
        raise ValueError(f"원본(root)이 작업 공간(workspace) 내부에 중첩되어 있습니다: {root}")

    is_child = False
    try:
        is_child = ws_path.is_relative_to(root)
    except Exception:
        is_child = False
    if is_child:
        raise ValueError(f"작업 공간(workspace)이 원본(root) 내부에 중첩되어 있습니다: {ws_path}")

    is_parent = False
    try:
        is_parent = root.is_relative_to(ws_path)
    except Exception:
        is_parent = False
    if is_parent:
        raise ValueError(f"원본(root)이 작업 공간(workspace) 내부에 중첩되어 있습니다: {root}")

    with role_state.role_lock(root):
        ctx = _validate_start_common(root, task, is_manual=True)

        role = ctx["role"]
        app = ctx["app"]
        state = ctx["state"]
        base_verify = ctx["base_verify"]
        head_sha = ctx["head_sha"]

        # 워커 작업 공간의 HEAD 및 clean 검증 (프로필 지정 여부에 따라 분기)
        is_filtered = "workspace_profile" in task

        if is_filtered:
            # 계약 2: [계획] 프로필 및 설정 값 검증 후 build_plan
            profile = task.get("workspace_profile")
            if not isinstance(profile, str) or not profile.strip():
                raise ValueError(f"[계획] workspace_profile 값이 유효하지 않습니다: {profile!r}")

            workspace_config = task.get("workspace_config", "config/workspace.toml")
            if not isinstance(workspace_config, str) or not is_valid_exact_path(workspace_config):
                raise ValueError(f"[계획] workspace_config 경로가 유효하지 않습니다: {workspace_config!r}")

            try:
                plan = build_plan(root, base_verify, profile, workspace_config)
            except ValueError as e:
                raise ValueError(f"[계획] {e}") from e

            # 계약 3: 워커 작업 공간 검사 순서 (a -> b -> c -> d -> e -> f -> g)
            # 3a. [격리:git-dir]
            expected_git_dir = (ws_path / ".git").resolve()
            if not (ws_path / ".git").is_dir():
                raise ValueError(f"[격리:git-dir] 워커 .git 이 디렉터리가 아닙니다: {ws_path / '.git'}")
            try:
                p_abs = _git(ws_path, "rev-parse", "--absolute-git-dir")
                p_common = _git(ws_path, "rev-parse", "--git-common-dir")
            except Exception as e:
                raise ValueError(f"[격리:git-dir] git rev-parse 실행 실패: {e}") from e

            abs_git_dir = Path(p_abs).resolve()
            p_common_path = Path(p_common)
            common_git_dir = p_common_path.resolve() if p_common_path.is_absolute() else (ws_path / p_common_path).resolve()
            if abs_git_dir != expected_git_dir or common_git_dir != expected_git_dir:
                raise ValueError(
                    f"[격리:git-dir] 워커 git-dir 또는 common-dir 가 독립 .git 디렉터리와 일치하지 않습니다 "
                    f"({abs_git_dir}, {common_git_dir} != {expected_git_dir})"
                )

            # 3b. [격리:alternates]
            git_dir = ws_path / ".git"
            if (git_dir / "objects" / "info" / "alternates").exists() or (git_dir / "objects" / "info" / "http-alternates").exists():
                raise ValueError("[격리:alternates] alternates 또는 http-alternates 파일이 감지되었습니다.")

            # 3c. [격리:메타]
            if (git_dir / "info" / "grafts").exists():
                raise ValueError("[격리:메타] info/grafts 메타 파일이 감지되었습니다.")
            if (git_dir / "shallow").exists():
                raise ValueError("[격리:메타] shallow 메타 파일이 감지되었습니다.")
            if (git_dir / "modules").exists():
                raise ValueError("[격리:메타] modules 메타 디렉터리가 감지되었습니다.")
            if (git_dir / "refs" / "replace").exists():
                raise ValueError("[격리:메타] refs/replace/ 참조 디렉터리가 감지되었습니다.")
            p_rep = subprocess.run(["git", "for-each-ref", "refs/replace/"], cwd=ws_path, capture_output=True, text=True)
            if p_rep.returncode != 0 or p_rep.stdout.strip():
                raise ValueError(f"[격리:메타] refs/replace/ 참조가 감지되었습니다: {p_rep.stdout.strip() or p_rep.stderr.strip()}")

            # 3d. [격리:parent]
            commit_raw = _git_bytes(ws_path, "cat-file", "commit", "HEAD")
            header = commit_raw.split(b"\n\n", 1)[0]
            for line in header.split(b"\n"):
                if line.startswith(b"parent "):
                    raise ValueError("[격리:parent] 원시 HEAD 커밋에 parent 헤더가 존재합니다.")

            # 3e. [격리:tree]
            tree_out = _git_bytes(ws_path, "ls-tree", "-r", "-z", "HEAD")
            worker_entries = []
            for item in tree_out.split(b"\0"):
                if not item:
                    continue
                meta, sep, path_b = item.partition(b"\t")
                if not sep:
                    continue
                parts = meta.split(b" ")
                if len(parts) != 3:
                    continue
                mode_b, type_b, blob_b = parts
                if type_b != b"blob":
                    raise ValueError(f"[격리:tree] 워커 트리에 blob 이 아닌 항목이 존재합니다: {meta.decode('utf-8', 'replace')}")
                worker_entries.append((path_b.decode("utf-8", "surrogateescape"), mode_b.decode("utf-8"), blob_b.decode("utf-8")))

            plan_entries = [(f["path"], f["mode"], f["blob"]) for f in plan["files"]]
            if sorted(worker_entries) != sorted(plan_entries):
                raise ValueError("[격리:tree] 워커 트리가 계획 파일 목록(경로, 모드, blob)과 일치하지 않습니다.")

            # 3f. 작업 트리 clean
            worker_clean = _git(ws_path, "status", "--porcelain")
            if worker_clean != "":
                raise ValueError(f"워커 작업 트리가 시작 시점에 깨끗하지 않습니다: {worker_clean}")

            # 3g. [격리:objects]
            worker_head = _git(ws_path, "rev-parse", "HEAD")
            worker_root_tree = _git(ws_path, "rev-parse", "HEAD^{tree}")

            ls_trees = _git(ws_path, "ls-tree", "-r", "-t", "HEAD")
            tree_oids = set()
            for line in ls_trees.splitlines():
                if not line.strip():
                    continue
                meta, _, _ = line.partition("\t")
                parts = meta.split()
                if len(parts) >= 3 and parts[1] == "tree":
                    tree_oids.add(parts[2])

            plan_blob_oids = {f["blob"] for f in plan["files"]}
            allowed_oids = {worker_head, worker_root_tree} | tree_oids | plan_blob_oids

            all_objs_out = _git(ws_path, "cat-file", "--batch-all-objects", "--batch-check")
            actual_oids = set()
            for line in all_objs_out.splitlines():
                if not line.strip():
                    continue
                parts = line.split()
                if parts:
                    actual_oids.add(parts[0])

            if not actual_oids.issubset(allowed_oids):
                extra_oids = actual_oids - allowed_oids
                raise ValueError(f"[격리:objects] 워커 저장소에 허용되지 않은 객체가 존재합니다: {extra_oids}")

        else:
            # 프로필 없음 (exact 모드): 기존 검사 그대로 유지
            worker_head = _git(ws_path, "rev-parse", "HEAD")
            if worker_head != base_verify:
                raise ValueError(f"워커 HEAD 불일치: {worker_head} != {base_verify}")
            worker_clean = _git(ws_path, "status", "--porcelain")
            if worker_clean != "":
                raise ValueError(f"워커 작업 트리가 시작 시점에 깨끗하지 않습니다: {worker_clean}")

        worker_start_snapshot = _snapshot_directory(ws_path)

        run_id = f"manual_{int(time.time()*1000)}_{task['slice']}_{role}"
        run_dir = root / "reports" / "executions" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        contract = {
            "schema": 1,
            "run_id": run_id,
            "wo": task["wo"],
            "slice": task["slice"],
            "role": role,
            "base": base_verify,
            "base_mode": "filtered" if is_filtered else "exact",
            "approval_ref": task["approval_ref"],
            "agent": app.get("agent"),
            "model": app.get("model"),
            "family": app.get("family"),
            "runner": "manual",
            "task_paths": app.get("paths", []),
            "status": "running",
            "accepted": False,
            "mode": "manual",
            "runtime_verification": "unverified",
            "observed_model": None,
            "permission": None,
            "start_note": note,
            "origin_head": head_sha,
            "origin_root": str(root),
            "origin_roles_hash": ctx["origin_roles_hash"],
            "registered_agents_hash": ctx["registered_agents_hash"],
            "assignments_hash": ctx["assignments_hash"],
            "origin_state_raw_hash": ctx["origin_state_raw_hash"],
            "trusted_sources_hash": ctx["trusted_sources_hash"],
            "snapshot_mode": "content_sha256",
            "generation": state.get("generation"),
            "config_version": state.get("config_version"),
            "documents_hash": state.get("documents", {}),
            "approval_blob_hash": state.get("approval_blob_hash"),
            "origin_start_snapshot": ctx["origin_start_snapshot"],
            "workspace": str(ws_path),
            "worker_start_snapshot": worker_start_snapshot,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        if is_filtered:
            contract["workspace_profile"] = profile
            contract["workspace_config"] = workspace_config
            contract["policy_blob"] = plan["policy_blob"]
            contract["worker_head"] = worker_head
            contract["worker_tree"] = worker_root_tree
            contract["plan_paths"] = [f["path"] for f in plan["files"]]

        contract_file = run_dir / "contract.json"
        status_file = run_dir / "status.json"

        status_data = {"run_id": run_id, "status": "running", "accepted": False, "mode": "manual"}
        contract_file.write_text(json.dumps(contract, indent=2, ensure_ascii=False), encoding="utf-8")
        status_file.write_text(json.dumps(status_data, indent=2, ensure_ascii=False), encoding="utf-8")

    return run_id


def _validate_origin_drift_with_handover(root: Path, contract: dict) -> None:
    """원본 드리프트 대조 또는 승인된 역할 인계(handover) 검증."""
    handover_ok = False
    try:
        import role_handover
        handover_ok = role_handover.validate_handover(root, contract)
    except (ImportError, AttributeError):
        handover_ok = False

    if handover_ok:
        return

    # 기존 원본 드리프트 엄격 검증
    cur_head = _git(root, "rev-parse", "HEAD")
    if cur_head != contract["origin_head"]:
        raise ValueError(f"원본 HEAD 드리프트 감지: {cur_head} != {contract['origin_head']}")

    roles_file = root / "config" / "roles.toml"
    if not roles_file.is_file():
        raise ValueError("원본 config/roles.toml 이 없습니다")
    cur_roles_hash = hashlib.sha256(roles_file.read_bytes()).hexdigest()
    if cur_roles_hash != contract["origin_roles_hash"]:
        raise ValueError("원본 config/roles.toml 정책 드리프트 감지")

    state_file = root / "config" / "role-state.json"
    if not state_file.is_file():
        raise ValueError("원본 config/role-state.json 이 없습니다 (state deleted/missing)")

    cur_state_raw = hashlib.sha256(state_file.read_bytes()).hexdigest()
    if contract.get("origin_state_raw_hash") and cur_state_raw != contract.get("origin_state_raw_hash"):
        raise ValueError("역할 상태 파일 원시 해시 불일치 (state drift 감지)")

    try:
        cur_state = json.loads(state_file.read_text(encoding="utf-8"))
    except Exception as e:
        raise ValueError(f"손상된 역할 상태 파일: {e}")

    if cur_state.get("phase") != "ready":
        raise ValueError(f"역할 상태 phase 가 'ready' 가 아닙니다: '{cur_state.get('phase')}'")
    if cur_state.get("generation") != contract.get("generation"):
        raise ValueError("역할 상태 세대(generation) 불일치")
    if cur_state.get("config_version") != contract.get("config_version"):
        raise ValueError("역할 상태 설정 버전(config_version) 불일치")
    if cur_state.get("approval_blob_hash") != contract.get("approval_blob_hash"):
        raise ValueError("역할 상태 승인 blob 해시 불일치")

    errs = role_state.internal_check(root, cur_state)
    if errs:
        raise ValueError(f"역할 상태 정합성 검사 실패: {'; '.join(errs)}")

    agents_file = root / "config" / "agents.toml"
    if not agents_file.is_file():
        raise ValueError("원본 config/agents.toml 이 없습니다 (agent registry 없음 거부)")

    ag_hash = hashlib.sha256(agents_file.read_bytes()).hexdigest()
    if ag_hash != contract.get("registered_agents_hash"):
        raise ValueError("원본 config/agents.toml 드리프트 감지")

    assignments_file = root / "config" / "assignments.toml"
    if not assignments_file.is_file():
        raise ValueError("원본 config/assignments.toml 이 없습니다")
    asg_hash = hashlib.sha256(assignments_file.read_bytes()).hexdigest()
    if asg_hash != contract.get("assignments_hash"):
        raise ValueError("원본 config/assignments.toml 배정 드리프트 감지")

    # 신뢰 디렉터리 변조 대조
    saved_trees = contract.get("trusted_sources_hash", {})
    for d in ("harness", "config", "adapters", "templates"):
        cur_tree = _hash_tree(root / d)
        if d in saved_trees and cur_tree != saved_trees[d]:
            raise ValueError(f"신뢰 원본 디렉터리 변조 감지 ({d})")


def _validate_origin_target_snapshot(root: Path, file_keys: Any, orig_start_snap: dict[str, Any]) -> None:
    """원본 파일 대상이 시작 시점 스냅샷과 동일한지 대조한다 (동시 원본 수정 또는 인계 충돌 보존)."""
    for f in file_keys:
        target_p = root / f
        if f in orig_start_snap:
            if not target_p.is_file():
                raise ValueError(f"대상 파일이 원본에서 삭제되었습니다: {f}")
            target_hash = hashlib.sha256(target_p.read_bytes()).hexdigest()
            target_st = target_p.stat()
            if target_hash != orig_start_snap[f]["sha256"]:
                raise ValueError(f"대상 파일에 대한 원본 변경 감지 (동시 사용자 수정 또는 인계 충돌 보존): {f}")
            if "mode" in orig_start_snap[f] and stat.S_IMODE(target_st.st_mode) != orig_start_snap[f]["mode"]:
                raise ValueError(f"대상 파일 모드에 대한 원본 변경 감지: {f}")
        else:
            if target_p.exists():
                raise ValueError(f"대상 파일이 원본에 이미 존재합니다 (동시 사용자 수정 보존): {f}")


def _validate_check_common(
    root: Path,
    contract: dict,
    ws_path: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """check 및 check_manual 의 공통 파일/정책 검증 (원본 드리프트, 워커 스냅샷 diff, 역할/승인 범위, GA/G0, digest 대조)."""
    # 1. 원본 드리프트 검사 (또는 승인된 handover 검증)
    _validate_origin_drift_with_handover(root, contract)

    # 워커 작업 공간 스냅샷 비교 (시작 스냅샷과 현재 파일 비교)
    if not ws_path.is_dir():
        raise ValueError(f"워커 작업 디렉터리가 존재하지 않습니다: {ws_path}")

    start_snap = contract.get("worker_start_snapshot", {})
    cur_snap = _snapshot_directory(ws_path)
    changed_files = _diff_snapshots(start_snap, cur_snap)

    # 계약 5: [미제공 덮어쓰기] 검사 (보호 경로 및 origin_target_snapshot 검사보다 앞에 배치)
    if contract.get("base_mode") == "filtered":
        base_tree_out = _git_bytes(root, "ls-tree", "-r", "-z", "--name-only", contract["base"])
        base_tree_paths_cf = {p.casefold() for p in base_tree_out.decode("utf-8", "replace").split("\0") if p}
        plan_cf = {p.casefold() for p in contract.get("plan_paths", [])}
        for f, action in changed_files.items():
            # 시작 전 무시 파일로 숨겨 둔 미제공 경로는 modify 로 보일 수 있다 — 계획 밖 base 경로면 종류와 무관 (마일스톤 검토 R01)
            if f.casefold() in base_tree_paths_cf and f.casefold() not in plan_cf:
                raise ValueError(f"[미제공 덮어쓰기] 원본 base 트리에 이미 존재하는 미제공 경로가 새로 추가(add)되었습니다: {f}")

    # 원본 파일 대상 변경 감지 (동시 수정 및 H 인계로 변경된 원본 문서 덮어쓰기 방지)
    _validate_origin_target_snapshot(root, changed_files.keys(), contract.get("origin_start_snapshot", {}))

    # 역할 규칙 및 작업 승인 경로 대조
    roles_file = root / "config" / "roles.toml"
    if not roles_file.is_file():
        raise ValueError("원본 config/roles.toml 이 없습니다")
    roles_data = tomllib.loads(roles_file.read_text(encoding="utf-8"))
    role_rules = roles_data.get("roles", {}).get(contract["role"], {})
    role_allow = role_rules.get("allow", [])
    role_deny = role_rules.get("deny", [])
    task_paths = contract.get("task_paths", [])

    for f, action in changed_files.items():
        if f.startswith("config/") or f.startswith("templates/") or f.startswith("harness/") or f.startswith("adapters/"):
            raise ValueError(f"보호 경로 무단 변경 거부: {f}")
        if _match_any(f, role_deny):
            raise ValueError(f"역할 deny 정책 위반: {f} (role={contract['role']})")
        if not _match_any(f, role_allow):
            raise ValueError(f"역할 allow 정책 밖의 파일 변경: {f} (role={contract['role']})")
        if not _match_any(f, task_paths):
            raise ValueError(f"작업 승인 범위(task paths) 밖의 파일 변경: {f}")

        # HANDOFF 상태줄 정규화 비교
        if f == "docs/HANDOFF.md" or f.endswith("HANDOFF.md"):
            from adapters.tools.check_handoff_state import extract_state_lines
            base_b = _git_bytes(root, "show", f"{contract['base']}:{f}") if f in start_snap else b""
            cur_b = (ws_path / f).read_bytes() if (ws_path / f).is_file() else b""
            base_lines = extract_state_lines(base_b.decode("utf-8", "replace"))
            cur_lines = extract_state_lines(cur_b.decode("utf-8", "replace"))
            if base_lines != cur_lines:
                raise ValueError(f"HANDOFF 상태줄 변경 거부 (doc_editor 불변 위반): {f}")

    # trusted GA/G0 임시 검사 실행
    gate_res = _run_trusted_gates(root, contract["base"], contract["role"], changed_files, ws_path)

    # 검증된 변경 집합 및 해시 기록
    verified_changes: dict[str, Any] = {}
    for f, action in changed_files.items():
        if action == "delete":
            verified_changes[f] = {"action": "delete", "sha256": None, "size": 0, "mode": None, "type": None}
        else:
            info = cur_snap[f]
            verified_changes[f] = {
                "action": action,
                "sha256": info["sha256"],
                "size": info["size"],
                "mode": info.get("mode"),
                "type": info.get("type"),
            }

    if contract.get("role") == "implementer":
        run_dir = root / "reports" / "executions" / contract["run_id"]
        gate_rep = run_dir / "gate_report.json"
        if gate_rep.is_file():
            try:
                rep_data = json.loads(gate_rep.read_text(encoding="utf-8"))
                rep_digest = (rep_data.get("evidence", {}) or {}).get("candidate_digest") or rep_data.get("candidate_digest")
                if rep_digest:
                    cur_digest = hashlib.sha256(json.dumps(cur_snap, sort_keys=True).encode("utf-8")).hexdigest()
                    if cur_digest != rep_digest:
                        raise ValueError("게이트 검사 이후 후보 변경 감지 (후보 digest 불일치)")
            except ValueError:
                raise
            except Exception:
                pass

    return verified_changes, cur_snap, gate_res


def check(root: Path, run_id: str, runner: Any) -> dict:
    """완료된 실행의 산출물과 허용 변경 집합을 검증하고 해시를 고정한다."""
    root = Path(root).resolve()

    if "/" in run_id or "\\" in run_id or ".." in run_id:
        raise ValueError(f"실행 ID 경로 탈출 감지: '{run_id}'")

    run_dir = root / "reports" / "executions" / run_id
    contract_file = run_dir / "contract.json"
    if not contract_file.is_file():
        raise ValueError(f"실행 계약 파일이 없습니다: {contract_file}")

    contract = json.loads(contract_file.read_text(encoding="utf-8"))
    if contract.get("accepted"):
        raise ValueError(f"실행 {run_id} 은 이미 채택(accepted)되었습니다")

    if contract.get("mode") == "manual":
        raise ValueError("수동 실행(mode=manual)은 strict check로 검증할 수 없습니다.")

    # 러너 id 재대조
    runner_id = getattr(runner, "id", None)
    if not runner_id or runner_id != contract.get("runner"):
        raise ValueError(f"러너 불일치: runner.id '{runner_id}' != 계약 runner '{contract.get('runner')}'")

    # 러너 상태 재관측 및 완료 시점 검증
    obs = runner.observe(contract)
    obs_err = _validate_observation(obs, contract, is_completion=True)
    if obs_err:
        raise ValueError(obs_err)

    ws_path = Path(obs["workspace"]).resolve()
    verified_changes, cur_snap, gate_res = _validate_check_common(root, contract, ws_path)

    contract["verified_changes"] = verified_changes
    contract["verified_worker_snapshot"] = cur_snap
    contract["ga_result"] = gate_res["ga_output"]
    contract["g0_result"] = gate_res["g0_output"]
    contract["verified_at"] = datetime.now(timezone.utc).isoformat()
    contract_file.write_text(json.dumps(contract, indent=2, ensure_ascii=False), encoding="utf-8")

    return {
        "run_id": run_id,
        "status": "checked",
        "verified_changes": verified_changes,
    }


def check_manual(root: Path, run_id: str, completion_note: str) -> dict:
    """수동 실행의 산출물과 파일 정책을 검증한다 (정적 검증 passed, 런타임 관측 unverified)."""
    root = Path(root).resolve()

    if not completion_note or not isinstance(completion_note, str) or not completion_note.strip():
        raise ValueError("수동 검증 completion_note는 비어 있거나 공백만으로 구성될 수 없습니다.")

    if "/" in run_id or "\\" in run_id or ".." in run_id:
        raise ValueError(f"실행 ID 경로 탈출 감지: '{run_id}'")

    run_dir = root / "reports" / "executions" / run_id
    contract_file = run_dir / "contract.json"
    if not contract_file.is_file():
        raise ValueError(f"실행 계약 파일이 없습니다: {contract_file}")

    contract = json.loads(contract_file.read_text(encoding="utf-8"))
    if contract.get("accepted"):
        raise ValueError(f"실행 {run_id} 은 이미 채택(accepted)되었습니다")

    if contract.get("mode") != "manual":
        raise ValueError("strict 실행은 check_manual로 검증할 수 없습니다.")

    ws_path = Path(contract["workspace"]).resolve()
    verified_changes, cur_snap, gate_res = _validate_check_common(root, contract, ws_path)

    contract["verified_changes"] = verified_changes
    contract["verified_worker_snapshot"] = cur_snap
    contract["ga_result"] = gate_res["ga_output"]
    contract["g0_result"] = gate_res["g0_output"]
    contract["completion_note"] = completion_note
    contract["static_validation"] = "passed"
    contract["runtime_verification"] = "unverified"
    contract["verified_at"] = datetime.now(timezone.utc).isoformat()
    contract_file.write_text(json.dumps(contract, indent=2, ensure_ascii=False), encoding="utf-8")

    return {
        "run_id": run_id,
        "status": "checked",
        "static_validation": "passed",
        "runtime_verification": "unverified",
        "verified_changes": verified_changes,
    }


def _apply_accept_common(
    root: Path,
    contract: dict,
    contract_file: Path,
    status_file: Path,
    extra_contract_fields: dict[str, Any] | None = None,
    extra_return_fields: dict[str, Any] | None = None,
) -> dict:
    """accept 및 accept_manual 의 공통 원본 반영 로직 (후보 불변성, 원본 드리프트, CAS 원본 보존, 게이트 검증)."""
    run_id = contract["run_id"]
    verified_changes = contract["verified_changes"]
    ws_path = Path(contract["workspace"]).resolve()

    # 1. 검사 이후 워커 작업 공간 스냅샷 재대조
    cur_snap = _snapshot_directory(ws_path)
    verified_snap = contract.get("verified_worker_snapshot", {})
    if set(cur_snap.keys()) != set(verified_snap.keys()):
        raise ValueError("검사 이후 워커 작업 공간의 파일 추가 또는 삭제 감지")
    for f, info in cur_snap.items():
        if info["sha256"] != verified_snap[f]["sha256"]:
            raise ValueError(f"검사 이후 후보 파일 변경 감지 (해시 불일치): {f}")
        if info.get("mode") != verified_snap[f].get("mode"):
            raise ValueError(f"검사 이후 후보 파일 모드 변경 감지 (mode 불일치): {f}")
        if info.get("type") != verified_snap[f].get("type"):
            raise ValueError(f"검사 이후 후보 파일 유형 변경 감지 (type 불일치): {f}")

    # 2. 원본 HEAD, state, config 드리프트 재대조 (또는 승인된 handover 검증)
    _validate_origin_drift_with_handover(root, contract)

    # 3. 원본 target snapshot 재검증 (동시 user 수정 보존)
    _validate_origin_target_snapshot(root, verified_changes.keys(), contract.get("origin_start_snapshot", {}))

    # implementer의 경우 유효 게이트 보고서 및 세부 증거 엄격 검증
    if contract.get("role") == "implementer":
        run_dir = root / "reports" / "executions" / run_id
        gate_rep = run_dir / "gate_report.json"
        # 전역보고서 fallback 제거: run-local(reports/executions/<run_id>/gate_report.json)만 허용
        if not gate_rep.is_file():
            raise ValueError(
                f"구현자 채택에는 해당 실행의 유효한 게이트 판정 보고서({gate_rep})가 필요합니다"
            )

        try:
            rep_data = json.loads(gate_rep.read_text(encoding="utf-8"))
        except Exception as e:
            raise ValueError(f"게이트 판정 보고서 검증 실패: {e}")

        if rep_data.get("passed") is not True:
            raise ValueError("게이트 판정이 통과(passed) 상태가 아닙니다")

        evidence = rep_data.get("evidence", {})
        if not isinstance(evidence, dict) or not evidence:
            raise ValueError("게이트 판정 보고서에 evidence 객체가 누락되었습니다")

        if evidence.get("exit_code") != 0:
            raise ValueError(f"게이트 판정 exit_code 가 0이 아닙니다: {evidence.get('exit_code')}")

        # 필수 필드 누락 및 불일치 검사
        rep_role = rep_data.get("role")
        if not rep_role:
            raise ValueError("게이트 보고서에 role 이 누락되었습니다")
        if rep_role != contract.get("role"):
            raise ValueError(f"게이트 보고서 역할 불일치: {rep_role} != {contract.get('role')}")
        if evidence.get("role") and evidence.get("role") != contract.get("role"):
            raise ValueError(f"게이트 보고서 evidence 역할 불일치: {evidence.get('role')} != {contract.get('role')}")

        rep_wo = rep_data.get("wo")
        if not rep_wo:
            raise ValueError("게이트 보고서에 wo 가 누락되었습니다")
        if rep_wo != contract.get("wo"):
            raise ValueError(f"게이트 보고서 작업 지시서 불일치: {rep_wo} != {contract.get('wo')}")
        if evidence.get("wo") and evidence.get("wo") != contract.get("wo"):
            raise ValueError(f"게이트 보고서 evidence 작업 지시서 불일치: {evidence.get('wo')} != {contract.get('wo')}")

        rep_slice = rep_data.get("slice")
        if not rep_slice:
            raise ValueError("게이트 보고서에 slice 가 누락되었습니다")
        if rep_slice != contract.get("slice"):
            raise ValueError(f"게이트 보고서 슬라이스 불일치: {rep_slice} != {contract.get('slice')}")
        if evidence.get("slice") and evidence.get("slice") != contract.get("slice"):
            raise ValueError(f"게이트 보고서 evidence 슬라이스 불일치: {evidence.get('slice')} != {contract.get('slice')}")

        # 판정이 partial/feedback/only 는 거부
        verdict = evidence.get("verdict", "")
        if verdict not in ("SLICE PASS", "ALL PASS"):
            raise ValueError(f"유효한 판정 결과가 아닙니다 (SLICE PASS 또는 ALL PASS 필요): '{verdict}'")
        if "PARTIAL" in verdict or "FEEDBACK" in verdict:
            raise ValueError(f"부분(partial) 또는 피드백(feedback) 판정은 채택 불가: '{verdict}'")

        scope = evidence.get("scope")
        if isinstance(scope, dict) and "only" in scope:
            raise ValueError("게이트 only 실행 판정은 전체 품질 채택에 사용할 수 없습니다")
        if scope == "feedback":
            raise ValueError("피드백 판정은 채택에 사용할 수 없습니다")

        # execution_id 필수 검증 (evidence 또는 rep_data)
        rep_exec_id = evidence.get("execution_id") or rep_data.get("execution_id")
        if not rep_exec_id:
            raise ValueError("게이트 보고서에 execution_id 가 누락되었습니다")
        if rep_exec_id != run_id:
            raise ValueError(f"게이트 보고서 execution_id 불일치: {rep_exec_id} != {run_id}")
        if evidence.get("execution_id") and evidence.get("execution_id") != run_id:
            raise ValueError(f"게이트 보고서 evidence execution_id 불일치: {evidence.get('execution_id')} != {run_id}")

        # base_sha 필수 검증 (evidence 또는 rep_data)
        contract_base = contract.get("base")
        rep_base_sha = evidence.get("base_sha") or rep_data.get("base_sha")
        if not rep_base_sha:
            raise ValueError("게이트 보고서에 base_sha 가 누락되었습니다")
        if contract_base and rep_base_sha != contract_base:
            raise ValueError(f"게이트 보고서 base_sha 불일치: {rep_base_sha} != {contract_base}")
        if evidence.get("base_sha") and contract_base and evidence.get("base_sha") != contract_base:
            raise ValueError(f"게이트 보고서 evidence base_sha 불일치: {evidence.get('base_sha')} != {contract_base}")

        # 후보 스냅샷 digest 필수 검증 및 검사 뒤 후보 변경 거부
        rep_digest = evidence.get("candidate_digest") or rep_data.get("candidate_digest")
        if not rep_digest:
            raise ValueError("게이트 보고서에 candidate_digest 가 누락되었습니다")

        cur_digest = hashlib.sha256(json.dumps(cur_snap, sort_keys=True).encode("utf-8")).hexdigest()
        if cur_digest != rep_digest:
            raise ValueError("게이트 검사 이후 후보 변경 감지 (후보 digest 불일치)")

    # 4. 원본 반영 및 실패 시 CAS 기반 복구 (사용자 외부 수정 보존)
    applied_files: list[str] = []
    backups: dict[str, tuple[bytes, int] | None] = {}
    tmp_files: list[Path] = []

    try:
        for f, info in verified_changes.items():
            target_p = root / f
            backups[f] = (
                (target_p.read_bytes(), stat.S_IMODE(target_p.stat().st_mode))
                if target_p.is_file()
                else None
            )

            if info["action"] == "delete":
                if target_p.exists():
                    target_p.unlink()
                applied_files.append(f)
            else:
                target_p.parent.mkdir(parents=True, exist_ok=True)
                content_b = (ws_path / f).read_bytes()
                tmp_p = target_p.with_name(f".{target_p.name}.tmp.{os.getpid()}")
                tmp_files.append(tmp_p)
                tmp_p.write_bytes(content_b)
                if info.get("mode") is not None:
                    try:
                        os.chmod(tmp_p, stat.S_IMODE(info["mode"]))
                    except Exception:
                        pass
                os.replace(tmp_p, target_p)
                applied_files.append(f)

        # 채택 완료 상태 영구 기록
        contract["accepted"] = True
        contract["status"] = "accepted"
        contract["accepted_at"] = datetime.now(timezone.utc).isoformat()
        if extra_contract_fields:
            contract.update(extra_contract_fields)
        contract_file.write_text(json.dumps(contract, indent=2, ensure_ascii=False), encoding="utf-8")

        status_data = {"run_id": run_id, "status": "accepted", "accepted": True}
        if contract.get("mode"):
            status_data["mode"] = contract["mode"]
        status_file.write_text(json.dumps(status_data, indent=2, ensure_ascii=False), encoding="utf-8")

    except Exception as err:
        # 실패 시 이 명령이 변경한 파일만 안전하게 복구 (CAS 복구)
        for f in reversed(applied_files):
            target_p = root / f
            backup_item = backups.get(f)
            orig_b = backup_item[0] if backup_item is not None else None
            orig_m = backup_item[1] if backup_item is not None else None
            info = verified_changes.get(f, {})
            curr_b = target_p.read_bytes() if target_p.is_file() else None

            if info.get("action") == "delete":
                # 채택으로 삭제했던 파일: 현재도 파일이 없을 때만 원래대로 복구
                if curr_b is None:
                    if orig_b is not None:
                        try:
                            target_p.parent.mkdir(parents=True, exist_ok=True)
                            target_p.write_bytes(orig_b)
                            if orig_m is not None:
                                try:
                                    os.chmod(target_p, orig_m)
                                except Exception:
                                    pass
                        except Exception:
                            pass
            else:
                # 채택으로 작성했던 파일: 현재 파일 내용이 채택으로 작성한 내용과 같을 때만 복원
                expected_written_b = (ws_path / f).read_bytes() if (ws_path / f).is_file() else None
                if curr_b is not None and curr_b == expected_written_b:
                    try:
                        if orig_b is not None:
                            target_p.write_bytes(orig_b)
                            if orig_m is not None:
                                try:
                                    os.chmod(target_p, orig_m)
                                except Exception:
                                    pass
                        else:
                            target_p.unlink(missing_ok=True)
                    except Exception:
                        pass
                # curr_b != expected_written_b 이면 동시 외부 수정을 보존함

        contract["status"] = "failed"
        contract["error"] = f"accept failed and rolled back: {err}"
        try:
            contract_file.write_text(json.dumps(contract, indent=2, ensure_ascii=False), encoding="utf-8")
            status_data = {"run_id": run_id, "status": "failed", "accepted": False, "error": str(err)}
            status_file.write_text(json.dumps(status_data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
        raise
    finally:
        for tf in tmp_files:
            try:
                if tf.exists():
                    tf.unlink()
            except Exception:
                pass

    res = {
        "run_id": run_id,
        "status": "accepted",
        "applied_files": list(verified_changes.keys()),
    }
    if extra_return_fields:
        res.update(extra_return_fields)
    return res


def accept(root: Path, run_id: str, runner: Any) -> dict:
    """검증된 변경 산출물을 원본에 안전하게 반영(채택)한다."""
    root = Path(root).resolve()

    if "/" in run_id or "\\" in run_id or ".." in run_id:
        raise ValueError(f"실행 ID 경로 탈출 감지: '{run_id}'")

    run_dir = root / "reports" / "executions" / run_id
    contract_file = run_dir / "contract.json"
    status_file = run_dir / "status.json"

    if not contract_file.is_file():
        raise ValueError(f"실행 계약 파일이 없습니다: {contract_file}")

    with role_state.role_lock(root):
        contract = json.loads(contract_file.read_text(encoding="utf-8"))
        if contract.get("accepted"):
            raise ValueError(f"중복 채택 거부: 실행 {run_id} 은 이미 채택되었습니다")

        # strict vs manual 모드 혼용 차단
        if contract.get("mode") == "manual":
            raise ValueError("수동 실행(mode=manual)은 strict accept로 채택할 수 없습니다.")

        # 러너 id 재대조
        runner_id = getattr(runner, "id", None)
        if not runner_id or runner_id != contract.get("runner"):
            raise ValueError(f"러너 불일치: runner.id '{runner_id}' != 계약 runner '{contract.get('runner')}'")

        # 1. 러너 상태 및 권한 재확인
        obs = runner.observe(contract)
        obs_err = _validate_observation(obs, contract, is_completion=True)
        if obs_err:
            raise ValueError(obs_err)

        # 2. check 가 선행되지 않았으면 check 실행
        if "verified_changes" not in contract:
            check(root, run_id, runner)
            contract = json.loads(contract_file.read_text(encoding="utf-8"))

        return _apply_accept_common(root, contract, contract_file, status_file)


def accept_manual(root: Path, run_id: str, review_note: str) -> dict:
    """수동 실행의 검증된 변경 산출물을 검토 후 원본에 채택한다."""
    root = Path(root).resolve()

    if not review_note or not isinstance(review_note, str) or not review_note.strip():
        raise ValueError("수동 채택 review_note는 비어 있거나 공백만으로 구성될 수 없습니다.")

    if "/" in run_id or "\\" in run_id or ".." in run_id:
        raise ValueError(f"실행 ID 경로 탈출 감지: '{run_id}'")

    run_dir = root / "reports" / "executions" / run_id
    contract_file = run_dir / "contract.json"
    status_file = run_dir / "status.json"

    if not contract_file.is_file():
        raise ValueError(f"실행 계약 파일이 없습니다: {contract_file}")

    with role_state.role_lock(root):
        contract = json.loads(contract_file.read_text(encoding="utf-8"))
        if contract.get("accepted"):
            raise ValueError(f"중복 채택 거부: 실행 {run_id} 은 이미 채택되었습니다")

        # strict vs manual 모드 혼용 차단
        if contract.get("mode") != "manual":
            raise ValueError("strict 실행은 accept_manual로 채택할 수 없습니다.")

        # manual accept는 성공한 manual check가 선행되어 있어야 함 (자동 재check 금지)
        if (
            "verified_changes" not in contract
            or not contract.get("completion_note")
            or not isinstance(contract.get("completion_note"), str)
            or not contract.get("completion_note").strip()
            or contract.get("static_validation") != "passed"
            or "verified_worker_snapshot" not in contract
        ):
            raise ValueError("수동 채택(accept_manual)은 성공한 수동 완료 검사(check_manual)가 선행되어야 합니다.")

        try:
            from .finish_work import ROLE_KIND_MAP, run_post_core_checks
        except (ImportError, ValueError):
            try:
                from finish_work import ROLE_KIND_MAP, run_post_core_checks
            except (ImportError, ValueError):
                from harness.finish_work import ROLE_KIND_MAP, run_post_core_checks

        closure = run_post_core_checks(
            root,
            run_id,
            kind=ROLE_KIND_MAP.get(contract.get("role"), "unknown"),
            contract=contract,
            lead_note=review_note,
            checks={
                "core": "passed",
                "manual_check": "passed",
                "static_validation": contract.get("static_validation", "passed"),
            },
        )
        blockers = closure.get("blockers") or []
        if closure.get("status") == "blocked" or blockers:
            msg = f"수동 채택 사후 검증 차단: {', '.join(str(b) for b in blockers)}" if blockers else "수동 채택 사후 검증 차단 (status=blocked)"
            raise ValueError(msg)

        return _apply_accept_common(
            root,
            contract,
            contract_file,
            status_file,
            extra_contract_fields={
                "review_note": review_note,
                "runtime_verification": "unverified",
            },
            extra_return_fields={
                "static_validation": "passed",
                "runtime_verification": "unverified",
                "closure": closure,
            },
        )


def main() -> int:
    """CLI 진입점."""
    ap = argparse.ArgumentParser(description="실행 관리자 및 채택 도구 (WO-H63, WO-H64)")
    sub = ap.add_subparsers(dest="command")

    p_start = sub.add_parser("start", help="실행 시작")
    p_start.add_argument("--wo", required=True, help="작업 지시서 ID")
    p_start.add_argument("--slice", required=True, help="슬라이스 ID")
    p_start.add_argument("--role", required=True, help="역할")
    p_start.add_argument("--base", required=True, help="기준 커밋 SHA")
    p_start.add_argument("--approval-ref", required=True, help="승인 참조")
    p_start.add_argument("--runner", default="orca", help="러너 (기본: orca)")

    p_check = sub.add_parser("check", help="산출물 검증")
    p_check.add_argument("--run", required=True, help="실행 ID")
    p_check.add_argument("--runner", default="orca", help="러너 (기본: orca)")

    p_accept = sub.add_parser("accept", help="산출물 채택")
    p_accept.add_argument("--run", required=True, help="실행 ID")
    p_accept.add_argument("--runner", default="orca", help="러너 (기본: orca)")

    p_mstart = sub.add_parser("manual-start", help="수동 실행 시작")
    p_mstart.add_argument("--wo", required=True, help="작업 지시서 ID")
    p_mstart.add_argument("--slice", required=True, help="슬라이스 ID")
    p_mstart.add_argument("--role", required=True, help="역할")
    p_mstart.add_argument("--base", required=True, help="기준 커밋 SHA")
    p_mstart.add_argument("--approval-ref", required=True, help="승인 참조")
    p_mstart.add_argument("--workspace", required=True, help="격리된 작업 공간 경로")
    p_mstart.add_argument("--note", required=True, help="수동 시작 사유/노트")
    p_mstart.add_argument("--workspace-profile", default=None, help="작업 공간 프로필")
    p_mstart.add_argument("--workspace-config", default=None, help="작업 공간 정책 설정 파일 경로")

    p_mcheck = sub.add_parser("manual-check", help="수동 실행 산출물 검증")
    p_mcheck.add_argument("--run", required=True, help="실행 ID")
    p_mcheck.add_argument("--completion-note", "--note", dest="completion_note", required=True, help="작업 완료 선언 노트")

    p_maccept = sub.add_parser("manual-accept", help="수동 실행 산출물 채택")
    p_maccept.add_argument("--run", required=True, help="실행 ID")
    p_maccept.add_argument("--review-note", "--note", dest="review_note", required=True, help="리드 검토 및 채택 노트")

    args = ap.parse_args()
    if not args.command:
        ap.print_help()
        return 1

    root = Path.cwd()

    if args.command == "manual-start":
        task = {
            "wo": args.wo,
            "slice": args.slice,
            "role": args.role,
            "base": args.base,
            "approval_ref": args.approval_ref,
        }
        if args.workspace_profile is not None:
            task["workspace_profile"] = args.workspace_profile
        if args.workspace_config is not None:
            task["workspace_config"] = args.workspace_config
        try:
            run_id = start_manual(root, task, args.workspace, args.note)
            print(f"수동 실행 등록 완료: run_id={run_id}")
            return 0
        except Exception as e:
            print(f"오류: 수동 실행 등록 실패: {e}", file=sys.stderr)
            return 1

    elif args.command == "manual-check":
        try:
            res = check_manual(root, args.run, args.completion_note)
            print(f"수동 실행 검증 완료: {res}")
            return 0
        except Exception as e:
            print(f"오류: 수동 실행 검증 실패: {e}", file=sys.stderr)
            return 1

    elif args.command == "manual-accept":
        try:
            res = accept_manual(root, args.run, args.review_note)
            print(f"수동 실행 채택 완료: {res}")
            return 0
        except Exception as e:
            print(f"오류: 수동 실행 채택 실패: {e}", file=sys.stderr)
            return 1

    from adapters.tools.orca_execution import get_runner

    try:
        runner_obj = get_runner(args.runner, root=root)
    except Exception as e:
        print(f"오류: 러너 초기화 실패: {e}", file=sys.stderr)
        return 1

    if args.command == "start":
        task = {
            "wo": args.wo,
            "slice": args.slice,
            "role": args.role,
            "base": args.base,
            "approval_ref": args.approval_ref,
        }
        try:
            run_id = start(root, task, runner_obj)
            print(f"실행 시작 완료: run_id={run_id}")
            return 0
        except Exception as e:
            print(f"오류: 실행 시작 실패: {e}", file=sys.stderr)
            return 1

    elif args.command == "check":
        try:
            res = check(root, args.run, runner_obj)
            print(f"실행 검증 완료: {res}")
            return 0
        except Exception as e:
            print(f"오류: 검증 실패: {e}", file=sys.stderr)
            return 1

    elif args.command == "accept":
        try:
            res = accept(root, args.run, runner_obj)
            print(f"실행 채택 완료: {res}")
            return 0
        except Exception as e:
            print(f"오류: 채택 실패: {e}", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
