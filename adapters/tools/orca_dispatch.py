"""Orca 워커 실행 한 단계 도구 (WO-H78, HD-80, WO-H85).

등록 실행(orca_execution.py manual-*, P2)과 분리된 하네스 개발 일회성 워커용이며 판정 · 승인을 하지 않는다.
지시서 표현 검사(brief-check), 워커 실행 및 감시 백그라운드 구동(dispatch), 최종 응답 대기(await-final)를 제공한다.
상설 원본에 파일을 올려 기준 커밋을 생성하는 stage와, 워커 작업 공간을 정리하는 release를 제공한다.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
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


def _norm_path(p: str | Path) -> str:
    """경로 비교 정규화: 역슬래시를 슬래시로, 중복 슬래시 제거, 끝 슬래시 제거, 대소문자 무시."""
    s = str(p).replace("\\", "/")
    while "//" in s:
        s = s.replace("//", "/")
    s = s.rstrip("/")
    return s.casefold()


def cmd_stage(args: argparse.Namespace) -> int:
    """stage 서브명령 실행."""
    # 계약 2: 원본을 건드리기 전에 모든 검사 완료 (어긋나면 종료 2)
    # 1. 이름 정규식 검사
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$", args.name):
        print(f"오류: 이름이 규칙에 맞지 않습니다: {args.name!r}", file=sys.stderr)
        return 2

    # 2. ref 해석 검사
    src_path = Path(args.src)
    git_ref_res = subprocess.run(
        ["git", "-C", str(src_path), "rev-parse", "--verify", f"{args.ref}^{{commit}}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if git_ref_res.returncode != 0:
        print(f"오류: ref를 커밋으로 해석할 수 없습니다: {args.ref}", file=sys.stderr)
        return 2
    base_ref_commit = git_ref_res.stdout.strip()

    # 3. 각 file 상대 경로 및 src 내 일반 파일 검사
    files = args.file if args.file is not None else []
    for f_str in files:
        fp = Path(f_str)
        if fp.is_absolute() or bool(fp.drive) or os.path.isabs(f_str):
            print(f"오류: 파일은 상대 경로여야 합니다 (절대/드라이브 경로 금지): {f_str}", file=sys.stderr)
            return 2
        if ".." in fp.parts:
            print(f"오류: 파일 경로에 '..' 조각이 포함될 수 없습니다: {f_str}", file=sys.stderr)
            return 2
        full_src_file = src_path / fp
        if not full_src_file.is_file():
            print(f"오류: --src 작업 트리에 일반 파일이 존재하지 않습니다: {f_str}", file=sys.stderr)
            return 2

    # 4. origin이 이미 존재하면 git 저장소인지 검사
    origin_path = Path(args.origin)
    if origin_path.exists():
        git_check = subprocess.run(
            ["git", "-C", str(origin_path), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if git_check.returncode != 0:
            print(f"오류: 원본 경로가 git 저장소가 아닙니다: {origin_path}", file=sys.stderr)
            return 2
        try:
            if Path(git_check.stdout.strip()).resolve() != origin_path.resolve():
                print(f"오류: 원본 경로가 git 저장소 루트가 아닙니다: {origin_path}", file=sys.stderr)
                return 2
        except Exception:
            print(f"오류: 원본 경로가 유효한 git 저장소가 아닙니다: {origin_path}", file=sys.stderr)
            return 2

    # 계약 3~5: 원본 준비, fetch, checkout, 파일 복사 및 커밋
    if not origin_path.exists():
        origin_path.parent.mkdir(parents=True, exist_ok=True)
        clone_res = subprocess.run(
            ["git", "clone", "-q", "--no-local", str(src_path), str(origin_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if clone_res.returncode != 0:
            print(f"오류: git clone 실패: {clone_res.stderr}", file=sys.stderr)
            return 1

    fetch_res = subprocess.run(
        ["git", "-C", str(origin_path), "fetch", "-q", "--no-tags", str(src_path), base_ref_commit],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if fetch_res.returncode != 0:
        print(f"오류: git fetch 실패: {fetch_res.stderr}", file=sys.stderr)
        return 1

    checkout_res = subprocess.run(
        ["git", "-C", str(origin_path), "checkout", "-q", "-f", "-B", f"stage/{args.name}", base_ref_commit],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if checkout_res.returncode != 0:
        print(f"오류: git checkout 실패: {checkout_res.stderr}", file=sys.stderr)
        return 1

    diff_files = []
    for f_str in files:
        src_f = src_path / f_str
        dst_f = origin_path / f_str
        dst_f.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_f, dst_f)

        cat_res = subprocess.run(
            ["git", "-C", str(origin_path), "cat-file", "-e", f"{base_ref_commit}:{f_str}"],
            capture_output=True,
        )
        if cat_res.returncode != 0:
            diff_files.append(f_str)
        else:
            diff_res = subprocess.run(
                ["git", "-C", str(origin_path), "diff", "--quiet", base_ref_commit, "--", f_str],
                capture_output=True,
            )
            if diff_res.returncode != 0:
                diff_files.append(f_str)

    if diff_files:
        add_res = subprocess.run(
            ["git", "-C", str(origin_path), "add", "--"] + diff_files,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if add_res.returncode != 0:
            print(f"오류: git add 실패: {add_res.stderr}", file=sys.stderr)
            return 1

        commit_res = subprocess.run(
            [
                "git",
                "-C",
                str(origin_path),
                "-c",
                "user.name=KMS",
                "-c",
                "user.email=noreply@local",
                "commit",
                "-qm",
                f"stage {args.name}",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if commit_res.returncode != 0:
            print(f"오류: git commit 실패: {commit_res.stderr}", file=sys.stderr)
            return 1

    head_res = subprocess.run(
        ["git", "-C", str(origin_path), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if head_res.returncode != 0:
        print(f"오류: git rev-parse HEAD 실패: {head_res.stderr}", file=sys.stderr)
        return 1
    base_sha = head_res.stdout.strip()

    # 계약 6, 8: Orca 등록
    origin_resolved = origin_path.resolve()
    origin_abs = str(origin_path) if origin_path.is_absolute() else str(origin_resolved)
    norm_origin = _norm_path(origin_resolved)

    orca_cmd = args.orca if args.orca else orca_watch.get_default_orca()
    res_list = orca_watch.run_orca(orca_cmd, ["repo", "list", "--json"])
    if res_list.returncode != 0:
        print(
            f"오류: orca repo list 실패 (코드 {res_list.returncode}): {res_list.stderr or res_list.stdout}",
            file=sys.stderr,
        )
        return 1

    try:
        data_list = json.loads(res_list.stdout)
    except Exception as e:
        print(f"오류: orca repo list JSON 디코드 실패: {e}\n{res_list.stdout}", file=sys.stderr)
        return 1

    if not isinstance(data_list, dict) or data_list.get("ok") is not True:
        print(f"오류: orca repo list 결과 ok 아님: {data_list}", file=sys.stderr)
        return 1

    repos = data_list.get("result", {}).get("repos", [])
    if not isinstance(repos, list):
        repos = []

    repo_id = None
    for r in repos:
        if isinstance(r, dict) and _norm_path(r.get("path", "")) == norm_origin:
            repo_id = r.get("id")
            break

    registered = False
    if repo_id:
        registered = False
    else:
        res_add = orca_watch.run_orca(orca_cmd, ["repo", "add", "--path", origin_abs, "--json"])
        if res_add.returncode != 0:
            print(
                f"오류: orca repo add 실패 (코드 {res_add.returncode}): {res_add.stderr or res_add.stdout}",
                file=sys.stderr,
            )
            return 1

        try:
            data_add = json.loads(res_add.stdout)
        except Exception as e:
            print(f"오류: orca repo add JSON 디코드 실패: {e}\n{res_add.stdout}", file=sys.stderr)
            return 1

        if not isinstance(data_add, dict) or data_add.get("ok") is not True:
            print(f"오류: orca repo add 결과 ok 아님: {data_add}", file=sys.stderr)
            return 1

        repo_info = data_add.get("result", {}).get("repo", {})
        if not isinstance(repo_info, dict) or not repo_info.get("id"):
            print(f"오류: orca repo add 결과에 id 없음: {data_add}", file=sys.stderr)
            return 1

        repo_id = repo_info["id"]
        registered = True

    payload = {
        "origin": origin_abs,
        "repo": f"id:{repo_id}",
        "base": base_sha,
        "branch": f"stage/{args.name}",
        "registered": registered,
    }
    print(json.dumps(payload, ensure_ascii=False), flush=True)
    return 0


def cmd_release(args: argparse.Namespace) -> int:
    """release 서브명령 실행."""
    # 안전 확인: workspace 부모 폴더 이름과 origin 폴더 이름 전체 일치 (대소문자 무시)
    ws_parent_name = Path(args.workspace).parent.name
    orig_name = Path(args.origin).name
    if ws_parent_name.casefold() != orig_name.casefold():
        print(
            f"오류: 작업 공간 부모 폴더({ws_parent_name})와 원본 이름({orig_name}) 불일치",
            file=sys.stderr,
        )
        return 2

    subargs = ["worktree", "rm", "--worktree", f"path:{args.workspace}", "--json"]
    if args.discard:
        subargs.append("--force")

    orca_cmd = args.orca if args.orca else orca_watch.get_default_orca()
    res = orca_watch.run_orca(orca_cmd, subargs)
    if res.returncode != 0:
        print(
            f"오류: orca worktree rm 실행 실패 (코드 {res.returncode}): {res.stderr or res.stdout}",
            file=sys.stderr,
        )
        return 1

    try:
        data = json.loads(res.stdout)
    except Exception as e:
        print(f"오류: orca worktree rm JSON 디코드 실패: {e}\n{res.stdout}", file=sys.stderr)
        return 1

    if not isinstance(data, dict) or data.get("ok") is not True:
        print(f"오류: orca worktree rm 결과 ok 아님: {data}", file=sys.stderr)
        return 1

    return 0


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

    # 4. stage
    p_stage = subparsers.add_parser("stage", help="상설 원본에 파일을 올려 기준 커밋 생성")
    p_stage.add_argument("--src", default=str(ROOT), help="소스 저장소 경로 (기본: 하네스 루트)")
    p_stage.add_argument(
        "--origin",
        default=str(ROOT / ".jobs" / "dev-origin"),
        help="상설 원본 경로 (기본: <ROOT>/.jobs/dev-origin)",
    )
    p_stage.add_argument("--ref", default="HEAD", help="기준 ref (기본: HEAD)")
    p_stage.add_argument("--name", required=True, help="스테이지 이름")
    p_stage.add_argument(
        "--file",
        action="append",
        default=None,
        dest="file",
        help="스테이지에 복사할 상대 파일 경로 (여러 번 지정 가능)",
    )
    p_stage.add_argument("--orca", default=None, help="orca 실행 파일 경로")

    # 5. release
    p_rel = subparsers.add_parser("release", help="끝난 워커의 작업 공간 정리")
    p_rel.add_argument("--workspace", required=True, help="작업 공간 경로")
    p_rel.add_argument(
        "--origin",
        default=str(ROOT / ".jobs" / "dev-origin"),
        help="상설 원본 경로 (기본: <ROOT>/.jobs/dev-origin)",
    )
    p_rel.add_argument(
        "--discard",
        action="store_true",
        default=False,
        help="커밋되지 않은 변경 사항 무시하고 강제 삭제 (--force)",
    )
    p_rel.add_argument("--orca", default=None, help="orca 실행 파일 경로")

    args = parser.parse_args()

    if args.subcommand == "brief-check":
        return cmd_brief_check(args)
    if args.subcommand == "dispatch":
        return cmd_dispatch(args)
    if args.subcommand == "await-final":
        return cmd_await_final(args)
    if args.subcommand == "stage":
        return cmd_stage(args)
    if args.subcommand == "release":
        return cmd_release(args)

    return 2


if __name__ == "__main__":
    sys.exit(main())
