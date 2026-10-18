"""Python 소스 → 정규화 import 그래프 (K8). 사실만 추출하고 판정은 하지 않는다(판정은 Core harness/arch.py).

파일마다:
- module: 점으로 이은 모듈 이름 (패키지 __init__ 은 패키지 이름)
- imports: import 한 모듈의 절대 이름 (상대 import 는 절대 이름으로 바꿈)
- uses: 별칭을 풀어 쓴 이름 · 속성 사슬 (예: `from datetime import datetime as X; X.now` → `datetime.datetime.now`)
- const_member: 상수 이름으로 멤버에 접근 (Python: getattr · __import__ 의 상수 문자열 인자)
- dynamic: 정적으로 이름을 알 수 없는 접근 (상수가 아닌 getattr · __import__, eval · exec · compile, import_module)
"""

from __future__ import annotations

import ast
from pathlib import Path


def module_name(path: Path, src_root: Path) -> str:
    parts = list(path.relative_to(src_root).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _imports(tree: ast.AST, path: Path, src_root: Path) -> list[str]:
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = module_name(path, src_root).split(".")
                base = base[: len(base) - node.level + (1 if path.name == "__init__.py" else 0)]
                found.append(".".join(base + ([node.module] if node.module else [])))
            else:
                found.append(node.module or "")
    return found


def _aliases(tree: ast.AST) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                aliases[a.asname or a.name.split(".")[0]] = a.name if a.asname else a.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for a in node.names:
                aliases[a.asname or a.name] = f"{node.module}.{a.name}"
    return aliases


def _dotted(node: ast.AST, aliases: dict[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value, aliases)
        return f"{base}.{node.attr}" if base else None
    return None


def file_facts(path: Path, src_root: Path) -> dict:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    aliases = _aliases(tree)
    uses: set[str] = set()
    const_member: set[str] = set()
    dynamic: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Name, ast.Attribute)):
            name = _dotted(node, aliases)
            if name:
                uses.add(f"{node.lineno}:{name}")
                # 최상위 builtins.<이름> 은 내장 이름도 함께 낸다 (한 마디 금지 항목 대응)
                parts = name.split(".")
                if len(parts) == 2 and parts[0] == "builtins" and parts[1]:
                    uses.add(f"{node.lineno}:{parts[1]}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            fn = node.func.id
            if fn in {"eval", "exec", "compile"}:
                dynamic.append(f"{node.lineno}:{fn}(...) 동적 실행")
            elif fn in {"getattr", "__import__"} and node.args:
                for a in node.args:
                    if isinstance(a, ast.Constant) and isinstance(a.value, str):
                        const_member.add(f"{node.lineno}:{a.value}")
                name_arg = node.args[1] if fn == "getattr" and len(node.args) > 1 else node.args[0]
                if not isinstance(name_arg, ast.Constant):
                    dynamic.append(f"{node.lineno}:{fn}(..., {ast.unparse(name_arg)}) 동적 이름")
        if isinstance(node, ast.Attribute) and node.attr == "import_module":
            dynamic.append(f"{node.lineno}:importlib.import_module 동적 import")
    return {
        "file": path.relative_to(src_root).as_posix(),
        "module": module_name(path, src_root),
        "imports": sorted(set(_imports(tree, path, src_root))),
        "uses": sorted(uses),
        "const_member": sorted(const_member),
        "dynamic": dynamic,
    }


def import_graph(src_root: Path) -> list[dict]:
    """src_root 아래 모든 .py 파일의 사실."""
    return [file_facts(p, src_root) for p in sorted(src_root.rglob("*.py")) if "__pycache__" not in p.parts]
