"""판정 증거 계약 검증 도구 (Core, HD-28 0a, WO-H24).

보고서(reports/gate_report.json)의 evidence 객체를 대조하여
지정한 커밋에서 게이트가 깨끗하게 통과했는지 확인한다.

주의:
보고서는 로컬에서 위조할 수 있으므로 이 검사는 표시 · 대조용이며
실제 게이트 판정을 대신하지 않는다.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

try:
    from upgrade import OWNED
except ImportError:
    from harness.upgrade import OWNED

ROOT = Path(__file__).resolve().parents[1]


def _is_ignored(path_str: str) -> bool:
    norm = path_str.strip().strip('"').replace("\\", "/")
    if norm == "reports" or norm.startswith("reports/"):
        return True
    parts = [seg for seg in norm.split("/") if seg]
    while parts and parts[0] == ".":
        parts.pop(0)
    if parts and parts[0] in OWNED and "__pycache__" in parts:
        return True
    return False


def check_dirty(cwd: Path) -> bool:
    """작업 트리의 변경사항 유무를 확인한다 (reports/ 및 하네스 소유 경로 아래 __pycache__ 제외)."""
    res = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all", "--ignore-submodules=none"],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if res.returncode != 0:
        return True
    for raw_line in res.stdout.splitlines():
        if not raw_line.strip() or len(raw_line) < 4:
            continue
        path_part = raw_line[3:].strip()
        if " -> " in path_part:
            parts = path_part.split(" -> ", 1)
            p1 = parts[0].strip()
            p2 = parts[1].strip()
            if _is_ignored(p1) and _is_ignored(p2):
                continue
            return True
        if _is_ignored(path_part):
            continue
        return True
    return False


def verify_evidence(sha_rev: str, report_file: Path | str = "reports/gate_report.json") -> tuple[int, str]:
    """커밋 ref 와 보고서의 evidence 를 검증한다.

    반환: (종료 코드, 한 줄 이유)
    - sha_rev 해석 실패: 종료 코드 2
    - 보고서 없음 / evidence 불일치 / dirty / 게이트 실패: 종료 코드 1
    - 모두 일치: 종료 코드 0
    """
    p_rev = subprocess.run(
        ["git", "rev-parse", "--verify", f"{sha_rev}^{{commit}}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if p_rev.returncode != 0:
        return 2, f"커밋 해시를 해석할 수 없습니다: {sha_rev}"
    target_sha = p_rev.stdout.strip()

    p_rep = Path(report_file)
    if not p_rep.is_absolute():
        p_rep = ROOT / p_rep

    if not p_rep.is_file():
        return 1, f"보고서 파일이 없습니다: {report_file}"

    try:
        data = json.loads(p_rep.read_text(encoding="utf-8"))
    except Exception as e:
        return 1, f"보고서 JSON 해석 실패: {e}"

    if "evidence" not in data or not isinstance(data["evidence"], dict):
        return 1, "보고서에 evidence 객체가 없습니다."

    ev = data["evidence"]
    schema = ev.get("schema")
    if not isinstance(schema, int) or isinstance(schema, bool) or schema != 1:
        return 1, f"evidence schema 가 정수 1 이 아닙니다: {schema!r}"

    if ev.get("target_sha") != target_sha:
        return 1, f"target_sha 불일치: 보고서={ev.get('target_sha')}, 대상={target_sha}"

    scope = ev.get("scope")
    if scope != "full":
        return 1, f"전체 실행 증거가 아닙니다 (scope={scope!r})."

    dirty = ev.get("dirty")
    if not isinstance(dirty, bool) or dirty is not False:
        return 1, "보고서가 dirty 상태에서 생성되었습니다 (dirty != false)."

    exit_code = ev.get("exit_code")
    if not isinstance(exit_code, int) or isinstance(exit_code, bool) or exit_code != 0:
        return 1, f"게이트가 실패한 보고서입니다 (exit_code={exit_code!r})."

    return 0, f"OK: {target_sha} 판정 증거 확인됨 (verdict={ev.get('verdict')})"


def main() -> int:
    ap = argparse.ArgumentParser(
        description="판정 보고서의 증거(evidence)를 검증합니다. 보고서는 로컬에서 위조할 수 있으므로 이 검사는 표시 · 대조용이며 실제 게이트 판정을 대신하지 않습니다."
    )
    sub = ap.add_subparsers(dest="subcommand", required=True)
    chk = sub.add_parser(
        "check",
        help="지정한 커밋 해시와 보고서의 evidence 가 일치하는지 확인합니다. (표시 · 대조용)",
    )
    chk.add_argument("--sha", required=True, help="확인할 커밋 ref 또는 전체 해시")
    chk.add_argument("--report", default="reports/gate_report.json", help="검증할 보고서 경로 (기본: reports/gate_report.json)")
    args = ap.parse_args()

    if args.subcommand == "check":
        code, msg = verify_evidence(args.sha, args.report)
        print(msg)
        return code
    return 1


if __name__ == "__main__":
    sys.exit(main())
