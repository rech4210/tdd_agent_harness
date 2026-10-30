"""Export 대상 selftest의 제외 경로 참조 사전 검사 (WO-H96).

정적 휴리스틱: 문자열 경로와 / 조합, 제외된 check 모듈 이름을 본다.
fixture 데이터와 실제 접근을 완전히 구분하지 못하며 실행 검증을 대신하지 않는다.
"""
from __future__ import annotations

import argparse
import ast
import fnmatch
from pathlib import Path
import re
import tomllib


def included(path: str, cfg: dict) -> bool:
    return (not path.startswith("scripts/") and path != "harness/selftest/check_export_cases.py"
            and path != "export.toml" and not path.endswith("/export.toml")
            and any(fnmatch.fnmatch(path, p) for p in cfg.get("include", [])))


def _path(node: ast.AST) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Div, ast.Add)):
        left, right = _path(node.left), _path(node.right)
        return (left + "/" + right).strip("/") if right else left
    return ""


def _guard(node: ast.AST) -> bool:
    calls = [n for n in ast.walk(node) if isinstance(n, ast.Call)]
    exists = any(isinstance(n.func, ast.Attribute) and n.func.attr in {"exists", "is_file", "is_dir"}
                 for n in calls)
    skip = any(isinstance(n.func, ast.Attribute) and n.func.attr == "skipTest" for n in calls)
    skip |= any(isinstance(n.func, ast.Name) and n.func.id == "print"
                and any(isinstance(a, ast.Constant) and isinstance(a.value, str)
                        and ("SKIP" in a.value or "건너뜁니다" in a.value)
                        for a in n.args) for n in calls)
    return exists and skip


def inspect_source(source: str, cfg: dict) -> list[str]:
    tree = ast.parse(source)
    parents = {c: p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}
    funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    guarded = {name for name, n in funcs.items() if _guard(n)}
    # presence + SKIP guard helpers (e.g. _need_export_cases).
    for _ in range(len(funcs)):
        for name, node in funcs.items():
            if any(isinstance(n, ast.Call) and
                   ((isinstance(n.func, ast.Attribute) and n.func.attr in guarded) or
                    (isinstance(n.func, ast.Name) and n.func.id in guarded)) for n in ast.walk(node)):
                guarded.add(name)
    module_guard = any(isinstance(n, ast.If) and _guard(n) for n in tree.body)
    module_guard |= "main" in guarded
    roots = {p.split("/")[0] for p in cfg.get("include", [])} | {"scripts", "public", "dev"}
    findings = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Constant, ast.BinOp)):
            continue
        parent = parents.get(node)
        if isinstance(parent, ast.BinOp):
            continue
        # Ignore docstrings and fixture dictionaries/collections: those describe data being created.
        if isinstance(parent, ast.Expr) or isinstance(parent, (ast.Dict, ast.Set, ast.List, ast.Tuple)):
            continue
        value = _path(node).replace("\\", "/")
        if isinstance(node, ast.BinOp):
            base = node
            while isinstance(base, ast.BinOp):
                base = base.left
            if isinstance(base, ast.Attribute) or (isinstance(base, ast.Name) and base.id != "ROOT"):
                continue  # temp-root fixture paths, not dependencies on this repository
        ancestor = parent
        probe = False
        while ancestor is not None and not isinstance(ancestor, (ast.stmt, ast.Module)):
            if isinstance(ancestor, ast.Call) and isinstance(ancestor.func, ast.Attribute):
                probe |= ancestor.func.attr in {"exists", "is_file", "is_dir", "startswith", "assertFalse"}
            ancestor = parents.get(ancestor)
        if probe:
            continue  # availability probes / exclusion assertions do not read a dependency
        if value == "check_export_cases":
            value = "harness/selftest/check_export_cases.py"
        if not re.fullmatch(r"[\w./*-]+", value) or "/" not in value or value.split("/")[0] not in roots:
            continue
        if included(value, cfg):
            continue
        scope = node
        while scope in parents and not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scope = parents[scope]
        if module_guard or (isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)) and scope.name in guarded):
            continue
        findings.add(f"{node.lineno}: {value}")
    return sorted(findings)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    root = ap.parse_args().root
    config = root / "public/export.toml"
    if not config.is_file():
        print("SKIP public/export.toml 없음(공개본·소비 프로젝트)")
        return 0
    cfg = tomllib.loads(config.read_text(encoding="utf-8"))["export"]
    bad = []
    files = [p for p in sorted((root / "harness/selftest").glob("check_*.py"))
             if included(p.relative_to(root).as_posix(), cfg)]
    for path in files:
        for finding in inspect_source(path.read_text(encoding="utf-8"), cfg):
            bad.append(f"{path.name}:{finding}")
    for finding in bad:
        print(f"BAD {finding} — 제외 경로 참조에 존재 확인 + SKIP 가드 없음")
    print(f"{'BAD' if bad else 'OK'} export refs: {len(files)} files, {len(bad)} findings")
    return int(bool(bad))


if __name__ == "__main__":
    raise SystemExit(main())
