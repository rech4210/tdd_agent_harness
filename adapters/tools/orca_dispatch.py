"""Orca 워커 실행 한 단계 도구 (WO-H78, HD-80, WO-H85, WO-H86, WO-H90).

등록 실행(orca_execution.py manual-*, P2)과 분리된 하네스 개발 일회성 워커용이며 판정 · 승인을 하지 않는다.
지시서 표현 검사(brief-check), 워커 실행 및 감시 백그라운드 구동(dispatch), 최종 응답 대기(await-final)를 제공한다.
프로젝트별 상설 원본에 파일을 올려 기준 커밋을 생성하는 stage와, 저장소 정체성을 확인해 워커 작업 공간을 정리하는 release를 제공한다.
워커 판정 전 피드백을 전달하고 새 회차 감시를 구동하는 feedback을 제공한다.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]

# Windows: 창 없는 감시 · 위임에서 자식(git · python · orca)이 콘솔 창을 잠깐 띄우지 않게 한다 (WO-H91)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# 피드백 메시지에서 앞에 모을 실패 표시 줄: 사례 결과(BAD · FAIL · ERROR) · 트레이스백 · 예외 줄 (WO-H94)
FAIL_MARK = re.compile(r"^(?:BAD|FAIL|ERROR)\b|^Traceback \(most recent call last\)|^\w*(?:Error|Exception)\b:")

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


def _launch_watch(watch: list[str], no_window: bool) -> None:
    """감시 프로세스를 창 또는 분리 프로세스로 실행한다."""
    if no_window:
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
                creationflags=NO_WINDOW,
            )
    else:
        if sys.platform == "win32":
            tools_path = Path(__file__).resolve().parent
            orca_watch_ps1 = tools_path / "orca_watch.ps1"
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
                creationflags=NO_WINDOW,
            )


def cmd_dispatch(args: argparse.Namespace) -> int:
    """dispatch 서브명령 실행."""
    # 1. git -C <origin> show <base>:<brief> (capture, utf-8 엄격)
    git_cmd = ["git", "-C", args.origin, "show", f"{args.base}:{args.brief}"]
    git_res = subprocess.run(git_cmd, capture_output=True, creationflags=NO_WINDOW)
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

    _launch_watch(watch, bool(args.no_window))

    # 6. 결과 JSON 및 지시서 존재 여부 확인
    term_prefix = handle[:12]
    log_path = ROOT / "reports" / "watch" / f"{args.wo}-{term_prefix}.log"
    final_path = log_path.parent / f"{log_path.stem}.final.md"
    state_path = log_path.parent / f"{log_path.stem}.dispatch.json"
    brief_present = (Path(path) / args.brief).is_file()

    payload = {
        "handle": handle,
        "workspace": path,
        "log": str(log_path),
        "final": str(final_path),
        "state": str(state_path),
        "brief_present": brief_present,
        "watch": watch,
    }

    state_data = dict(payload)
    state_data.update({
        "agent": args.agent,
        "model": args.model,
        "wo": args.wo,
        "role": args.role,
        "cap_seconds": args.cap_seconds,
        "no_window": bool(args.no_window),
        "round": 0,
    })
    if args.orca:
        state_data["orca"] = args.orca

    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state_data, ensure_ascii=False), encoding="utf-8")

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


def _default_origin(src: Path) -> Path:
    """--src 기준 기본 상설 원본 경로 (<src>/.jobs/<src 이름>-dev-origin)."""
    return src / ".jobs" / f"{src.resolve().name}-dev-origin"


def _find_repo_id(orca_cmd: str | None, origin_path: Path) -> tuple[str | None, str | None]:
    """Orca 저장소 목록에서 origin_path와 경로가 일치하는 저장소 id를 찾는다."""
    res_list = orca_watch.run_orca(orca_cmd, ["repo", "list", "--json"])
    if res_list.returncode != 0:
        err = (res_list.stderr or res_list.stdout).strip()
        return None, f"orca repo list 실패 (코드 {res_list.returncode}): {err}"

    try:
        data_list = json.loads(res_list.stdout)
    except Exception as e:
        return None, f"orca repo list JSON 디코드 실패: {e}\n{res_list.stdout}"

    if not isinstance(data_list, dict) or data_list.get("ok") is not True:
        return None, f"orca repo list 결과 ok 아님: {data_list}"

    repos = data_list.get("result", {}).get("repos", [])
    if not isinstance(repos, list):
        repos = []

    norm_origin = _norm_path(origin_path.resolve())
    for r in repos:
        if isinstance(r, dict) and _norm_path(r.get("path", "")) == norm_origin:
            rid = r.get("id")
            if rid:
                return str(rid), None

    return None, None


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
        creationflags=NO_WINDOW,
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

    # 원본 경로 결정
    origin_path = Path(args.origin or _default_origin(src_path))
    src_resolved = src_path.resolve()
    origin_resolved = origin_path.resolve()

    # 계약 2: 원본이 --src 안에 있을 때 git 무시 대상 검사
    is_inside = False
    rel_to_src = None
    try:
        rel = origin_resolved.relative_to(src_resolved)
        if rel != Path("."):
            is_inside = True
            rel_to_src = rel
    except ValueError:
        norm_origin = _norm_path(origin_resolved)
        norm_src = _norm_path(src_resolved)
        if norm_origin.startswith(norm_src + "/"):
            is_inside = True
            rel_to_src = Path(norm_origin[len(norm_src) + 1 :])

    if is_inside and rel_to_src is not None:
        rel_slash = rel_to_src.as_posix()
        chk_res = subprocess.run(
            ["git", "-C", str(src_path), "check-ignore", "-q", "--", rel_slash],
            capture_output=True,
            creationflags=NO_WINDOW,
        )
        if chk_res.returncode != 0:
            print(
                f"오류: 원본 경로가 --src의 git 무시 대상이 아닙니다 ({rel_slash})",
                file=sys.stderr,
            )
            return 2

    # 4. origin이 이미 존재하면 git 저장소인지 검사
    if origin_path.exists():
        git_check = subprocess.run(
            ["git", "-C", str(origin_path), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=NO_WINDOW,
        )
        if git_check.returncode != 0:
            print(f"오류: 원본 경로가 git 저장소가 아닙니다: {origin_path}", file=sys.stderr)
            return 2
        try:
            if Path(git_check.stdout.strip()).resolve() != origin_resolved:
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
            creationflags=NO_WINDOW,
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
        creationflags=NO_WINDOW,
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
        creationflags=NO_WINDOW,
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
            creationflags=NO_WINDOW,
        )
        if cat_res.returncode != 0:
            diff_files.append(f_str)
        else:
            diff_res = subprocess.run(
                ["git", "-C", str(origin_path), "diff", "--quiet", base_ref_commit, "--", f_str],
                capture_output=True,
                creationflags=NO_WINDOW,
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
            creationflags=NO_WINDOW,
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
            creationflags=NO_WINDOW,
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
        creationflags=NO_WINDOW,
    )
    if head_res.returncode != 0:
        print(f"오류: git rev-parse HEAD 실패: {head_res.stderr}", file=sys.stderr)
        return 1
    base_sha = head_res.stdout.strip()

    # 계약 6, 8: Orca 등록
    origin_abs = str(origin_path) if origin_path.is_absolute() else str(origin_resolved)

    orca_cmd = args.orca if args.orca else orca_watch.get_default_orca()
    repo_id, err = _find_repo_id(orca_cmd, origin_path)
    if err:
        print(f"오류: {err}", file=sys.stderr)
        return 1

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
    src_path = Path(args.src)
    origin_path = Path(args.origin or _default_origin(src_path))
    orca_cmd = args.orca if args.orca else orca_watch.get_default_orca()

    # (a) 원본의 Orca 저장소 id 찾기
    repo_id, err = _find_repo_id(orca_cmd, origin_path)
    if err:
        print(f"오류: {err}", file=sys.stderr)
        return 1
    if repo_id is None:
        print(
            f"오류: 원본 저장소가 Orca에 등록되어 있지 않습니다: {origin_path}",
            file=sys.stderr,
        )
        return 2

    # (b) worktree show 로 작업 공간 정보 확인
    res_show = orca_watch.run_orca(
        orca_cmd, ["worktree", "show", "--worktree", f"path:{args.workspace}", "--json"]
    )
    if res_show.returncode != 0:
        err_msg = (res_show.stderr or res_show.stdout).strip()
        print(
            f"오류: orca worktree show 실패 (코드 {res_show.returncode}): {err_msg}",
            file=sys.stderr,
        )
        return 2

    try:
        data_show = json.loads(res_show.stdout)
    except Exception as e:
        print(
            f"오류: orca worktree show JSON 디코드 실패: {e}\n{res_show.stdout}",
            file=sys.stderr,
        )
        return 2

    if not isinstance(data_show, dict) or data_show.get("ok") is not True:
        print(f"오류: orca worktree show 결과 ok 아님: {data_show}", file=sys.stderr)
        return 2

    result = data_show.get("result", {})
    wt_info = result.get("worktree", {}) if isinstance(result, dict) else {}
    if not isinstance(wt_info, dict):
        print(f"오류: worktree show 결과에 worktree 정보 없음: {data_show}", file=sys.stderr)
        return 2

    # (c) result.worktree.repoId != 원본 id 검사
    wt_repo_id = wt_info.get("repoId")
    if wt_repo_id != repo_id:
        print(
            f"오류: 작업 공간 저장소 ID({wt_repo_id})가 원본 ID({repo_id})와 일치하지 않습니다",
            file=sys.stderr,
        )
        return 2

    # (d) result.worktree.isMainWorktree 가 true 인지 검사
    if bool(wt_info.get("isMainWorktree")):
        print(
            "오류: 메인 작업 공간(isMainWorktree=true)은 삭제할 수 없습니다",
            file=sys.stderr,
        )
        return 2

    # (e) 통과 시 worktree rm 실행
    subargs = ["worktree", "rm", "--worktree", f"path:{args.workspace}", "--json"]
    if args.discard:
        subargs.append("--force")

    res_rm = orca_watch.run_orca(orca_cmd, subargs)
    if res_rm.returncode != 0:
        print(
            f"오류: orca worktree rm 실행 실패 (코드 {res_rm.returncode}): {res_rm.stderr or res_rm.stdout}",
            file=sys.stderr,
        )
        return 1

    try:
        data_rm = json.loads(res_rm.stdout)
    except Exception as e:
        print(f"오류: orca worktree rm JSON 디코드 실패: {e}\n{res_rm.stdout}", file=sys.stderr)
        return 1

    if not isinstance(data_rm, dict) or data_rm.get("ok") is not True:
        print(f"오류: orca worktree rm 결과 ok 아님: {data_rm}", file=sys.stderr)
        return 1

    return 0


def cmd_feedback(args: argparse.Namespace) -> int:
    """feedback 서브명령 실행 (WO-H90)."""
    # 1. 상태 파일 존재 및 JSON 검증 (계약 2)
    state_file = Path(args.state)
    if not state_file.is_file():
        print(f"오류: 상태 파일을 찾을 수 없습니다: {args.state}", file=sys.stderr)
        return 2

    try:
        state_data = json.loads(state_file.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"오류: 상태 파일이 JSON 형식이 아닙니다: {e}", file=sys.stderr)
        return 2

    if not isinstance(state_data, dict):
        print("오류: 상태 파일 내용이 JSON 객체가 아닙니다", file=sys.stderr)
        return 2

    workspace = state_data.get("workspace")
    handle = state_data.get("handle")
    watch = state_data.get("watch")

    if not workspace or not handle or not isinstance(watch, list):
        print(
            "오류: 상태 파일에 필수 필드(workspace, handle, watch)가 누락되었습니다",
            file=sys.stderr,
        )
        return 2

    # 2. 회차 및 상한 검증 (계약 2)
    if args.max_rounds < 1 or args.max_rounds > 3:
        print(f"오류: --max-rounds는 1~3 사이여야 합니다: {args.max_rounds}", file=sys.stderr)
        return 2

    current_round = int(state_data.get("round", 0))
    next_round = current_round + 1
    if next_round > args.max_rounds:
        print(f"오류: 회차 상한 초과 ({next_round} > {args.max_rounds})", file=sys.stderr)
        return 2

    if not args.run:
        print("오류: --run 인자가 하나 이상 필요합니다", file=sys.stderr)
        return 2

    # 실패 전송은 리드가 실패를 분류한 뒤에만 (WO-H92): 사례 결함까지 보내면 워커가 제품 코드로 사례를 맞춘다(H89)
    if args.send and (not isinstance(args.triage, str) or not args.triage.strip()):
        print("오류: --send 에는 --triage \"<분류 근거>\" 가 필요합니다 (구현자 원인 실패만 보낸다)", file=sys.stderr)
        return 2

    if args.timeout_seconds < 1:
        print(f"오류: --timeout-seconds는 1 이상이어야 합니다: {args.timeout_seconds}", file=sys.stderr)
        return 2

    # 3. --run 검사 (계약 2, 3, 7)
    ws_path = Path(workspace)
    ws_resolved = ws_path.resolve()

    parsed_runs: list[tuple[str, Path, list[str]]] = []
    for run_cmd_str in args.run:
        try:
            tokens = shlex.split(run_cmd_str, posix=True)
        except Exception as e:
            print(f"오류: --run 파싱 실패: {e}", file=sys.stderr)
            return 2

        if not tokens:
            print("오류: --run 명령이 비어 있습니다", file=sys.stderr)
            return 2

        first_token = tokens[0]
        token_path = Path(first_token)

        if token_path.is_absolute() or bool(token_path.drive) or os.path.isabs(first_token):
            print(f"오류: 상대 경로여야 합니다 (절대 경로 금지): {first_token}", file=sys.stderr)
            return 2

        if ".." in token_path.parts:
            print(f"오류: '..' 경로는 허용되지 않습니다: {first_token}", file=sys.stderr)
            return 2

        if not first_token.endswith(".py"):
            print(f"오류: .py 파일이어야 합니다: {first_token}", file=sys.stderr)
            return 2

        target_path = ws_path / token_path
        if not target_path.is_file():
            print(f"오류: 파일을 찾을 수 없습니다: {target_path}", file=sys.stderr)
            return 2

        target_resolved = target_path.resolve()
        try:
            rel = target_resolved.relative_to(ws_resolved)
            if rel == Path("."):
                print(f"오류: 유효한 파일 경로가 아닙니다: {first_token}", file=sys.stderr)
                return 2
        except ValueError:
            print(f"오류: 경로가 작업 공간 밖을 가리킵니다: {first_token}", file=sys.stderr)
            return 2

        parsed_runs.append((run_cmd_str, target_resolved, tokens[1:]))

    # 4. 실행 전 git status --porcelain (계약 4)
    git_before = subprocess.run(
        ["git", "-C", str(ws_path), "status", "--porcelain"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=NO_WINDOW,
    )
    before_status = git_before.stdout

    # 5. 각 명령 실행 (계약 3, 4)
    exec_env = dict(os.environ, PYTHONIOENCODING="utf-8")
    results: list[dict] = []
    all_passed = True

    for run_cmd_str, target_resolved, rest in parsed_runs:
        try:
            proc = subprocess.run(
                [sys.executable, str(target_resolved), *rest],
                cwd=str(ws_path),
                capture_output=True,
                timeout=args.timeout_seconds,
                env=exec_env,
                creationflags=NO_WINDOW,
            )
            out_str = proc.stdout.decode("utf-8", errors="replace") if proc.stdout else ""
            err_str = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""
            combined = out_str + err_str
            rc = proc.returncode
            timed_out = False
            passed = (rc == 0)
        except subprocess.TimeoutExpired as te:
            out_str = (
                te.stdout.decode("utf-8", errors="replace")
                if isinstance(te.stdout, bytes)
                else str(te.stdout or "")
            )
            err_str = (
                te.stderr.decode("utf-8", errors="replace")
                if isinstance(te.stderr, bytes)
                else str(te.stderr or "")
            )
            combined = out_str + err_str
            rc = None
            timed_out = True
            passed = False

        if not passed:
            all_passed = False

        results.append({
            "cmd": run_cmd_str,
            "returncode": rc,
            "passed": passed,
            "timed_out": timed_out,
            "output": combined,
        })

    # 6. 실행 후 git status --porcelain (계약 4)
    git_after = subprocess.run(
        ["git", "-C", str(ws_path), "status", "--porcelain"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=NO_WINDOW,
    )
    after_status = git_after.stdout
    workspace_changed = (before_status != after_status)

    # 7. 출력 파일 텍스트 조립 (계약 8, F20, F21)
    output_blocks: list[str] = []
    for r in results:
        output_blocks.append(f"$ {r['cmd']}")
        if r["timed_out"]:
            if r["output"]:
                output_blocks.append(r["output"].rstrip())
            output_blocks.append("시간 초과")
        else:
            if r["output"]:
                output_blocks.append(r["output"].rstrip())
    full_output_text = "\n".join(output_blocks) + "\n"

    # 경로 결정
    reports_dir = ROOT / "reports" / "watch"
    log_in_state = state_data.get("log")
    if log_in_state:
        reports_dir = Path(log_in_state).parent
    reports_dir.mkdir(parents=True, exist_ok=True)

    term_prefix = handle[:12]
    wo = str(state_data.get("wo", ""))

    # 8. 모두 통과 시 (계약 5, 8)
    if all_passed:
        pass_path = reports_dir / f"{wo}-{term_prefix}.r{next_round}.pass.txt"
        pass_path.write_text(full_output_text, encoding="utf-8")

        payload = {
            "round": next_round,
            "passed": True,
            "sent": False,
            "workspace_changed": workspace_changed,
            "results": results,
        }
        print(json.dumps(payload, ensure_ascii=False), flush=True)
        return 0

    # 9. 하나라도 실패 시 메시지 조립 (계약 6)
    feedback_path = reports_dir / f"{wo}-{term_prefix}.r{next_round}.feedback.txt"
    feedback_path.write_text(full_output_text, encoding="utf-8")

    header = (
        f"[피드백 {next_round}/{args.max_rounds}]\n"
        "하네스가 작업 공간 밖에서 실행한 확인 결과입니다.\n"
        "사례 · 테스트 파일은 고치지 말고 허용 경로 안에서만 고친 뒤, 명령은 실행하지 말고 바꾼 내용을 보고하라.\n"
        "사례 이름 · 호출 스택 · 실행 환경을 보고 동작을 바꾸지 말 것. 사례가 틀렸다고 보이면 고치지 말고 그 근거를 보고하라.\n"
    )

    cmd_blocks: list[dict[str, str]] = []
    for r in results:
        cmd = r["cmd"]
        if r["timed_out"]:
            st = f"{cmd} → 시간 초과"
        elif r["passed"]:
            st = f"{cmd} → 통과"
        else:
            st = f"{cmd} → 종료 {r['returncode']}"

        tail = ""
        marks: list[str] = []
        if not r["passed"]:
            all_lines = r["output"].splitlines()
            tail = "\n".join(all_lines[-60:])
            # 끝 60줄 밖의 실패 표시 줄을 앞에 모은다 — 사례마다 OK 줄을 찍는 검사에서 앞쪽 BAD 가 잘리지 않게 (WO-H94)
            marks = [ln[:300] for ln in all_lines if FAIL_MARK.search(ln)][:40]
        cmd_blocks.append({"status": st, "output": tail, "marks": marks})

    def render_msg(blocks: list[dict]) -> str:
        parts = [header]
        for b in blocks:
            parts.append(b["status"])
            if b["marks"]:
                parts.append("실패 표시 줄:")
                parts.extend(b["marks"])
                parts.append("출력 끝:")
            if b["output"]:
                parts.append(b["output"])
            parts.append("")
        return "\n".join(parts).rstrip() + "\n"

    msg = render_msg(cmd_blocks)
    while len(msg) > 8000:
        excess = len(msg) - 8000
        longest = max(cmd_blocks, key=lambda b: len(b["output"]))
        if longest["output"]:
            if len(longest["output"]) <= excess:
                longest["output"] = ""
            else:
                longest["output"] = longest["output"][excess:]
        else:
            # 출력 끝을 다 비워도 넘치면 표시 줄을 뒤에서부터 줄인다(첫 표시 줄은 남긴다)
            most = max(cmd_blocks, key=lambda b: len(b["marks"]))
            if len(most["marks"]) <= 1:
                break
            most["marks"].pop()
        msg = render_msg(cmd_blocks)
    if len(msg) > 8000:
        # 명령 수 × 표시 줄 한 줄만으로도 넘치는 경우: 명령마다 상태 줄과 첫 표시 줄(앞부분)을 남기도록
        # 표시 줄을 예산에 맞춰 자른다 (마일스톤 검토 A05 · 재확인)
        marked = [b for b in cmd_blocks if b["marks"]]
        for b in cmd_blocks:
            b["output"] = ""
        fixed = len(render_msg([dict(b, marks=[""] if b["marks"] else []) for b in cmd_blocks]))
        budget = max(8, (8000 - fixed) // max(1, len(marked)) - 1)
        for b in marked:
            b["marks"] = [b["marks"][0][:budget]]
        msg = render_msg(cmd_blocks)
        if len(msg) > 8000:
            msg = msg[:7980].rstrip() + "\n…(8000자 상한에서 잘림)\n"

    # 리드 분류 전: 보낼 메시지만 남기고 전송 · 새 감시 · 상태 갱신 없음 (WO-H92)
    if not args.send:
        message_path = reports_dir / f"{wo}-{term_prefix}.r{next_round}.message.txt"
        message_path.write_text(msg, encoding="utf-8")
        payload = {
            "round": next_round,
            "passed": False,
            "sent": False,
            "message": str(message_path),
            "workspace_changed": workspace_changed,
            "results": results,
        }
        print(json.dumps(payload, ensure_ascii=False), flush=True)
        return 0

    # 10. terminal send 전송 (계약 6)
    orca_cmd = args.orca if args.orca else state_data.get("orca") or orca_watch.get_default_orca()
    send_args = ["terminal", "send", "--terminal", handle, "--text", msg, "--enter", "--json"]
    res_send = orca_watch.run_orca(orca_cmd, send_args)

    if res_send.returncode != 0:
        err = (res_send.stderr or res_send.stdout).strip()
        print(f"오류: orca terminal send 실패 (코드 {res_send.returncode}): {err}", file=sys.stderr)
        return 1

    try:
        send_data = json.loads(res_send.stdout)
    except Exception as e:
        print(f"오류: orca terminal send JSON 디코드 실패: {e}\n{res_send.stdout}", file=sys.stderr)
        return 1

    if not isinstance(send_data, dict) or send_data.get("ok") is not True:
        print(f"오류: orca terminal send 결과 ok 아님: {send_data}", file=sys.stderr)
        return 1

    # 11. 새 감시 띄우기 (계약 7)
    def filter_watch_args(orig_watch: list[str]) -> list[str]:
        filtered: list[str] = []
        skip = False
        for arg in orig_watch:
            if skip:
                skip = False
                continue
            if arg in ("--log", "--round"):
                skip = True
                continue
            filtered.append(arg)
        return filtered

    new_log_path = reports_dir / f"{wo}-{term_prefix}.r{next_round}.log"
    new_final_path = reports_dir / f"{wo}-{term_prefix}.r{next_round}.final.md"

    new_watch = filter_watch_args(watch)
    new_watch.extend([
        "--log", str(new_log_path),
        "--round", str(next_round),
    ])

    no_window = bool(state_data.get("no_window", False))
    _launch_watch(new_watch, no_window)

    # 상태 파일 갱신은 감시를 띄운 뒤
    state_data["round"] = next_round
    state_data[f"watch_r{next_round}"] = new_watch
    state_data[f"triage_r{next_round}"] = args.triage
    state_file.write_text(json.dumps(state_data, ensure_ascii=False), encoding="utf-8")

    payload = {
        "round": next_round,
        "passed": False,
        "sent": True,
        "final": str(new_final_path),
        "workspace_changed": workspace_changed,
        "results": results,
    }
    print(json.dumps(payload, ensure_ascii=False), flush=True)
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
        default=None,
        help="상설 원본 경로 (기본: <src>/.jobs/<src 이름>-dev-origin)",
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
    p_rel.add_argument("--src", default=str(ROOT), help="소스 저장소 경로 (기본: 하네스 루트)")
    p_rel.add_argument(
        "--origin",
        default=None,
        help="상설 원본 경로 (기본: <src>/.jobs/<src 이름>-dev-origin)",
    )
    p_rel.add_argument(
        "--discard",
        action="store_true",
        default=False,
        help="커밋되지 않은 변경 사항 무시하고 강제 삭제 (--force)",
    )
    p_rel.add_argument("--orca", default=None, help="orca 실행 파일 경로")

    # 6. feedback
    p_fb = subparsers.add_parser("feedback", help="워커 판정 전 피드백 및 새 회차 감시 구동")
    p_fb.add_argument("--state", required=True, help="상태 파일 경로")
    p_fb.add_argument(
        "--run",
        action="append",
        default=None,
        dest="run",
        help="작업 공간 내 실행할 검사 스크립트 (여러 번 지정 가능)",
    )
    p_fb.add_argument(
        "--max-rounds",
        type=int,
        default=2,
        help="최대 회차 수 (1~3, 기본: 2)",
    )
    p_fb.add_argument(
        "--timeout-seconds",
        type=int,
        default=300,
        help="명령별 실행 상한 초 (기본: 300)",
    )
    p_fb.add_argument("--orca", default=None, help="orca 실행 파일 경로")
    p_fb.add_argument("--send", action="store_true",
                      help="실패를 워커에게 전송 (리드가 실패를 분류한 뒤에만, --triage 필요)")
    p_fb.add_argument("--triage", default=None,
                      help="리드 분류 근거 — 상태 파일에만 남고 워커에게는 보내지 않는다")

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
    if args.subcommand == "feedback":
        return cmd_feedback(args)

    return 2


if __name__ == "__main__":
    sys.exit(main())
