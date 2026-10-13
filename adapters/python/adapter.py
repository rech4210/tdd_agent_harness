"""Python 어댑터: Core 가 모르는 Python 도구 세부를 모두 여기에 둔다.

Core(harness/) 는 이 모듈의 함수만 부른다. 다른 언어 어댑터는 같은 이름의 함수를 제공하면 된다.
도구: pytest(G2) · coverage.py JSON(G3) · mutmut(G6, Windows 에서는 WSL) · 우회 패턴(G0) · raise-message 등가 분류기(예외 메시지는 계약이 아니다)

계약 (Core ↔ Adapter)
- resolve_command(cmd) -> list[str]
- stale_artifacts() -> list[str]                      실행 전 지울 도구 산출물 (저장소 루트 기준)
- read_coverage(path) -> {file: {branches_total, branches_covered, lines_total, lines_covered[, functions_total, functions_covered]}}
- mutation_config_mismatch(expected) -> str          빈 문자열이면 일치
- run_mutation(gate, run) -> {"counts": {...}} | {"error": str}
- survivors(run_full) -> list[str]                    생존 변이 ID
- survivor_diff(mid, run_full) -> (removed_lines, added_lines)
- equivalent(rule, removed, added) -> bool
- import_graph(src_root) -> [{file, module, imports, uses, const_member, dynamic}]   (K8, imports.py)
- GAMING_PATTERNS: list[(regex, reason)]
- wrap_mutation(cmd, env) -> list[str]
- scaffold(root, package) -> list[str]                 (선택) init 이 부른다. 만든 파일 목록. 있는 파일은 건드리지 않는다
- doctor() -> [(name, ok, detail)]                     (선택) 도구 설치 점검
"""

from __future__ import annotations

import ast
import json
import os
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = ROOT / "pyproject.toml"

SOURCE_SUFFIXES = (".py",)  # 우회 패턴을 적용할 파일 (C1)
LOCK_EXCLUDE_PARTS = ("__pycache__", ".hypothesis", ".pytest_cache")  # 잠금에서 뺄 도구 산출물 (C2)

GAMING_PATTERNS: list[tuple[str, str]] = [
    (r"pytest\.mark\.(skip|skipif|xfail)\b", "테스트 건너뛰기"),
    (r"pytest\.skip\(", "테스트 건너뛰기"),
    (r"pragma:\s*no\s*cover", "커버리지 제외"),
    (r"pragma:\s*no\s*mutate", "돌연변이 제외"),
    (r"#\s*type:\s*ignore", "타입 검사 무시"),
    (r"#\s*noqa", "린트 무시"),
    (r"PYTEST_CURRENT_TEST", "테스트 실행 중인지 감지 (테스트 때만 다르게 동작)"),
    (r"['\"]pytest['\"]\s+in\s+sys\.modules", "테스트 실행 중인지 감지"),
    (r"\bexcept\s*(Exception|BaseException)?\s*:\s*(pass|\.\.\.)\s*$", "예외 삼키기"),
    (r"max_examples\s*=\s*[0-9]\b", "성질 테스트 예시 수를 한 자리로 축소"),
]


def resolve_command(cmd: list[str]) -> list[str]:
    """명령을 실행기 자신의 인터프리터 · 가상환경 기준으로 푼다 (Windows 에서 시스템 Python 을 잡는 문제)."""
    if cmd[0] == "python":
        return [sys.executable, *cmd[1:]]
    scripts = Path(sys.executable).parent
    for name in (cmd[0], cmd[0] + ".exe"):
        if (scripts / name).is_file():
            return [str(scripts / name), *cmd[1:]]
    return cmd


def import_graph(src_root: Path) -> list[dict]:
    from adapters.python.imports import import_graph as _graph

    return _graph(src_root)


def stale_artifacts() -> list[str]:
    return [".coverage", "mutants"]


def read_coverage(path: Path) -> dict[str, dict[str, int]]:
    """coverage.py JSON → 정규화 커버리지."""
    files = json.loads(path.read_text(encoding="utf-8"))["files"]
    out: dict[str, dict[str, int]] = {}
    for name, d in files.items():
        entry = {
            "branches_total": d["summary"].get("num_branches", 0),
            "branches_covered": d["summary"].get("covered_branches", 0),
            "lines_total": d["summary"].get("num_statements", 0),
            "lines_covered": d["summary"].get("covered_lines", 0),
        }
        if "functions" in d and d["functions"] is not None:
            non_empty = [fn for fn_name, fn in d["functions"].items() if fn_name != ""]
            valid = [
                fn for fn in non_empty
                if fn.get("summary", {}).get("num_statements", 0) > 0
            ]
            entry["functions_total"] = len(valid)
            entry["functions_covered"] = sum(
                1 for fn in valid if fn.get("summary", {}).get("covered_lines", 0) > 0
            )
            excluded_names = [
                fn_name for fn_name, fn in d["functions"].items()
                if fn_name != "" and (
                    bool(fn.get("excluded_lines"))
                    or fn.get("summary", {}).get("excluded_lines", 0) > 0
                )
            ]
            if excluded_names:
                entry["functions_excluded"] = excluded_names
        out[name.replace("\\", "/")] = entry
    return out


def wrap_mutation(cmd: list[str], env: dict[str, str]) -> list[str]:
    """mutmut 은 Windows 를 지원하지 않는다. Windows 에서는 같은 작업 트리를 WSL 의 .venv-wsl 로 돌린다.
    WSL 은 Windows 환경변수를 넘겨받지 않으므로 env 를 명령 앞에 붙인다."""
    if os.name != "nt":
        return cmd
    drive, rest = str(ROOT)[0].lower(), str(ROOT)[2:].replace("\\", "/")
    line = " ".join(cmd)
    pairs = " ".join(f"{k}='{v}'" for k, v in env.items())
    prefix = f"env {pairs} " if pairs else ""
    return ["wsl", "-e", "bash", "-lc", f"cd '/mnt/{drive}{rest}' && {prefix}.venv-wsl/bin/{line}"]


def mutation_config_mismatch(expected: dict) -> str:
    """슬라이스가 요구하는 mutmut 설정과 pyproject 가 다르면 이유를, 같으면 빈 문자열."""
    actual = tomllib.loads(PYPROJECT.read_text(encoding="utf-8")).get("tool", {}).get("mutmut", {})
    diffs = [f"{k}: pyproject={actual.get(k)!r}, 슬라이스={v!r}" for k, v in expected.items() if actual.get(k) != v]
    if not diffs:
        return ""
    return "pyproject.toml 의 [tool.mutmut] 이 슬라이스와 다르다. 사람이 맞춘 뒤 다시 실행한다:" + chr(10) + chr(10).join(diffs)


def run_mutation(gate: dict, run, env: dict[str, str]) -> dict:
    """mutmut 실행 → 정규화 통계. run(cmd) -> (code, tail)."""
    _, out = run(wrap_mutation(gate["run"], env))
    # mutmut 은 살아남은 변이가 있으면 0이 아닌 코드로 끝날 수 있으므로 통계 파일로 판정한다
    _, out2 = run(wrap_mutation(gate["stats"], env))
    stats_path = ROOT / gate["stats_file"]
    if not stats_path.exists():
        return {"error": f"돌연변이 통계 파일 없음: {gate['stats_file']}\n{out}\n{out2}"}
    s = json.loads(stats_path.read_text(encoding="utf-8"))
    return {"counts": {k: s.get(k, 0) for k in ("total", "killed", "timeout", "survived", "no_tests")}}


def survivors(run_full, env: dict[str, str]) -> list[str]:
    out = []
    for line in run_full(wrap_mutation(["mutmut", "results"], env)).splitlines():
        if line.strip().endswith(": survived"):
            out.append(line.strip().rsplit(":", 1)[0])
    return out


def survivor_diff(mid: str, run_full, env: dict[str, str]) -> tuple[list[str], list[str]]:
    diff = run_full(wrap_mutation(["mutmut", "show", f"'{mid}'"], env)).splitlines()
    removed = [d[1:] for d in diff if d.startswith("-") and not d.startswith("---")]
    added = [d[1:] for d in diff if d.startswith("+") and not d.startswith("+++")]
    return removed, added


RAISE_CALL = re.compile(r"^\s*raise\s+([\w.]+)\((.*)\)\s*$")


def _message_only(removed: str, added: str) -> bool:
    """두 줄이 같은 예외를 raise 하고, 인자만 문자열 리터럴 · None 사이에서 바뀌었는가."""
    a, b = RAISE_CALL.match(removed), RAISE_CALL.match(added)
    if not (a and b) or a.group(1) != b.group(1):
        return False
    try:
        va, vb = ast.literal_eval(a.group(2) or "None"), ast.literal_eval(b.group(2) or "None")
    except (ValueError, SyntaxError):
        return False
    return all(v is None or isinstance(v, str) for v in (va, vb))


def equivalent(rule: str, removed: list[str], added: list[str]) -> bool:
    """어댑터가 아는 등가 규칙. 모르는 규칙이면 거짓(제외하지 않음)."""
    if rule == "raise-message":
        return len(removed) == 1 and len(added) == 1 and _message_only(removed[0], added[0])
    return False


def doctor() -> list[tuple[str, bool, str]]:
    """도구 설치 점검 (harness/doctor.py 가 부른다). 판정이 아니라 환경 확인이다."""
    import importlib.util
    import subprocess

    out: list[tuple[str, bool, str]] = []
    for mod in ("pytest", "hypothesis", "pytest_cov", "pytest_timeout"):
        out.append((f"Python 모듈 {mod}", importlib.util.find_spec(mod) is not None, ""))
    scripts = Path(sys.executable).parent
    for tool in ("mypy", "xenon", "bandit"):
        found = any((scripts / n).is_file() for n in (tool, tool + ".exe"))
        out.append((f"도구 {tool}", found, str(scripts)))
    if os.name == "nt":
        venv = ROOT / ".venv-wsl" / "bin" / "mutmut"
        out.append(("WSL 변이 도구 (.venv-wsl/bin/mutmut)", venv.exists(), "없으면 WSL 에서 bash adapters/python/setup_wsl.sh 실행"))
    else:
        out.append(("변이 도구 mutmut", subprocess.run(["mutmut", "--version"], capture_output=True).returncode == 0, ""))
    return out


TEMPLATE = Path(__file__).resolve().parent / "project_template"
GITIGNORE = [
    "__pycache__/", "*.pyc", ".pytest_cache/", ".hypothesis/", ".coverage", "mutants/", ".venv/", ".venv-wsl/",
    "*.egg-info/", "reports/coverage.json", "reports/junit*.xml", "reports/g0_report.json",
]


def scaffold(root: Path, package: str) -> list[str]:
    """새 프로젝트 골격 (harness/init.py 가 부른다). 있는 파일은 건드리지 않는다."""
    if not package.isidentifier():
        raise ValueError(f"Python 패키지 이름이 아니다: {package}")
    files = {
        "pyproject.toml": (TEMPLATE / "pyproject.toml").read_text(encoding="utf-8").replace("<package>", package),
        f"src/{package}/__init__.py": "",
        f"src/{package}/domain/__init__.py": "",
        "tests/__init__.py": "",
        "tests/unit/__init__.py": "",
    }
    made = []
    for rel, text in files.items():
        path = root / rel
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
            made.append(rel)
    ignore = root / ".gitignore"
    have = ignore.read_text(encoding="utf-8").splitlines() if ignore.exists() else []
    add = [line for line in GITIGNORE if line not in have]
    if add:
        ignore.write_text("\n".join(have + add) + "\n", encoding="utf-8", newline="\n")
        made.append(".gitignore")
    return made
