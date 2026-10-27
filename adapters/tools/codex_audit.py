"""Codex 읽기 전용 점검 실행 감싸개 (WO-H48, WO-H55).

Codex를 '-s read-only' 샌드박스로 실행하고 결과를 출력 파일에 기록한다.
작업 사본의 변경 여부를 실행 전·후로 확인하여 환경 격리와 정합성을 보장한다.

사용법:
  python adapters/tools/codex_audit.py --workspace <git 작업 사본> --prompt-file <파일> \
    --out <파일> [--model gpt-6-luna] [--effort medium] [--cap-seconds 900] \
    [--codex-bin codex] [--print-plan]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys


class AuditArgumentParser(argparse.ArgumentParser):
    """인자 파싱 오류 시 종료 코드 2로 종료하는 인자 파서."""

    def error(self, message: str) -> None:
        print(f"인자 오류: {message}", file=sys.stderr)
        sys.exit(2)


class WorkspaceReadError(Exception):
    """작업 사본 파일 읽기 실패 시 발생하는 예외."""

    def __init__(self, path: str, reason: str) -> None:
        super().__init__(f"{path}: {reason}")
        self.path = path
        self.reason = reason


def build_workspace_hashes(workspace: Path) -> dict[str, str]:
    """작업 사본의 모든 파일(.git/ 아래 제외, 하위 모듈 작업 트리 포함)의 상대 경로 -> SHA-256 해시 표를 반환한다."""
    table: dict[str, str] = {}

    def on_walk_error(err: OSError) -> None:
        path_obj = Path(err.filename) if err.filename else workspace
        try:
            rel = path_obj.relative_to(workspace).as_posix()
        except Exception:
            rel = str(path_obj).replace("\\", "/")
        raise WorkspaceReadError(rel, str(err))

    for root, dirs, files in os.walk(workspace, onerror=on_walk_error):
        dirs[:] = [d for d in dirs if d != ".git"]
        for f in files:
            if f == ".git":
                continue
            file_path = Path(root) / f
            try:
                rel_path = file_path.relative_to(workspace).as_posix()
            except ValueError:
                try:
                    rel_path = file_path.resolve().relative_to(workspace.resolve()).as_posix()
                except Exception:
                    rel_path = str(file_path).replace("\\", "/")

            try:
                if not file_path.is_file():
                    # 열거한 파일이 사라졌으면 표가 불완전하다 → 읽기 실패로 본다 (리드 보정, Sol 검토 C16)
                    if not file_path.exists() and not file_path.is_symlink():
                        raise WorkspaceReadError(rel_path, "열거 뒤 사라짐")
                    continue
                h = hashlib.sha256()
                with open(file_path, "rb") as fp:
                    while chunk := fp.read(65536):
                        h.update(chunk)
                table[rel_path] = h.hexdigest()
            except OSError as e:
                raise WorkspaceReadError(rel_path, str(e))

    return table


BANNED_SUBSTRINGS = (
    "workspace-write",
    "danger-full-access",
    "--dangerously",
    "--full-auto",
)


def get_git_status(workspace: Path) -> tuple[int, str]:
    """작업 사본의 기본 git status 결과를 반환한다 (사전 검사용)."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(workspace), "status", "--porcelain", "--untracked-files=all"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return proc.returncode, proc.stdout
    except Exception as e:
        return -1, str(e)


def get_detailed_git_status(workspace: Path) -> tuple[int, str]:
    """실행 전·후 비교를 위한 상세 git 상태 (ignored 및 submodule 포함)를 반환한다."""
    proc1 = subprocess.run(
        [
            "git",
            "-C",
            str(workspace),
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--ignored",
            "--ignore-submodules=none",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc1.returncode != 0:
        return proc1.returncode, proc1.stderr or proc1.stdout

    proc2 = subprocess.run(
        [
            "git",
            "-C",
            str(workspace),
            "submodule",
            "foreach",
            "--recursive",
            "--quiet",
            "git status --porcelain --untracked-files=all --ignored",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc2.returncode != 0:
        return proc2.returncode, proc2.stderr or proc2.stdout

    combined = proc1.stdout
    if proc2.stdout:
        if combined and not combined.endswith("\n"):
            combined += "\n"
        combined += proc2.stdout
    return 0, combined


def main() -> None:
    parser = AuditArgumentParser(description="Codex 읽기 전용 점검 실행 감싸개")
    parser.add_argument("--workspace", required=True, help="git 작업 사본 경로")
    parser.add_argument("--prompt-file", required=True, help="프롬프트 파일 경로")
    parser.add_argument("--out", required=True, help="결과 출력 파일 경로")
    parser.add_argument("--model", default="gpt-6-luna", help="모델 (기본값: gpt-6-luna)")
    parser.add_argument("--effort", default="medium", help="추론 노력 수준 (기본값: medium)")
    parser.add_argument("--cap-seconds", type=int, default=900, help="상한 초 (기본값: 900)")
    parser.add_argument("--codex-bin", default="codex", help="codex 실행 바이너리 (기본값: codex)")
    parser.add_argument("--print-plan", action="store_true", help="실행 계획 출력 후 종료")

    args = parser.parse_args()

    # 인자 검사: --model, --effort 가 '-' 로 시작하면 종료 2
    if args.model.startswith("-") or args.effort.startswith("-"):
        print("인자 오류: --model 및 --effort 값은 '-'로 시작할 수 없습니다.", file=sys.stderr)
        sys.exit(2)

    # B07: --effort 는 low|medium|high|xhigh 만 허용
    if args.effort not in ("low", "medium", "high", "xhigh"):
        print(f"인자 오류: --effort 는 low, medium, high, xhigh 만 허용됩니다: {args.effort}", file=sys.stderr)
        sys.exit(2)

    # B07: --model 은 정규식 [A-Za-z0-9._-]+ 전체 일치만 허용
    if not re.fullmatch(r"[A-Za-z0-9._-]+", args.model):
        print(f"인자 오류: --model 은 [A-Za-z0-9._-]+ 패턴과 일치해야 합니다: {args.model}", file=sys.stderr)
        sys.exit(2)

    # B05: --cap-seconds 가 1 이상이 아니면 종료 2
    if args.cap_seconds < 1:
        print(f"인자 오류: --cap-seconds 는 1 이상이어야 합니다: {args.cap_seconds}", file=sys.stderr)
        sys.exit(2)

    # 금지 인자 검사
    for banned in BANNED_SUBSTRINGS:
        if any(banned in str(val) for val in (args.model, args.effort, args.codex_bin)):
            print(f"인자 오류: 금지된 인자 포함 ({banned})", file=sys.stderr)
            sys.exit(2)

    # 사전 검사 1: prompt-file 이 있고 비어 있지 않음
    prompt_path = Path(args.prompt_file)
    if not prompt_path.is_file():
        print(f"사전 검사 실패: 프롬프트 파일이 존재하지 않습니다: {args.prompt_file}", file=sys.stderr)
        sys.exit(2)
    try:
        prompt_content = prompt_path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"사전 검사 실패: 프롬프트 파일 읽기 오류: {e}", file=sys.stderr)
        sys.exit(2)
    if not prompt_content.strip():
        print("사전 검사 실패: 프롬프트 파일이 비어 있습니다.", file=sys.stderr)
        sys.exit(2)

    # 사전 검사 2: workspace 가 디렉터리로 존재
    ws_path = Path(args.workspace)
    if not ws_path.is_dir():
        print(f"사전 검사 실패: 작업 공간이 디렉터리가 아닙니다: {args.workspace}", file=sys.stderr)
        sys.exit(2)

    # 사전 검사 3: --out 이 workspace 밖
    out_path = Path(args.out)
    try:
        out_resolved = out_path.resolve()
        ws_resolved = ws_path.resolve()
        out_resolved.relative_to(ws_resolved)
        print("사전 검사 실패: --out 이 작업 사본 안에 있습니다.", file=sys.stderr)
        sys.exit(2)
    except ValueError:
        pass

    # B04: --out 이 이미 있으면 종료 2
    if out_path.exists():
        print(f"사전 검사 실패: --out 파일이 이미 존재합니다: {args.out}", file=sys.stderr)
        sys.exit(2)

    # 사전 검사 4: workspace 가 git 작업 사본이고, git status --porcelain --untracked-files=all 이 비어 있음
    pre_code, pre_status = get_git_status(ws_path)
    if pre_code != 0:
        print(f"사전 검사 실패: workspace 가 git 작업 사본이 아닙니다: {pre_status}", file=sys.stderr)
        sys.exit(2)
    if pre_status.strip():
        print(f"사전 검사 실패: 작업 사본이 깨끗하지 않습니다.\n{pre_status}", file=sys.stderr)
        sys.exit(2)

    # B02: git -C <ws> ls-files -v 에서 첫 글자가 'S' 이거나 소문자인 줄이 있으면 종료 2
    ls_res = subprocess.run(
        ["git", "-C", str(ws_path), "ls-files", "-v"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if ls_res.returncode != 0:
        print(f"사전 검사 실패: git ls-files 오류: {ls_res.stderr}", file=sys.stderr)
        sys.exit(2)
    flagged = [line for line in ls_res.stdout.splitlines() if line and (line[0] == "S" or line[0].islower())]
    if flagged:
        print(f"인덱스 플래그: skip-worktree 또는 assume-unchanged 플래그가 설정된 파일이 있습니다.\n" + "\n".join(flagged), file=sys.stderr)
        sys.exit(2)

    # B01, B03: 실행 전 상세 git 상태 기록 (ignored 포함)
    pre_detailed_code, pre_detailed_status = get_detailed_git_status(ws_path)
    if pre_detailed_code != 0:
        print(f"사전 검사 실패: 작업 사본 상태 확인 실패: {pre_detailed_status}", file=sys.stderr)
        sys.exit(2)

    # 실행 명령 구성
    cmd: list[str] = []
    if args.codex_bin.endswith(".py"):
        cmd.extend([sys.executable, args.codex_bin])
    else:
        cmd.append(args.codex_bin)

    cmd.extend([
        "exec",
        "-s",
        "read-only",
        "-m",
        args.model,
        "-c",
        f'model_reasoning_effort="{args.effort}"',
        "-C",
        str(args.workspace),
        "-o",
        str(args.out),
        prompt_content,
    ])

    # 명령 검증
    joined_cmd = " ".join(cmd)
    for banned in BANNED_SUBSTRINGS:
        if banned in joined_cmd:
            print(f"오류: 금지된 인자가 명령에 포함되어 있습니다 ({banned})", file=sys.stderr)
            sys.exit(2)

    s_indices = [i for i, x in enumerate(cmd) if x == "-s"]
    if len(s_indices) != 1 or cmd[s_indices[0] + 1] != "read-only":
        print("오류: '-s read-only'가 정확히 한 번 포함되어야 합니다.", file=sys.stderr)
        sys.exit(2)

    # --print-plan 처리
    if args.print_plan:
        print(json.dumps({"cmd": cmd}))
        sys.exit(0)

    # 실행 전 작업 사본 파일 해시 표 구성
    try:
        pre_hashes = build_workspace_hashes(ws_path)
    except WorkspaceReadError as e:
        print(f"파일을 읽을 수 없음: {e.path}", file=sys.stderr)
        sys.exit(2)

    # 실행 (B06: OSError 시 "실행할 수 없음: ...", 종료 2)
    try:
        if sys.platform == "win32":
            proc = subprocess.Popen(cmd)
        else:
            proc = subprocess.Popen(cmd, start_new_session=True)
    except OSError as e:
        print(f"실행할 수 없음: {e}", file=sys.stderr)
        sys.exit(2)

    timed_out = False
    try:
        proc.wait(timeout=args.cap_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        if sys.platform == "win32":
            kill_res = subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True,
                check=False,
            )
            if kill_res.returncode != 0:
                proc.kill()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
                print("트리 종료 실패: 직접 자식만 끝냄", file=sys.stderr)
            else:
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
        else:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                proc.kill()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass

    # 1. 실행 뒤 작업 사본 파일 해시 표 구성 및 비교 (WO-H55)
    try:
        post_hashes = build_workspace_hashes(ws_path)
    except WorkspaceReadError as e:
        print(f"파일을 읽을 수 없음: {e.path}", file=sys.stderr)
        sys.exit(1)

    diff_paths = sorted(
        path for path in (set(pre_hashes) ^ set(post_hashes))
        | {path for path in pre_hashes if path in post_hashes and pre_hashes[path] != post_hashes[path]}
    )
    if diff_paths:
        print("작업 사본 변경:", file=sys.stderr)
        for p in diff_paths:
            print(p, file=sys.stderr)
        sys.exit(1)

    # 2. B08: 실행 뒤 git 상태 확인 (.git/ 내부 변경 또는 상태 확인 실패 시 종료 1)
    post_detailed_code, post_detailed_status = get_detailed_git_status(ws_path)
    if post_detailed_code != 0:
        print(f"작업 사본 상태 확인 실패: {post_detailed_status}", file=sys.stderr)
        sys.exit(1)

    # 3. git 상태 비교도 유지 (리드 보정, Sol 검토 C17 · C18): 내용이 같아도 인덱스 변경(rm --cached · 실행 비트)은 변경이다
    if post_detailed_status != pre_detailed_status:
        pre_lines = set(pre_detailed_status.splitlines())
        post_lines = post_detailed_status.splitlines()
        changed = [line for line in post_lines if line not in pre_lines]
        changed += [f"- {line}" for line in pre_lines if line not in set(post_lines)]
        print("작업 사본 변경 (git 상태):", file=sys.stderr)
        for line in changed:
            print(line, file=sys.stderr)
        sys.exit(1)

    # 상한 초과인 경우 (작업 사본 변경 없음)
    if timed_out:
        print("상한 초과: 프로세스를 끝냈습니다.", file=sys.stderr)
        sys.exit(3)

    # 실행 뒤 검사 2: codex 종료 코드가 0이 아니면 종료 1
    codex_code = proc.returncode
    if codex_code != 0:
        print(f"codex 실패 종료 (종료 코드 {codex_code})", file=sys.stderr)
        sys.exit(1)

    # 실행 뒤 검사 3: --out 이 없거나 비면 종료 1 + "결과 없음"
    if not out_path.is_file() or out_path.stat().st_size == 0:
        print("결과 없음: 출력 파일이 생성되지 않았거나 비어 있습니다.", file=sys.stderr)
        sys.exit(1)

    try:
        out_text = out_path.read_text(encoding="utf-8", errors="replace")
        if not out_text.strip():
            print("결과 없음: 출력 파일 내용이 비어 있습니다.", file=sys.stderr)
            sys.exit(1)
    except Exception as e:
        print(f"결과 없음: 출력 파일 읽기 실패: {e}", file=sys.stderr)
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
