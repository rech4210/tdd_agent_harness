"""구조 규칙: 레이어 import, 내부 레이어의 시각 · 난수 · 환경 직접 접근 금지, 테스트 도구 import 금지.

guards:      <D-xx> (레이어 규칙), <D-yy> (내부 레이어는 시각 · 난수 · 환경을 포트 · 인자로만 받는다)
technique:   구조 적합성 (AST 정적 검사), 09 공허한 참 방지, 검사기 자기 검사 (알려진 우회 · 깨끗한 코드)
checklist:   B-1, B-7, B-9
decisions:   <D-xx>, <D-yy>
not-covered: 문자열을 계산해 만드는 동적 import 의 대상, 복잡한 데이터 흐름을 거친 별칭. 내부 레이어에서는
             동적 import 수단(__import__, importlib, eval, exec) 자체를 금지해 이 빈틈을 줄인다

이 골격은 그대로 실행된다. 위쪽 설정(PACKAGE · LAYERS · INNER · TOP_LEVEL_REQUIRED)만 프로젝트 값으로 바꾼다.
바꾸지 않으면 "검사할 파일이 없다"로 실패한다 (공허하게 통과하지 않는다).
src 가 없으면 skip 이 아니라 실패한다 (Red 상태를 정직하게 보여 준다).
Core 추출 후에는 설정이 harness.toml [layers] 로, import 추출이 어댑터로 옮겨진다.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# ── 프로젝트 설정 (여기만 고친다) ─────────────────────────────────────────
# [사람 결정 H4 · <D-xx>] 패키지 이름과 레이어 규칙
PACKAGE = "<package>"
SRC_ROOT = Path(__file__).resolve().parents[2] / "src"  # 이 파일이 tests/architecture/ 에 있을 때
SRC = SRC_ROOT / PACKAGE

# 내부 레이어가 import 할 수 있는 표준 라이브러리 ("@stdlib_pure")
STDLIB_PURE = {
    "__future__", "abc", "collections", "dataclasses", "datetime", "decimal",
    "enum", "functools", "itertools", "math", "typing",
}

# 레이어 규칙: PACKAGE 아래 상대 경로 → import 허용 목록. 가장 구체적인(긴) 경로의 규칙이 적용된다.
#   "@stdlib_pure" = 위 집합, "*" = 제한 없음, 그 밖 = 모듈 이름 접두사
LAYERS: dict[str, list[str]] = {
    "__init__.py": ["@stdlib_pure"],  # 루트 __init__ 도 규칙이 있어야 한다 (규칙 없는 파일 = 우회 경로)
    "<inner_layer>": ["@stdlib_pure", f"{PACKAGE}.<inner_layer>"],
    "<middle_layer>": ["@stdlib_pure", f"{PACKAGE}.<inner_layer>", f"{PACKAGE}.<middle_layer>"],
    "<outer_layer>": ["*"],
}
INNER = ["<inner_layer>", "<middle_layer>"]  # 시각 · 난수 · 환경 직접 접근 금지 대상
TOP_LEVEL_REQUIRED = ["<inner_layer>", "<middle_layer>", "<outer_layer>"]  # 모두 있어야 한다

# 내부 레이어에서 금지하는 접근 (점 경로). 이 경로이거나 이 경로 아래면 금지
FORBIDDEN = {
    "datetime.datetime.now", "datetime.datetime.utcnow", "datetime.datetime.today", "datetime.date.today",
    "time.time", "time.time_ns", "time.monotonic", "time.perf_counter", "time.localtime", "time.gmtime",
    "os.environ", "os.environb", "os.getenv", "os.getenvb",
    "random", "secrets", "uuid.uuid1", "uuid.uuid4",
    "__import__", "importlib", "eval", "exec",
}
TEST_FRAMEWORKS = {"pytest", "_pytest", "hypothesis", "unittest", "mock"}

# ── 검사기 ────────────────────────────────────────────────────────────────

DYNAMIC = "<dynamic>"
DYNAMIC_GUARDED = {p.rsplit(".", 1)[0] for p in FORBIDDEN if "." in p} | {p for p in FORBIDDEN if "." not in p}


def source_files(rel: str) -> list[Path]:
    """검사 대상. 0개면 실패한다: 검사할 것이 없으면 '위반 없음'은 공허한 참이다."""
    target = SRC / rel
    files = [target] if target.is_file() else sorted(target.rglob("*.py")) if target.is_dir() else []
    assert files, f"검사할 파일이 없다: {target}"
    return files


def parse(f: Path) -> ast.Module:
    return ast.parse(f.read_text(encoding="utf-8"), filename=str(f))


def dotted(node: ast.AST, aliases: dict[str, str]) -> str | None:
    """이름 · 속성 · getattr 를 점 경로로 바꾼다. 별칭을 따라간다. 모르면 None."""
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        base = dotted(node.value, aliases)
        return f"{base}.{node.attr}" if base else None
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) >= 2
    ):
        base = dotted(node.args[0], aliases)
        name = node.args[1]
        if base and isinstance(name, ast.Constant) and isinstance(name.value, str):
            return f"{base}.{name.value}"
        return f"{base}.{DYNAMIC}" if base else None
    return None


def module_aliases(tree: ast.Module) -> dict[str, str]:
    """이름 → 점 경로. import 별칭(as)과 단순 대입 별칭(x = datetime.datetime)을 따라간다."""
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.asname:
                    aliases[a.asname] = a.name
                else:
                    top = a.name.split(".")[0]
                    aliases[top] = top
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for a in node.names:
                aliases[a.asname or a.name] = f"{node.module}.{a.name}"
    for _ in range(5):  # 대입 별칭의 연쇄를 몇 단계까지 따라간다 (x = y; z = x)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                path = dotted(node.value, aliases)
                if path and path != node.targets[0].id:
                    aliases[node.targets[0].id] = path
    return aliases


def is_forbidden(path: str) -> bool:
    if path.endswith("." + DYNAMIC):
        return path[: -len(DYNAMIC) - 1] in DYNAMIC_GUARDED
    return any(path == f or path.startswith(f + ".") for f in FORBIDDEN)


def forbidden_uses(tree: ast.Module) -> list[str]:
    aliases = module_aliases(tree)
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Name, ast.Attribute, ast.Call)):
            path = dotted(node, aliases)
            if path and is_forbidden(path):
                found.add(f"{node.lineno}: {path}")
    return sorted(found)


def imported_modules(tree: ast.Module, file: Path) -> list[str]:
    """파일이 import 하는 모듈의 절대 이름. 상대 import 는 파일 위치로 풀어서."""
    package = list(file.relative_to(SRC_ROOT).with_suffix("").parts)[:-1]
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[: len(package) - node.level + 1]
                mod = ".".join(base + ([node.module] if node.module else []))
            else:
                mod = node.module or ""
            if mod == PACKAGE:  # from <package> import <layer> 는 하위 모듈 이름으로 본다
                out += [f"{mod}.{a.name}" for a in node.names]
            else:
                out.append(mod)
    return out


def allowed(module: str, rules: list[str]) -> bool:
    if "*" in rules:
        return True
    if "@stdlib_pure" in rules and module.split(".")[0] in STDLIB_PURE:
        return True
    return any(module == r or module.startswith(r + ".") for r in rules if not r.startswith("@"))


def layer_of(f: Path) -> str | None:
    rel = f.relative_to(SRC).as_posix()
    matches = [k for k in LAYERS if rel == k or rel.startswith(k.rstrip("/") + "/")]
    return max(matches, key=len) if matches else None


# ── 구조 규칙 ─────────────────────────────────────────────────────────────


def test_required_top_level_entries_exist():
    """<D-xx>: 정해진 레이어가 모두 있다. 소스가 없으면 실패한다 (skip 아님)."""
    assert TOP_LEVEL_REQUIRED, "필수 레이어 목록이 비어 있다"
    missing = [n for n in TOP_LEVEL_REQUIRED if not (SRC / n).exists() and not (SRC / f"{n}.py").exists()]
    assert missing == [], f"없는 레이어: {missing} (위치: {SRC})"


def test_every_source_file_has_a_layer_rule():
    """<D-xx>: 규칙이 없는 파일은 아무것이나 import 할 수 있다. 모든 파일이 어느 레이어에 속해야 한다."""
    orphans = [f.relative_to(SRC).as_posix() for f in source_files(".") if layer_of(f) is None]
    assert orphans == []


@pytest.mark.parametrize("layer", sorted(LAYERS))
def test_layer_imports(layer):
    """<D-xx>: 각 레이어는 허용된 것만 import 한다."""
    rules = LAYERS[layer]
    bad = [
        f"{f.relative_to(SRC).as_posix()}: import {m}"
        for f in source_files(layer)
        if layer_of(f) == layer  # 더 구체적인 규칙이 있는 파일은 그 규칙이 맡는다
        for m in imported_modules(parse(f), f)
        if not allowed(m, rules)
    ]
    assert bad == []


@pytest.mark.parametrize("layer", INNER)
def test_inner_layers_do_not_touch_clock_random_env(layer):
    """<D-yy>: 내부 레이어는 현재 시각 · 난수 · 환경 변수를 직접 읽지 않는다. 별칭 · getattr · 대입 별칭도 본다."""
    bad = [f"{f.relative_to(SRC).as_posix()}:{use}" for f in source_files(layer) for use in forbidden_uses(parse(f))]
    assert bad == []


def test_product_never_imports_test_frameworks():
    """제품 코드는 테스트 도구를 import 하지 않는다. 테스트 실행기를 바꿔치기하면 테스트가 돌지 않고도 통과한다."""
    bad = [
        f"{f.relative_to(SRC).as_posix()}: import {m}"
        for f in source_files(".")
        for m in imported_modules(parse(f), f)
        if m.split(".")[0] in TEST_FRAMEWORKS
    ]
    assert bad == []


# ── 검사기 자기 검사 (검사기의 공허한 참 방지, B-9) ─────────────────────────

KNOWN_BYPASSES = {
    "import-alias": "from datetime import datetime as X\nX.now()\n",
    "module-alias": "import datetime as d\nd.datetime.now()\n",
    "getattr-const": "from datetime import datetime\ngetattr(datetime, 'now')()\n",
    "getattr-dynamic": "from datetime import datetime\nname = 'n' + 'ow'\ngetattr(datetime, name)()\n",
    "assign-alias": "from datetime import datetime\nc = datetime\nc.now()\n",
    "bound-method": "from datetime import datetime\nf = datetime.now\n",
    "time-func": "from time import time\ntime()\n",
    "environ-alias": "from os import environ as E\nE.get('X')\n",
    "environ-subscript": "import os\nos.environ['X']\n",
    "random-module": "import random as r\nr.random()\n",
    "dynamic-import": "__import__('os')\n",
}


@pytest.mark.parametrize("name", sorted(KNOWN_BYPASSES))
def test_checker_catches_known_bypass(name):
    assert forbidden_uses(ast.parse(KNOWN_BYPASSES[name])), f"검사기가 이 우회를 못 잡는다: {name}"


def test_checker_accepts_clean_code():
    """양성 대조: 시각을 인자로 받는 깨끗한 코드는 통과한다 (과잉 탐지 방지)."""
    clean = "from datetime import datetime, timedelta\ndef f(now: datetime) -> datetime:\n    return now + timedelta(1)\n"
    assert forbidden_uses(ast.parse(clean)) == []
