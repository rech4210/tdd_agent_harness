"""환경 준비 스크립트 (Python 어댑터 전용, WO-H39).

계약
준비 스크립트 adapters/python/setup_env.py (Python 어댑터 전용, Core 밖). 프로젝트 루트(작업 폴더)에서 실행한다.
  사용: python adapters/python/setup_env.py [--dry-run] [--speckit <연동 이름>]
  단계(이 순서), 키 · 하는 일:
    venv          .venv 를 Python 3.12 로 만든다 (Windows: py -3.12 -m venv .venv, 그 밖: python3.12 -m venv .venv).
                  .venv 의 파이썬(Windows .venv/Scripts/python.exe, 그 밖 .venv/bin/python)과 .venv/pyvenv.cfg 가 있으면 건너뜀.
    pip           <.venv 파이썬> -m pip install -e ".[test]" — 출력을 숨기지 않는다(-q 없음). 항상 실행.
    wsl-venv      WSL 변이 도구 환경 .venv-wsl. 정상(.venv-wsl/bin/mutmut 있음, site-packages 에 “~” 로 시작하는 항목 없음)이면
                  건너뜀. 없으면 실행(python3.12 -m venv .venv-wsl). 폴더는 있는데 비정상이면 재생성(rm -rf .venv-wsl 뒤 만듦).
    wsl-pip       .venv-wsl/bin/python -m pip install --upgrade pip 와 -e ".[test]" (-q 없음). wsl-venv 가 건너뜀이면 건너뜀.
                  Windows 에서 WSL 단계는 wsl -e bash -lc "cd /mnt/<드라이브 소문자>/<나머지 경로> && ..." 로 감싼다.
    speckit-init  --speckit <연동> 일 때만: specify init --here --force --non-interactive --integration <연동> --ignore-agent-tools
    speckit-preset --speckit 일 때만: specify preset add --dev templates/sdd/preset --priority 1
    doctor        <.venv 파이썬> harness/doctor.py
  출력: 단계마다 한 줄 “<키> <실행|건너뜀|재생성> :: <명령>”. --dry-run 은 계획만 출력하고 파일 · 폴더를 만들지 않는다(종료 0).
  실제 실행은 순서대로, 첫 실패에서 멈추고 종료 1. --speckit 값은 [a-z0-9-]+ 만(아니면 종료 2).
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path


def wrap_wsl(cmd_str: str, cwd: Path) -> str:
    if os.name == "nt":
        as_posix = cwd.resolve().as_posix()
        drive = as_posix[0].lower()
        rest = as_posix[2:]
        if not rest.startswith("/"):
            rest = "/" + rest
        # 공백이 든 경로(예: "My Project")가 깨지지 않게 bash 안에서는 작은따옴표로 감싼다 (리드 판정 보정)
        return f"wsl -e bash -lc \"cd '/mnt/{drive}{rest}' && {cmd_str}\""
    return cmd_str


def build_plan(proj: Path, speckit: str | None) -> list[tuple[str, str, str]]:
    is_win = os.name == "nt"
    steps: list[tuple[str, str, str]] = []

    # 1. venv
    venv_py = ".venv/Scripts/python.exe" if is_win else ".venv/bin/python"
    venv_cmd = "py -3.12 -m venv .venv" if is_win else "python3.12 -m venv .venv"
    if (proj / venv_py).exists() and (proj / ".venv" / "pyvenv.cfg").exists():
        steps.append(("venv", "건너뜀", venv_cmd))
    else:
        steps.append(("venv", "실행", venv_cmd))

    # 2. pip
    pip_cmd = f'{venv_py} -m pip install -e ".[test]"'
    steps.append(("pip", "실행", pip_cmd))

    # 3. wsl-venv
    wsl_dir = proj / ".venv-wsl"
    if not wsl_dir.exists():
        wsl_venv_status = "실행"
        wsl_venv_cmd = wrap_wsl("python3.12 -m venv .venv-wsl", proj)
    else:
        has_mutmut = (wsl_dir / "bin" / "mutmut").exists()
        has_tilde = False
        for sp in wsl_dir.glob("**/site-packages"):
            if sp.is_dir():
                for item in sp.iterdir():
                    if item.name.startswith("~"):
                        has_tilde = True
                        break
            if has_tilde:
                break
        if has_mutmut and not has_tilde:
            wsl_venv_status = "건너뜀"
            wsl_venv_cmd = wrap_wsl("python3.12 -m venv .venv-wsl", proj)
        else:
            wsl_venv_status = "재생성"
            wsl_venv_cmd = wrap_wsl("rm -rf .venv-wsl && python3.12 -m venv .venv-wsl", proj)
    steps.append(("wsl-venv", wsl_venv_status, wsl_venv_cmd))

    # 4. wsl-pip
    if wsl_venv_status == "건너뜀":
        wsl_pip_status = "건너뜀"
    else:
        wsl_pip_status = "실행"
    wsl_pip_inner = ".venv-wsl/bin/python -m pip install --upgrade pip && .venv-wsl/bin/python -m pip install -e '.[test]'"
    wsl_pip_cmd = wrap_wsl(wsl_pip_inner, proj)
    steps.append(("wsl-pip", wsl_pip_status, wsl_pip_cmd))

    # 5. speckit-init & speckit-preset (선택)
    if speckit is not None:
        steps.append((
            "speckit-init",
            "실행",
            f"specify init --here --force --non-interactive --integration {speckit} --ignore-agent-tools"
        ))
        steps.append((
            "speckit-preset",
            "실행",
            "specify preset add --dev templates/sdd/preset --priority 1"
        ))

    # 6. doctor
    doctor_cmd = f"{venv_py} harness/doctor.py"
    steps.append(("doctor", "실행", doctor_cmd))

    return steps


def run_command(cmd: str, cwd: Path) -> int:
    exec_cmd = cmd
    if os.name == "nt":
        if exec_cmd.startswith(".venv/Scripts/python.exe"):
            exec_cmd = exec_cmd.replace(".venv/Scripts/python.exe", r".venv\Scripts\python.exe", 1)
    res = subprocess.run(exec_cmd, shell=True, cwd=cwd)
    return res.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="환경 준비 스크립트 (adapters/python/setup_env.py)")
    parser.add_argument("--dry-run", action="store_true", help="계획만 출력하고 아무것도 만들지 않는다")
    parser.add_argument("--speckit", help="Specify 연동 이름 ([a-z0-9-]+)")
    args = parser.parse_args()

    if args.speckit is not None:
        if not re.fullmatch(r"[a-z0-9-]+", args.speckit):
            print(f"오류: speckit 연동 이름은 [a-z0-9-]+ 형식이어야 합니다: '{args.speckit}'", file=sys.stderr)
            return 2

    proj = Path.cwd().resolve()
    steps = build_plan(proj, args.speckit)

    if args.dry_run:
        for key, status, cmd in steps:
            print(f"{key} {status} :: {cmd}")
        return 0

    for key, status, cmd in steps:
        print(f"{key} {status} :: {cmd}", flush=True)
        if status in ("실행", "재생성"):
            code = run_command(cmd, proj)
            if code != 0:
                print(f"오류: 단계 '{key}' 실행 실패 (종료 코드 {code})", file=sys.stderr)
                return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
