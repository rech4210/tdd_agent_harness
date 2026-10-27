"""작업 완료 검증 래퍼 (WO-H71, P3).

Core(check_manual), step.py(--check close), 문서 최신성 검사(check_docs_fresh.py),
KPI 산출 도구(kpi.py)를 통합 검증하는 래퍼.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

# harness 및 저장소 루트를 sys.path에 등록 (기존 하네스 CLI와 동일 부트스트랩)
HARNESS_DIR = Path(__file__).resolve().parent
REPO_ROOT = HARNESS_DIR.parent
if str(HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(HARNESS_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# 기존 테스트 seam을 위한 execution 모듈 노출
import execution

KIND_ROLE_MAP = {
    "code": "implementer",
    "doc-audit": "doc_auditor",
    "doc-edit": "doc_editor",
}

ROLE_KIND_MAP = {
    "implementer": "code",
    "doc_auditor": "doc-audit",
    "doc_editor": "doc-edit",
}


def run_post_core_checks(
    root: Path | str,
    run_id: str | None = None,
    kind: str = "unknown",
    contract: dict[str, Any] | None = None,
    blockers: list[str] | None = None,
    advice: list[str] | None = None,
    checks: dict[str, Any] | None = None,
    *,
    run: str | None = None,
    audit_report: str | None = None,
    lead_note: str | None = None,
    audited_inputs: tuple[str, ...] | list[str] = (),
) -> dict[str, Any]:
    """Core check_manual 이후의 태스크별 검증(step close, docs fresh, audit, kpi)을 수행한다."""
    root = Path(root).resolve()
    actual_run_id = run_id or run or ""
    contract = contract if isinstance(contract, dict) else {}
    if not actual_run_id and "run_id" in contract:
        actual_run_id = contract.get("run_id", "")
    blockers = list(blockers) if blockers is not None else []
    advice = list(advice) if advice is not None else []
    checks = dict(checks) if checks is not None else {}
    audited_inputs = audited_inputs if audited_inputs is not None else ()

    contract_role = contract.get("role")
    if kind in KIND_ROLE_MAP:
        expected_role = KIND_ROLE_MAP[kind]
        if contract_role and contract_role != expected_role:
            mismatch_msg = (
                f"Role mismatch: kind '{kind}' requires role '{expected_role}', but contract role is '{contract_role}'"
            )
            if mismatch_msg not in blockers:
                blockers.append(mismatch_msg)
    else:
        unknown_msg = f"Unknown kind '{kind}': manual confirmation required"
        if unknown_msg not in advice:
            advice.append(unknown_msg)

    # Code 태스크의 경우 step.py --check close 실행 (선행 blocker가 없을 때)
    if kind == "code" and not blockers:
        contract_slice = contract.get("slice")
        if not contract_slice:
            blockers.append("Contract missing slice identifier")
        else:
            step_script = root / "harness" / "step.py"
            if not step_script.is_file():
                blockers.append(f"step.py not found at {step_script}")
            else:
                try:
                    p_step = subprocess.run(
                        [
                            sys.executable,
                            "-B",
                            str(step_script),
                            "--slice",
                            str(contract_slice),
                            "--check",
                            "close",
                            "--json",
                        ],
                        cwd=root,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=180,
                    )
                    step_report = None
                    try:
                        parsed = json.loads(p_step.stdout)
                        if isinstance(parsed, dict):
                            step_report = parsed
                    except Exception:
                        step_report = None

                    checks["step"] = {
                        "returncode": p_step.returncode,
                        "stdout": p_step.stdout,
                        "stderr": p_step.stderr,
                        "report": step_report if isinstance(step_report, dict) else {},
                    }

                    if step_report is None:
                        blockers.append(
                            f"step.py --check close returned malformed or missing JSON evidence (exit {p_step.returncode}): {p_step.stderr.strip() or p_step.stdout.strip()}"
                        )
                    elif p_step.returncode != 0:
                        step_blockers = step_report.get("blockers", [])
                        if isinstance(step_blockers, list) and step_blockers:
                            for sb in step_blockers:
                                blockers.append(str(sb))
                        else:
                            blockers.append(
                                f"step.py --check close failed (exit {p_step.returncode}): {p_step.stderr.strip() or p_step.stdout.strip()}"
                            )
                    else:
                        step_blockers = step_report.get("blockers", [])
                        if isinstance(step_blockers, list) and step_blockers:
                            for sb in step_blockers:
                                blockers.append(str(sb))
                except Exception as se:
                    blockers.append(f"Failed to execute step.py: {se}")

    # 지원되는 태스크의 경우 문서 최신성 검사기(scripts/check_docs_fresh.py) 실행
    if kind in KIND_ROLE_MAP:
        docs_script = root / "scripts" / "check_docs_fresh.py"
        if docs_script.is_file():
            try:
                p_docs = subprocess.run(
                    [sys.executable, "-B", str(docs_script)],
                    cwd=root,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=180,
                )
                checks["docs"] = {
                    "returncode": p_docs.returncode,
                    "stdout": p_docs.stdout,
                    "stderr": p_docs.stderr,
                }
                if p_docs.returncode != 0:
                    blockers.append(
                        f"check_docs_fresh.py failed (exit {p_docs.returncode}): {p_docs.stderr.strip() or p_docs.stdout.strip()}"
                    )
            except Exception as de:
                blockers.append(f"Failed to execute check_docs_fresh.py: {de}")
        else:
            advice.append("scripts/check_docs_fresh.py is absent; manual documentation check required")

    # 감사 보고서(audit_report) 검증
    if not audit_report or not isinstance(audit_report, str) or not audit_report.strip():
        advice.append("audit_report is missing or empty; manual confirmation required")
    else:
        audit_path = Path(audit_report)
        if audit_path.is_absolute() or ".." in audit_path.parts:
            blockers.append(f"audit_report path escape detected: '{audit_report}'")
        else:
            full_audit = root / audit_path
            try:
                res_audit = full_audit.resolve()
                try:
                    is_under = res_audit.is_relative_to(root)
                except AttributeError:
                    is_under = str(res_audit).startswith(str(root))
                if not is_under:
                    blockers.append(f"audit_report path escape detected: '{audit_report}'")
                elif not full_audit.is_file() or full_audit.stat().st_size == 0:
                    advice.append(f"audit_report '{audit_report}' does not exist or is empty")
            except Exception as ae:
                advice.append(f"audit_report check error: {ae}")

    # 리드 비교 노트(lead_note) 검증
    if not lead_note or not isinstance(lead_note, str) or not lead_note.strip():
        advice.append("lead_note is missing or empty; manual comparison required")

    # 명시적 감사 입력(audited_inputs) 기준 스냅샷 대조
    audit_inputs_changed: list[str] = []
    ws_str = contract.get("workspace")
    ws_path = Path(ws_str).resolve() if ws_str else None
    worker_start_snap = contract.get("worker_start_snapshot", {})

    if not audited_inputs:
        advice.append("No audited_inputs provided; relevance and manual advice required")
    else:
        for inp in audited_inputs:
            if not inp or not isinstance(inp, str):
                continue
            p_inp = Path(inp)
            if p_inp.is_absolute() or ".." in p_inp.parts:
                blockers.append(f"Explicit input escape detected in audited_inputs: '{inp}'")
                continue

            norm_rel = p_inp.as_posix()
            worker_file = (ws_path / norm_rel) if ws_path else None
            origin_file = root / norm_rel

            if (worker_file and worker_file.is_symlink()) or origin_file.is_symlink():
                blockers.append(f"Symlink escape detected in audited_inputs: '{inp}'")
                continue

            if worker_file:
                try:
                    res_w = worker_file.resolve()
                    if not res_w.is_relative_to(ws_path):
                        blockers.append(f"Worker path escape detected: '{inp}'")
                        continue
                except Exception:
                    pass
            try:
                res_o = origin_file.resolve()
                if not res_o.is_relative_to(root):
                    blockers.append(f"Origin path escape detected: '{inp}'")
                    continue
            except Exception:
                pass

            if norm_rel not in worker_start_snap:
                audit_inputs_changed.append(inp)
                continue

            snap_entry = worker_start_snap[norm_rel]
            expected_sha = snap_entry.get("sha256") if isinstance(snap_entry, dict) else str(snap_entry)

            if not worker_file or not worker_file.is_file():
                audit_inputs_changed.append(inp)
                continue
            try:
                w_sha = hashlib.sha256(worker_file.read_bytes()).hexdigest()
                if w_sha != expected_sha:
                    audit_inputs_changed.append(inp)
                    continue
            except Exception:
                audit_inputs_changed.append(inp)
                continue

            if not origin_file.is_file():
                audit_inputs_changed.append(inp)
                continue
            try:
                o_sha = hashlib.sha256(origin_file.read_bytes()).hexdigest()
                if o_sha != expected_sha:
                    audit_inputs_changed.append(inp)
                    continue
            except Exception:
                audit_inputs_changed.append(inp)
                continue

    checks["audit_inputs"] = {"changed": audit_inputs_changed}
    if audit_inputs_changed:
        advice.append(
            f"Audited inputs drift or missing baseline detected for: {', '.join(audit_inputs_changed)}; baseline audit invalidated"
        )

    # reports/events.jsonl 에서 실제 model_usage 이벤트 탐색
    events_file = root / "reports" / "events.jsonl"
    usage_event = None
    if events_file.is_file():
        try:
            for line in events_file.read_text(encoding="utf-8", errors="replace").splitlines():
                if not line.strip():
                    continue
                try:
                    ev = json.loads(line)
                    if isinstance(ev, dict) and ev.get("kind") == "model_usage" and ev.get("execution_id") == actual_run_id:
                        usage_event = ev
                except Exception:
                    pass
        except Exception as ee:
            advice.append(f"Failed to read events.jsonl: {ee}")

    if usage_event is not None:
        checks["usage"] = usage_event
        checks["model_usage"] = usage_event
    else:
        advice.append(f"Actual model_usage event for execution_id '{actual_run_id}' not found in reports/events.jsonl")

    # harness/kpi.py 및 --timing 실행
    kpi_script = root / "harness" / "kpi.py"
    if kpi_script.is_file():
        try:
            p_kpi = subprocess.run(
                [sys.executable, "-B", str(kpi_script)],
                cwd=root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
            )
            p_timing = subprocess.run(
                [sys.executable, "-B", str(kpi_script), "--timing"],
                cwd=root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
            )
            checks["kpi"] = {
                "returncode": p_kpi.returncode,
                "stdout": p_kpi.stdout,
                "stderr": p_kpi.stderr,
                "timing_returncode": p_timing.returncode,
                "timing_stdout": p_timing.stdout,
                "timing_stderr": p_timing.stderr,
            }
            if p_kpi.returncode != 0 or p_timing.returncode != 0:
                advice.append(
                    f"kpi.py execution returned non-zero (kpi: {p_kpi.returncode}, timing: {p_timing.returncode})"
                )
        except Exception as ke:
            advice.append(f"Failed to execute kpi.py: {ke}")
    else:
        advice.append("harness/kpi.py is not available")

    # 최종 상태 판정
    if blockers:
        status = "blocked"
    elif advice or kind not in KIND_ROLE_MAP:
        status = "manual_confirmation"
    else:
        status = "checks_passed"

    return {
        "execution_id": actual_run_id,
        "status": status,
        "blockers": blockers,
        "advice": advice,
        "checks": checks,
        "runtime_verification": "unverified",
    }


check_post_core = run_post_core_checks
post_core_checks = run_post_core_checks


def finish_work(
    root: Path | str,
    run_id: str,
    kind: str,
    completion_note: str,
    *,
    audit_report: str | None = None,
    lead_note: str | None = None,
    audited_inputs: tuple[str, ...] | list[str] = (),
) -> dict[str, Any]:
    """작업 완료를 위한 통합 검증을 수행하고 상태/조언을 반환한다."""
    root = Path(root).resolve()
    blockers: list[str] = []
    advice: list[str] = []
    checks: dict[str, Any] = {}

    # run_id 검증 및 격리된 경로 확인
    if not run_id or not isinstance(run_id, str) or "/" in run_id or "\\" in run_id or ".." in run_id:
        blockers.append(f"Invalid run_id or path escape detected: {run_id}")
        return {
            "execution_id": run_id,
            "status": "blocked",
            "blockers": blockers,
            "advice": advice,
            "checks": checks,
            "runtime_verification": "unverified",
        }

    run_dir = root / "reports" / "executions" / run_id
    try:
        executions_dir = (root / "reports" / "executions").resolve()
        resolved_run_dir = run_dir.resolve()
        try:
            is_rel = resolved_run_dir.is_relative_to(executions_dir)
        except AttributeError:
            is_rel = str(resolved_run_dir).startswith(str(executions_dir))
        if not is_rel or resolved_run_dir == executions_dir:
            blockers.append(f"run_id path escape detected: {run_id}")
            return {
                "execution_id": run_id,
                "status": "blocked",
                "blockers": blockers,
                "advice": advice,
                "checks": checks,
                "runtime_verification": "unverified",
            }
    except Exception as e:
        blockers.append(f"Failed to resolve execution directory: {e}")
        return {
            "execution_id": run_id,
            "status": "blocked",
            "blockers": blockers,
            "advice": advice,
            "checks": checks,
            "runtime_verification": "unverified",
        }

    contract_file = run_dir / "contract.json"
    if not contract_file.is_file():
        blockers.append(f"Missing contract: contract.json not found for run_id '{run_id}'")
        return {
            "execution_id": run_id,
            "status": "blocked",
            "blockers": blockers,
            "advice": advice,
            "checks": checks,
            "runtime_verification": "unverified",
        }

    try:
        contract = json.loads(contract_file.read_text(encoding="utf-8"))
    except Exception as e:
        blockers.append(f"Invalid contract JSON in '{contract_file}': {e}")
        return {
            "execution_id": run_id,
            "status": "blocked",
            "blockers": blockers,
            "advice": advice,
            "checks": checks,
            "runtime_verification": "unverified",
        }

    # 역할 일치 검증: code=implementer, doc-audit=doc_auditor, doc-edit=doc_editor
    contract_role = contract.get("role")
    if kind in KIND_ROLE_MAP:
        expected_role = KIND_ROLE_MAP[kind]
        if contract_role != expected_role:
            blockers.append(
                f"Role mismatch: kind '{kind}' requires role '{expected_role}', but contract role is '{contract_role}'"
            )
    else:
        advice.append(f"Unknown kind '{kind}': manual confirmation required")

    # Core check_manual 실행
    try:
        core_res = execution.check_manual(root, run_id, completion_note)
        checks["core"] = core_res
    except Exception as e:
        blockers.append(f"Core check_manual failed: {e}")

    return run_post_core_checks(
        root=root,
        run_id=run_id,
        kind=kind,
        contract=contract,
        blockers=blockers,
        advice=advice,
        checks=checks,
        audit_report=audit_report,
        lead_note=lead_note,
        audited_inputs=audited_inputs,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Finish work verification wrapper (WO-H71)")
    default_root = Path(__file__).resolve().parents[1]
    parser.add_argument("--root", default=str(default_root), help="Repository root path")
    parser.add_argument("--run", required=True, help="Execution run ID")
    parser.add_argument("--kind", required=True, help="Task kind (code, doc-edit, doc-audit, etc.)")
    parser.add_argument("--completion-note", required=True, help="Human completion note")
    parser.add_argument("--audit-report", default=None, help="Relative path to audit report artifact under root")
    parser.add_argument("--lead-note", default=None, help="Human lead comparison note")
    parser.add_argument(
        "--audit-input",
        dest="audited_inputs",
        action="append",
        default=None,
        help="Explicit audited relative file path (repeatable)",
    )

    args = parser.parse_args()

    inputs = args.audited_inputs if args.audited_inputs is not None else []
    result = finish_work(
        root=args.root,
        run_id=args.run,
        kind=args.kind,
        completion_note=args.completion_note,
        audit_report=args.audit_report,
        lead_note=args.lead_note,
        audited_inputs=inputs,
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))

    if result.get("status") == "blocked" or result.get("blockers"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
