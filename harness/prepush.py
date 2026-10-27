"""git pre-push 훅 실행기 및 설치기 (Core, HD-28 0a, WO-H24).

보호 ref 로 가는 push 전에 게이트를 다시 돌려 깨끗한 상태를 강제한다.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from evidence import check_dirty

MARKER = "# harness-pre-push-hook"
ZERO_SHA = "0" * 40


def _is_zero_sha(sha: str) -> bool:
    return bool(sha) and set(sha) == {"0"}


def _read_stdin() -> str:
    if sys.stdin is None:
        return ""
    buf = getattr(sys.stdin, "buffer", None)
    if buf is not None:
        return buf.read().decode("utf-8", errors="replace")
    raw = sys.stdin.read()
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return raw


def install_hook() -> int:
    """<git 저장소>/.git/hooks/pre-push 훅을 설치한다."""
    res = subprocess.run(
        ["git", "rev-parse", "--git-path", "hooks"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if res.returncode == 0 and res.stdout.strip():
        hooks_p = Path(res.stdout.strip())
        hooks_dir = hooks_p if hooks_p.is_absolute() else (ROOT / hooks_p).resolve()
    else:
        hooks_dir = ROOT / ".git" / "hooks"

    hook_file = hooks_dir / "pre-push"
    if hook_file.exists():
        existing = hook_file.read_text(encoding="utf-8", errors="replace")
        if MARKER not in existing:
            print("다른 훅이 이미 존재하여 덮어쓰지 않습니다.", file=sys.stderr)
            return 1

    py_exe = Path(sys.executable).resolve().as_posix()
    hook_content = (
        "#!/bin/sh\n"
        f"{MARKER}\n"
        'cd "$(git rev-parse --show-toplevel)" || exit 1\n'
        f'exec "{py_exe}" "harness/prepush.py" "$@"\n'
    )
    hook_file.parent.mkdir(parents=True, exist_ok=True)
    hook_file.write_text(hook_content, encoding="utf-8", newline="\n")
    try:
        hook_file.chmod(0o755)
    except Exception:
        pass
    print(f"pre-push 훅 설치 완료: {hook_file.as_posix()}")
    return 0


def _reusable(report: object, local_sha: str, base_sha: str, role: str) -> bool:
    """reports/gate_report.json 이 대상 push 의 유효한 통과 증거인지 엄격하게 검증한다."""
    if not isinstance(report, dict):
        return False
    if report.get("passed") is not True:
        return False
    rep_role = report.get("role")
    if not isinstance(rep_role, str) or rep_role != role:  # 정확히 같아야 한다 (공백 정규화 없음, WO-H77 리드 보정 B04)
        return False
    ev = report.get("evidence")
    if not isinstance(ev, dict):
        return False
    schema = ev.get("schema")
    if type(schema) is not int or schema != 1:
        return False
    if ev.get("target_sha") != local_sha:
        return False
    if ev.get("base_sha") != base_sha:
        return False
    if ev.get("scope") != "full":
        return False
    if ev.get("dirty") is not False:
        return False
    exit_code = ev.get("exit_code")
    if type(exit_code) is not int or exit_code != 0:
        return False
    if ev.get("verdict") != "ALL PASS":
        return False
    return True


def run_hook() -> int:
    """git pre-push 훅 규약에 따라 표준 입력을 검증하고 필요시 게이트를 실행한다."""
    gates_path = ROOT / "config" / "gates.toml"
    if not gates_path.is_file():
        print("[enforcement] 설정이 없어 병합 강제 검사를 건너뜁니다.")
        return 0

    try:
        gates_doc = tomllib.loads(gates_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"config/gates.toml 파싱 실패: {e}", file=sys.stderr)
        return 1

    enforce = gates_doc.get("enforcement")
    if not enforce or not isinstance(enforce, dict):
        print("[enforcement] 설정이 없어 병합 강제 검사를 건너뜁니다.")
        return 0

    protected_refs = enforce.get("protected_refs", [])
    if not protected_refs:
        return 0

    stdin_data = _read_stdin()
    lines = [l.rstrip("\r") for l in stdin_data.split("\n")]

    protected_lines = []
    for line in lines:
        if not line:
            continue
        parts = line.split(" ")
        if len(parts) == 4:
            local_ref, local_sha, remote_ref, remote_sha = parts
            if remote_ref in protected_refs:
                protected_lines.append((local_ref, local_sha, remote_ref, remote_sha))

    if not protected_lines:
        return 0

    role = enforce.get("role")
    if not role or not isinstance(role, str) or not role.strip():
        print("[enforcement] 에 role 설정이 없습니다 (기본 역할 없음).", file=sys.stderr)
        return 1
    role = role.strip()

    reuse = enforce.get("reuse_evidence", False)
    if type(reuse) is not bool:
        print("[enforcement] reuse_evidence 는 true/false 여야 합니다.", file=sys.stderr)
        return 1

    # 1. 거부 사유 검사: 하나라도 있으면 게이트를 돌리지 않는다
    # (1) 삭제 검사: local_sha 가 zero object ID
    for local_ref, local_sha, remote_ref, remote_sha in protected_lines:
        if _is_zero_sha(local_sha):
            print(f"보호 ref 삭제는 허용되지 않습니다: {remote_ref}")
            return 1

    # (2) local_sha != HEAD 검사
    res_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if res_head.returncode != 0:
        print("HEAD 커밋을 찾을 수 없습니다.")
        return 1
    current_head = res_head.stdout.strip()

    for local_ref, local_sha, remote_ref, remote_sha in protected_lines:
        if local_sha != current_head:
            print(f"보낼 커밋({local_sha[:7]})이 현재 HEAD({current_head[:7]})와 다릅니다.")
            return 1

    # (3) 작업 트리가 깨끗하지 않음 검사 (reports/ 제외)
    if check_dirty(ROOT):
        print("작업 트리가 깨끗하지 않습니다 (커밋되지 않은 변경사항 존재).")
        return 1

    # (4) remote_sha 가 0 이 아닌데 로컬에 없음 검사 ("먼저 fetch")
    for local_ref, local_sha, remote_ref, remote_sha in protected_lines:
        if not _is_zero_sha(remote_sha):
            cat_res = subprocess.run(
                ["git", "rev-parse", "--verify", f"{remote_sha}^{{commit}}"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if cat_res.returncode != 0:
                print(f"원격 커밋 {remote_sha} 이 로컬에 없습니다 (먼저 fetch).")
                return 1

    # 2. 모든 검사를 통과한 경우 게이트 실행 또는 증거 재사용
    initial_report: object = None
    if reuse:
        report_path = ROOT / "reports" / "gate_report.json"
        try:
            if report_path.is_file():
                # BOM 은 evidence.py check 와 같게 거부한다 (utf-8 엄격 디코드 → BOM 이면 파싱 실패, 리드 보정 B09)
                initial_report = json.loads(report_path.read_bytes().decode("utf-8"))
        except Exception:
            initial_report = None

    for local_ref, local_sha, remote_ref, remote_sha in protected_lines:
        base_sha = local_sha if _is_zero_sha(remote_sha) else remote_sha
        if reuse and _reusable(initial_report, local_sha, base_sha, role):
            print(f"재사용: 대상 {local_sha} · 기준 {base_sha} (reports/gate_report.json)")
            continue
        cmd = [sys.executable, str(ROOT / "harness" / "run_gates.py"), "--role", role, "--base", base_sha]
        run_res = subprocess.run(cmd, cwd=ROOT)
        if run_res.returncode != 0:
            return 1

    return 0


def main() -> int:
    if "--install" in sys.argv:
        return install_hook()
    return run_hook()


if __name__ == "__main__":
    sys.exit(main())
