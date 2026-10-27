"""Orca 워커 실행 한 단계 도구 (WO-H78, HD-80).

등록 실행(orca_execution.py manual-*, P2)과 분리된 하네스 개발 일회성 워커용이며 판정 · 승인을 하지 않는다.
지시서 표현 검사(brief-check), 워커 실행 및 감시 백그라운드 구동(dispatch), 최종 응답 대기(await-final)를 제공한다.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]

# 같은 폴더에서 orca_watch 모듈 가져오기
tools_dir = str(Path(__file__).resolve().parent)
if tools_dir not in sys.path:
    sys.path.insert(0, tools_dir)

import orca_watch

BRIEF_BLOCK_TERMS = (
    "레드팀",
    "red team",
    "red-team",
    "redteam",
    "뚫",
    "위조",
    "attack",
    "bypass",
)


class DispatchArgumentParser(argparse.ArgumentParser):
    """인자 파싱 오류 시 종료 코드 2로 종료하는 인자 파서."""

    def error(self, message: str) -> None:
        print(f"인자 오류: {message}", file=sys.stderr)
        sys.exit(2)


def brief_findings(text: str) -> list[tuple[int, str]]:
    """지시서 텍스트에서 금지 표현이 포함된 줄 번호(1부터)와 걸린 표현 목록을 반환한다."""
    findings: list[tuple[int, str]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        line_cf = line.casefold()
        for term in BRIEF_BLOCK_TERMS:
            if term.casefold() in line_cf:
                findings.append((line_no, term))
    return findings


def cmd_brief_check(args: argparse.Namespace) -> int:
    """brief-check 서브명령 실행."""
    has_findings = False
    findings_by_file: list[tuple[str, list[tuple[int, str]]]] = []

    for file_str in args.files:
        p = Path(file_str)
        if not p.is_file():
            print(f"오류: 파일을 찾을 수 없습니다: {file_str}", file=sys.stderr)
            return 2
        try:
            raw = p.read_bytes()
            text = raw.decode("utf-8")
        except UnicodeDecodeError as e:
            print(f"오류: UTF-8 디코드 실패 ({file_str}): {e}", file=sys.stderr)
            return 2
        except Exception as e:
            print(f"오류: 파일 읽기 실패 ({file_str}): {e}", file=sys.stderr)
            return 2

        findings = brief_findings(text)
        if findings:
            has_findings = True
            findings_by_file.append((file_str, findings))

    if has_findings:
        for file_str, findings in findings_by_file:
            for line_no, term in findings:
                print(f"{file_str}:{line_no}: '{term}'")
        print("중립 문구로 바꾼 뒤 다시 실행하세요 (예: 경계 입력 검증 사례 설계). 자동으로 바꾸지 않습니다.")
        return 1

    print(f"brief-check 통과: {len(args.files)}개 파일")
    return 0


def cmd_dispatch(args: argparse.Namespace) -> int:
    """dispatch 서브명령 실행."""
    # 1. git -C <origin> show <base>:<brief> (capture, utf-8 엄격)
    git_cmd = ["git", "-C", args.origin, "show", f"{args.base}:{args.brief}"]
    git_res = subprocess.run(git_cmd, capture_output=True)
    if git_res.returncode != 0:
        err_msg = git_res.stderr.decode("utf-8", errors="replace").strip()
        print(
            f"오류: 기준 커밋에서 지시서를 읽을 수 없습니다 ({args.base}:{args.brief}): {err_msg}",
            file=sys.stderr,
        )
        return 2

    try:
        brief_text = git_res.stdout.decode("utf-8")
    except UnicodeDecodeError as e:
        print(f"오류: 지시서가 UTF-8 형식이 아닙니다: {e}", file=sys.stderr)
        return 2

    # 2. agent == "codex" 이면 brief_findings 검사
    if args.agent == "codex":
        findings = brief_findings(brief_text)
        if findings:
            for line_no, term in findings:
                print(f"{args.brief}:{line_no}: '{term}'")
            return 1

    # 3. prompt 및 run_orca(worktree create)
    prompt = f"Read {args.brief} in this workspace and do exactly what it says, without asking questions. Answer in Korean."
    orca_agent = "antigravity" if args.agent == "agy" else "codex"
    subargs = [
        "worktree",
        "create",
        "--repo",
        args.repo,
        "--name",
        args.name,
        "--no-parent",
        "--base-branch",
        args.base,
        "--setup",
        "skip",
        "--agent",
        orca_agent,
        "--model",
        args.model,
    ]
    if args.effort:
        subargs.extend(["--effort", args.effort])
    subargs.extend(["--prompt", prompt, "--json"])

    orca_cmd = args.orca if args.orca else orca_watch.get_default_orca()
    res = orca_watch.run_orca(orca_cmd, subargs)

    # 4. create 결과 검증
    if res.returncode != 0:
        print(
            f"오류: orca worktree create 실행 실패 (코드 {res.returncode}): {res.stderr or res.stdout}",
            file=sys.stderr,
        )
        return 1

    try:
        data = json.loads(res.stdout)
    except Exception as e:
        print(f"오류: orca 응답 JSON 디코드 실패: {e}\n{res.stdout}", file=sys.stderr)
        return 1

    if not isinstance(data, dict) or data.get("ok") is not True:
        print(f"오류: orca worktree create 결과 ok 아님: {data}", file=sys.stderr)
        return 1

    def _obj(value: object) -> dict:
        # 응답 구조가 객체가 아니면 빈 값으로 보고 아래 누락 검사에서 거부한다 (리드 보정)
        return value if isinstance(value, dict) else {}

    result = _obj(data.get("result"))
    path = _obj(result.get("worktree")).get("path")
    handle = _obj(_obj(result.get("launch")).get("outcome")).get("handle")
    if not isinstance(path, str) or not isinstance(handle, str):
        path = path if isinstance(path, str) else None
        handle = handle if isinstance(handle, str) else None

    if not handle or not path:
        print(
            f"오류: handle 또는 path 누락: handle={handle}, path={path}",
            file=sys.stderr,
        )
        return 1

    # 5. 감시 인자 목록 및 프로세스 실행
    tools_path = Path(__file__).resolve().parent
    orca_watch_py = tools_path / "orca_watch.py"
    orca_watch_ps1 = tools_path / "orca_watch.ps1"

    watch = [
        sys.executable,
        str(orca_watch_py),
        "--terminal",
        handle,
        "--workspace",
        path,
        "--agent",
        args.agent,
        "--wo",
        args.wo,
        "--role",
        args.role,
        "--cap-seconds",
        str(args.cap_seconds),
    ]
    if args.interval is not None:
        watch.extend(["--interval", str(args.interval)])
    if args.orca:
        watch.extend(["--orca", args.orca])
    if args.transcript_root:
        watch.extend(["--transcript-root", args.transcript_root])
    if args.model:
        watch.extend(["--model", args.model])
    if args.tier:
        watch.extend(["--tier", args.tier])
    if args.risk:
        watch.extend(["--risk", args.risk])
    watch.extend(["--collect-on-exit", "--save-final"])

    if args.no_window:
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
            subprocess.Popen(
                watch,
                cwd=ROOT,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
        else:
            subprocess.Popen(
                watch,
                cwd=ROOT,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
    else:
        if sys.platform == "win32":
            ps_cmd = [
                "powershell",
                "-NoExit",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(orca_watch_ps1),
            ] + watch[2:]
            creationflags = subprocess.CREATE_NEW_CONSOLE
            subprocess.Popen(
                ps_cmd,
                cwd=ROOT,
                creationflags=creationflags,
            )
        else:
            subprocess.Popen(
                watch,
                cwd=ROOT,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )

    # 6. 결과 JSON 및 지시서 존재 여부 확인
    term_prefix = handle[:12]
    log_path = ROOT / "reports" / "watch" / f"{args.wo}-{term_prefix}.log"
    final_path = log_path.parent / f"{log_path.stem}.final.md"
    brief_present = (Path(path) / args.brief).is_file()

    payload = {
        "handle": handle,
        "workspace": path,
        "log": str(log_path),
        "final": str(final_path),
        "brief_present": brief_present,
        "watch": watch,
    }
    print(json.dumps(payload, ensure_ascii=False), flush=True)

    if not brief_present:
        print("작업 공간에 지시서가 없습니다 — 리드가 확인 후 중단", file=sys.stderr)
        return 1

    return 0


def cmd_await_final(args: argparse.Namespace) -> int:
    """await-final 서브명령 실행."""
    final_path = Path(args.final)
    timeout = float(args.timeout_seconds)
    interval = float(args.interval)
    start_time = time.monotonic()
    deadline = start_time + timeout

    while True:
        if final_path.is_file():
            try:
                data = final_path.read_bytes().decode("utf-8")
                sys.stdout.write(data)
                sys.stdout.flush()
                return 0
            except Exception:
                pass
        now = time.monotonic()
        if now >= deadline:
            break
        sleep_sec = min(interval, max(0.0, deadline - now))
        if sleep_sec <= 0:
            break
        time.sleep(sleep_sec)

    if final_path.is_file():
        try:
            data = final_path.read_bytes().decode("utf-8")
            sys.stdout.write(data)
            sys.stdout.flush()
            return 0
        except Exception:
            pass

    print(
        f"오류: final 파일 대기 시간 초과 ({args.timeout_seconds}초): {final_path}",
        file=sys.stderr,
    )
    return 3


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

    parser = DispatchArgumentParser(description="Orca 워커 실행 한 단계 도구")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # 1. brief-check
    p_bc = subparsers.add_parser("brief-check", help="지시서 금지 표현 검사")
    p_bc.add_argument("files", nargs="+", help="검사할 지시서 파일 경로 목록")

    # 2. dispatch
    p_disp = subparsers.add_parser("dispatch", help="워커 런칭 및 감시 실행")
    p_disp.add_argument("--origin", required=True, help="등록 원본 경로")
    p_disp.add_argument("--repo", required=True, help="Orca 저장소 지정")
    p_disp.add_argument("--name", required=True, help="워크트리 이름")
    p_disp.add_argument("--base", required=True, help="기준 커밋")
    p_disp.add_argument("--brief", required=True, help="원본 안 지시서 경로")
    p_disp.add_argument(
        "--agent", required=True, choices=["agy", "codex"], help="에이전트 (agy 또는 codex)"
    )
    p_disp.add_argument("--model", required=True, help="모델 ID")
    p_disp.add_argument("--effort", default=None, help="노력 수준")
    p_disp.add_argument("--wo", required=True, help="작업 지시서 ID")
    p_disp.add_argument("--role", required=True, help="역할")
    p_disp.add_argument("--cap-seconds", type=int, required=True, help="상한 초")
    p_disp.add_argument("--tier", default=None, help="티어")
    p_disp.add_argument("--risk", default=None, help="위험도")
    p_disp.add_argument("--interval", type=int, default=None, help="감시 간격 초")
    p_disp.add_argument("--orca", default=None, help="orca 실행 파일 경로")
    p_disp.add_argument(
        "--transcript-root", default=None, help="작업자 로그 루트 경로"
    )
    p_disp.add_argument(
        "--no-window",
        action="store_true",
        default=False,
        help="창 없이 백그라운드 감시 실행",
    )

    # 3. await-final
    p_af = subparsers.add_parser("await-final", help="최종 응답 대기")
    p_af.add_argument("--final", required=True, help="최종 응답 파일 경로")
    p_af.add_argument(
        "--timeout-seconds", type=int, required=True, help="대기 상한 초"
    )
    p_af.add_argument(
        "--interval", type=float, default=5.0, help="확인 주기 초 (기본: 5.0)"
    )

    args = parser.parse_args()

    if args.subcommand == "brief-check":
        return cmd_brief_check(args)
    if args.subcommand == "dispatch":
        return cmd_dispatch(args)
    if args.subcommand == "await-final":
        return cmd_await_final(args)

    return 2


if __name__ == "__main__":
    sys.exit(main())
