"""하네스 자기 검사: 환경 준비 스크립트 · 첫 커밋 옵션 (WO-H39).

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
첫 커밋: harness/new_project.py --commit 이면 생성(역할 프리셋 적용 포함) 끝에 모든 파일을 첫 커밋(작업 트리 깨끗).
  --commit 이 없으면 기존처럼 커밋하지 않는다.

사용: python harness/selftest/check_setup_env_cases.py
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable
TOOL = ROOT / "adapters" / "python" / "setup_env.py"
LINE = re.compile(r"^(venv|pip|wsl-venv|wsl-pip|speckit-init|speckit-preset|doctor) (실행|건너뜀|재생성) :: (.*)$")
sys.path.insert(0, str(Path(__file__).resolve().parent))


def plan(proj: Path, *args: str) -> tuple[int, list[tuple[str, str, str]], str]:
    r = subprocess.run([PY, str(TOOL), "--dry-run", *args], cwd=proj, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    steps = [m.groups() for m in (LINE.match(l.strip()) for l in r.stdout.splitlines()) if m]
    return r.returncode, steps, r.stdout + r.stderr


def project(base: Path, name: str) -> Path:
    p = base / name
    p.mkdir()
    (p / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "0"\n', encoding="utf-8")
    return p


def touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")


def main() -> int:
    rows: list[tuple[str, bool, str]] = []

    def case(name: str, good: bool, detail: str = "") -> None:
        rows.append((name, bool(good), detail.replace("\n", " | ")[:200]))

    base = Path(tempfile.mkdtemp(prefix="setup-env-"))
    has = TOOL.exists()
    win = os.name == "nt"
    try:
        p = project(base, "e01")
        code, steps, out = plan(p) if has else (None, [], "도구 없음")
        keys = [k for k, _, _ in steps]
        case("E01 새 프로젝트 계획: venv → pip → wsl-venv → wsl-pip → doctor 모두 실행, 종료 0, 아무것도 안 만듦",
             has and code == 0 and keys == ["venv", "pip", "wsl-venv", "wsl-pip", "doctor"]
             and all(s == "실행" for _, s, _ in steps) and sorted(x.name for x in p.iterdir()) == ["pyproject.toml"], out)
        cmd = {k: c for k, _, c in steps}
        case("E02 pip · wsl-pip: 편집 설치 .[test], 출력 숨김(-q) 없음",
             has and "-m pip install -e" in cmd.get("pip", "") and ".[test]" in cmd.get("pip", "")
             and ".[test]" in cmd.get("wsl-pip", "") and "--upgrade pip" in cmd.get("wsl-pip", "")
             and " -q" not in cmd.get("pip", "") + cmd.get("wsl-pip", ""), out)
        drive = str(p.resolve())[0].lower()
        case("E03 Python 3.12 · WSL 경로 변환(/mnt/<드라이브>/...)",
             has and "3.12" in cmd.get("venv", "") and "python3.12 -m venv .venv-wsl" in cmd.get("wsl-venv", "")
             and (not win or (f"/mnt/{drive}/" in cmd.get("wsl-venv", "") and cmd.get("wsl-venv", "").startswith("wsl "))), out)
        # WO-H39 판정 뒤 추가 (리드 diff 읽기): 공백이 든 경로(예: "My Project")에서 cd 가 깨지지 않는다
        p = project(base, "e03 space")
        _, steps, out = plan(p) if has else (None, [], "")
        cmd = {k: c for k, _, c in steps}
        posix = p.resolve().as_posix()
        mnt = f"/mnt/{posix[0].lower()}{posix[2:]}"
        case("E03b 공백 경로: WSL cd 경로를 따옴표로 감싼다 (Windows)",
             has and (not win or f"cd '{mnt}'" in cmd.get("wsl-venv", "")), out)

        p = project(base, "e04")
        touch(p / ".venv" / ("Scripts/python.exe" if win else "bin/python"))
        touch(p / ".venv" / "pyvenv.cfg")
        code, steps, out = plan(p) if has else (None, [], "")
        st = {k: s for k, s, _ in steps}
        case("E04 정상 .venv → venv 건너뜀, pip 는 실행", has and st.get("venv") == "건너뜀" and st.get("pip") == "실행", out)

        p = project(base, "e05a")
        touch(p / ".venv-wsl" / "bin" / "mutmut")
        (p / ".venv-wsl" / "lib" / "python3.12" / "site-packages" / "~ip").mkdir(parents=True)
        _, sa, outa = plan(p) if has else (None, [], "")
        p = project(base, "e05b")
        touch(p / ".venv-wsl" / "pyvenv.cfg")
        _, sb, outb = plan(p) if has else (None, [], "")
        da, db = {k: (s, c) for k, s, c in sa}, {k: (s, c) for k, s, c in sb}
        case("E05 손상(~ 항목) · mutmut 없음 → wsl-venv 재생성(rm -rf .venv-wsl 포함), wsl-pip 실행",
             has and da.get("wsl-venv", ("", ""))[0] == "재생성" and "rm -rf .venv-wsl" in da.get("wsl-venv", ("", ""))[1]
             and db.get("wsl-venv", ("", ""))[0] == "재생성" and da.get("wsl-pip", ("",))[0] == "실행", outa + outb)

        p = project(base, "e06")
        touch(p / ".venv-wsl" / "bin" / "mutmut")
        (p / ".venv-wsl" / "lib" / "python3.12" / "site-packages" / "pip").mkdir(parents=True)
        _, steps, out = plan(p) if has else (None, [], "")
        st = {k: s for k, s, _ in steps}
        case("E06 정상 .venv-wsl → wsl-venv · wsl-pip 건너뜀", has and st.get("wsl-venv") == "건너뜀"
             and st.get("wsl-pip") == "건너뜀", out)

        p = project(base, "e07")
        code, steps, out = plan(p, "--speckit", "claude") if has else (None, [], "")
        keys = [k for k, _, _ in steps]
        cmd = {k: c for k, _, c in steps}
        bad_code, _, _ = plan(p, "--speckit", "a;b") if has else (None, [], "")
        case("E07 --speckit claude → specify init · 하네스 preset 이 doctor 앞, 이상한 연동 이름은 종료 2",
             has and code == 0 and keys[-3:] == ["speckit-init", "speckit-preset", "doctor"]
             and "specify init --here --force --non-interactive --integration claude --ignore-agent-tools" in cmd.get("speckit-init", "")
             and "specify preset add --dev templates/sdd/preset --priority 1" in cmd.get("speckit-preset", "")
             and bad_code == 2, out)

        # 첫 커밋 옵션
        from check_project_cases import make_upstream, new_project
        up = make_upstream(base)
        # 사용자 git 설정이 없는 환경(CI 등)에서도 같은 판정이 나오게 작성자를 환경 변수로 준다 (하위 프로세스가 물려받는다)
        for k, v in (("GIT_AUTHOR_NAME", "t"), ("GIT_AUTHOR_EMAIL", "t@example.invalid"),
                     ("GIT_COMMITTER_NAME", "t"), ("GIT_COMMITTER_EMAIL", "t@example.invalid")):
            os.environ.setdefault(k, v)
        env_ok = True
        r1 = new_project(up, base / "c1", "--package", "demo_app", "--commit")
        r2 = new_project(up, base / "c2", "--package", "demo_app")

        def git(repo: Path, *a: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(["git", *a], cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace")
        n1 = git(base / "c1", "rev-list", "--count", "HEAD").stdout.strip() if r1.returncode == 0 else "?"
        s1 = git(base / "c1", "status", "--porcelain").stdout.strip() if r1.returncode == 0 else "?"
        h2 = git(base / "c2", "rev-parse", "--verify", "-q", "HEAD").returncode if r2.returncode == 0 else -1
        case("E08 new_project --commit → 커밋 1개 · 작업 트리 깨끗, 옵션 없으면 커밋 없음",
             env_ok and r1.returncode == 0 and n1 == "1" and s1 == "" and r2.returncode == 0 and h2 != 0,
             f"r1={r1.returncode} n={n1} status={s1[:80]} r2={r2.returncode} h2={h2} {(r1.stdout + r1.stderr)[-120:]}")
        case("E09 준비 스크립트는 Core 밖 (harness/ 에 setup_env 가 없다)",
             has and not list((ROOT / "harness").glob("setup_env*.py")))
    finally:
        shutil.rmtree(base, ignore_errors=True)

    all_good = True
    for name, good, detail in rows:
        all_good &= good
        print(f"{'OK ' if good else 'BAD'} {name}  — {detail}")
    print("결과: 모든 사례가 기대와 일치" if all_good else "결과: 기대와 다른 사례가 있다")
    return 0 if all_good else 1


if __name__ == "__main__":
    sys.exit(main())
