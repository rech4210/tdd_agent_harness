"""아키텍처 규칙 평가기 (Core, K8). 언어를 모른다.

입력: 어댑터가 추출한 정규화 import 그래프 (파일마다 module · imports · uses · const_member · dynamic)
     + 설정 [architecture] (레이어 규칙, 금지 호출, 테스트 도구, 필수 최상위 폴더)
출력: (통과 여부, 위반 목록)

설정 예 (config/architecture.toml):

    [architecture]
    package = "<패키지 이름>"             # 소스 루트 아래 최상위 패키지
    closed_layers = true                  # 선언된 레이어 밖의 파일 금지 (선택, 기본 거짓)
    groups = { pure = ["dataclasses", "datetime", ...] }
    inner = ["<내부 레이어 경로>", "__init__.py"]   # 시각 · 난수 · 환경 직접 접근 · 동적 접근 금지
    forbidden_calls = [["datetime", "now"], ...]   # 이름의 마지막 두 마디
    forbidden_names = ["now", "environ", "datetime", ...]  # 상수 이름 접근(예: getattr(x, "now"))으로 금지
    test_frameworks = ["<테스트 도구 최상위 모듈>", ...]
    required_top_level = ["<폴더>", ...]
    checks = ["source_tree", "layers", "never_import", "inner", "test_frameworks", "top_level"]
    [architecture.layers]
    "<레이어 경로>" = ["@pure", "<허용 import 접두사>", ...]
    # 접두 비교: 이름 == 접두, 접두 + '.' · '/', 접두가 구분자('.', '/', ':')로 끝날 때 일치
    [[architecture.never_import]]
    from = "<레이어 경로>"
    prefixes = ["<금지 import 접두사>", ...]

모든 규칙은 검사 대상이 0개이면 실패한다 (공허한 참 방지).
"""

from __future__ import annotations


def _expand(allow: list[str], groups: dict[str, list[str]]) -> set[str]:
    out: set[str] = set()
    for item in allow:
        out |= set(groups.get(item[1:], [])) if item.startswith("@") else {item}
    return out


def _allowed(name: str, prefixes: set[str]) -> bool:
    return any(
        name == p
        or name.startswith(p + ".")
        or name.startswith(p + "/")
        or (p.endswith((".", "/", ":")) and name.startswith(p))
        for p in prefixes
    )


def _in_layer(facts: dict, package: str, layer: str) -> bool:
    rel = facts["file"]
    target = f"{package}/{layer}"
    return rel == target or rel.startswith(target.rstrip("/") + "/")


def _line(item: str) -> tuple[str, str]:
    line, _, rest = item.partition(":")
    return line, rest


def check_architecture(cfg: dict, graph: list[dict], top_level_dirs: set[str]) -> tuple[bool, list[str]]:
    package = cfg["package"]
    groups = cfg.get("groups", {})
    checks = cfg.get("checks", ["source_tree", "layers", "never_import", "inner", "test_frameworks", "top_level"])
    only_layers = cfg.get("only_layers")
    files = [f for f in graph if f["file"].startswith(package + "/")]
    bad: list[str] = []

    if "source_tree" in checks and not files:
        bad.append(f"[소스] {package} 아래 파일이 없다 (공허한 참 방지)")
        return False, bad

    if cfg.get("closed_layers"):
        all_layers = list(cfg.get("layers", {}).keys())
        for f in files:
            if not any(_in_layer(f, package, layer) for layer in all_layers):
                bad.append(f"[레이어] {f['file']}: 선언된 레이어 밖의 파일")

    if "layers" in checks:
        for layer, allow in cfg.get("layers", {}).items():
            if only_layers is not None and layer not in only_layers:
                continue
            members = [f for f in files if _in_layer(f, package, layer)]
            if not members:
                bad.append(f"[레이어] {layer}: 검사할 파일이 없다")
                continue
            prefixes = _expand(allow, groups)
            for f in members:
                for name in f["imports"]:
                    if not _allowed(name, prefixes):
                        bad.append(f"[레이어] {f['file']}: import {name}")

    if "never_import" in checks:
        only_from = cfg.get("never_import_from")  # 슬라이스가 고르는 규칙 (없으면 전부)
        for rule in cfg.get("never_import", []):
            if only_from is not None and rule["from"] not in only_from:
                continue
            members = [f for f in files if _in_layer(f, package, rule["from"])]
            if not members:
                bad.append(f"[금지 import] {rule['from']}: 검사할 파일이 없다")
                continue
            prefixes = set(rule["prefixes"])
            for f in members:
                for name in f["imports"]:
                    if _allowed(name, prefixes):
                        bad.append(f"[금지 import] {f['file']}: {name}")

    if "inner" in checks:
        forbidden_pairs = {tuple(p) for p in cfg.get("forbidden_calls", [])}
        forbidden_names = set(cfg.get("forbidden_names", []))
        members = [f for f in files if any(_in_layer(f, package, layer) for layer in cfg.get("inner", []))]
        if not members:
            bad.append("[내부] 검사할 파일이 없다")
        for f in members:
            for item in f["uses"]:
                line, name = _line(item)
                if tuple(name.split(".")[-2:]) in forbidden_pairs:
                    bad.append(f"[내부] {f['file']}:{line} {name}")
            for item in f["const_member"]:
                line, name = _line(item)
                if name in forbidden_names:
                    bad.append(f"[내부] {f['file']}:{line} 상수 이름 접근 '{name}'")
            for item in f["dynamic"]:
                line, what = _line(item)
                bad.append(f"[내부] {f['file']}:{line} {what}")

    if "test_frameworks" in checks:
        frameworks = set(cfg.get("test_frameworks", []))
        for f in files:
            for name in f["imports"]:
                if _allowed(name, frameworks):
                    bad.append(f"[테스트 도구] {f['file']}: import {name}")

    if "top_level" in checks:
        missing = set(cfg.get("required_top_level", [])) - top_level_dirs
        if missing:
            bad.append(f"[최상위] 필요한 폴더가 없다: {sorted(missing)}")

    # 같은 위반이 별칭 사슬로 여러 번 잡힐 수 있으므로 중복을 없앤다
    bad = sorted(set(bad))
    return not bad, bad
