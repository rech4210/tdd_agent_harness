"""하네스 자기 검사: 아키텍처 규칙 평가기(harness/arch.py)의 판정 사례.

어댑터가 낸 정규화 import 그래프를 직접 만들어 check_architecture 에 넣는다 (언어를 모른다는 것을 사례로 보인다).
F1 = 선언된 레이어 밖의 파일 (닫힌 레이어), F2 = 모듈 경로 구분자 (Python '.', JS '/' · 'node:').

사용: python harness/selftest/check_arch_cases.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness"))
from arch import check_architecture  # noqa: E402


def node(file: str, imports: list[str]) -> dict:
    module = file.rsplit(".", 1)[0].replace("/", ".")
    return {"file": file, "module": module, "imports": imports, "uses": [], "const_member": [], "dynamic": []}


BASE = {
    "package": "pkg",
    "groups": {"pure": ["dataclasses", "__future__"]},
    "inner": ["domain"],
    "test_frameworks": ["pytest", "node:test"],
    "checks": ["source_tree", "layers", "never_import", "inner", "test_frameworks"],
    "layers": {"__init__.py": ["@pure"], "domain": ["@pure", "pkg.domain"]},
}
NORMAL = [node("pkg/__init__.py", []), node("pkg/domain/money.py", ["dataclasses", "pkg.domain.errors"]),
          node("pkg/domain/errors.py", [])]
INFRA = NORMAL + [node("pkg/infra/clock.py", ["dataclasses"])]


def case(cid: str, why: str, expect_ok: bool, graph: list[dict], cfg: dict | None = None, text: str = "") -> tuple:
    ok, bad = check_architecture({**BASE, **(cfg or {})}, graph, {"domain", "infra"})
    good = ok == expect_ok and (not text or any(text in b for b in bad))
    return cid, why, good, f"{'PASS' if ok else 'FAIL'} {bad[0][:80] if bad else ''}"


def js_layers(allow: list[str]) -> dict:
    return {"layers": {"__init__.py": ["@pure"], "domain": ["@pure", "pkg.domain", *allow]}}


def main() -> int:
    rows = [
        case("A01", "정상", True, NORMAL),
        # F1: 닫힌 레이어 (선택, 기본 꺼짐)
        case("A02", "F1 closed_layers = true: 선언된 레이어 밖의 파일 → 실패", False, INFRA,
             {"closed_layers": True}, "선언된 레이어 밖"),
        case("A03", "F1 closed_layers 가 없으면 지금처럼 통과 (기존 프로젝트 판정 불변)", True, INFRA),
        case("A04", "F1 closed_layers = true 라도 선언된 레이어 안의 파일만 있으면 통과", True, NORMAL, {"closed_layers": True}),
        case("A05", "F1 closed_layers 는 only_layers(슬라이스)와 함께여도 선언 전체 기준", False, INFRA,
             {"closed_layers": True, "only_layers": ["domain"]}, "선언된 레이어 밖"),
        # F2: 모듈 경로 구분자
        case("A06", "F2 허용 'node:fs' 는 하위 경로 'node:fs/promises' 도 허용", True,
             NORMAL + [node("pkg/domain/io.js", ["node:fs/promises"])], js_layers(["node:fs"])),
        case("A07", "F2 허용 'chart.js' 는 'chart.js/auto' 도 허용", True,
             NORMAL + [node("pkg/domain/c.js", ["chart.js/auto"])], js_layers(["chart.js"])),
        case("A08", "F2 구분자로 끝나는 허용 'node:' 는 계열 전체 ('node:path')", True,
             NORMAL + [node("pkg/domain/p.js", ["node:path"])], js_layers(["node:"])),
        case("A09", "F2 허용 'node:fs' 가 'node:fsx' 까지 넓어지지 않는다", False,
             NORMAL + [node("pkg/domain/x.js", ["node:fsx"])], js_layers(["node:fs"]), "node:fsx"),
        case("A10", "F2 테스트 도구 'node:test' 는 'node:test/reporters' 도 잡는다", False,
             NORMAL + [node("pkg/domain/t.js", ["node:test/reporters"])], js_layers(["node:test"]), "[테스트 도구]"),
        case("A11", "Python 이름은 그대로: 'pkg.domain' 이 'pkg.domainx' 를 허용하지 않는다", False,
             NORMAL + [node("pkg/domain/y.py", ["pkg.domainx"])], None, "pkg.domainx"),
        case("A12", "Python 테스트 도구 'pytest' 는 'pytest.mark' 를 잡고 'pytestx' 는 잡지 않는다", False,
             NORMAL + [node("pkg/domain/z.py", ["pytest.mark", "pytestx"])],
             js_layers(["pytestx", "pytest.mark"]), "[테스트 도구] pkg/domain/z.py: import pytest.mark"),
    ]
    # WO-H09 레드팀에서 나온 정책 공백 (HD-13 · HD-14)
    rows.append(case("A14", "HD-13 금지 import 도 모듈 경계로: 'pkg.adapters' 금지가 'pkg.adaptersx' 를 잡지 않는다", True,
                     NORMAL + [node("pkg/domain/a.py", ["pkg.adaptersx"])],
                     {**js_layers(["pkg.adaptersx"]), "never_import": [{"from": "domain", "prefixes": ["pkg.adapters"]}]}))
    rows.append(case("A15", "HD-13 금지 import 의 하위 모듈은 여전히 잡는다 ('pkg.adapters.db')", False,
                     NORMAL + [node("pkg/domain/a.py", ["pkg.adapters.db"])],
                     {**js_layers(["pkg.adapters.db"]), "never_import": [{"from": "domain", "prefixes": ["pkg.adapters"]}]},
                     "[금지 import]"))
    rows.append(case("A16", "HD-14 closed_layers = true 는 checks 에 layers 가 없어도 적용된다", False, INFRA,
                     {"closed_layers": True, "checks": ["source_tree"]}, "선언된 레이어 밖"))
    # A12 는 pytestx 가 테스트 도구로 잡히면 안 된다
    extra_ok, extra_bad = check_architecture({**BASE, **js_layers(["pytestx", "pytest.mark"])},
                                             NORMAL + [node("pkg/domain/z.py", ["pytestx"])], {"domain"})
    rows.append(("A13", "Python 'pytestx' 는 테스트 도구가 아니다", extra_ok, f"{'PASS' if extra_ok else 'FAIL'} {extra_bad[:1]}"))

    all_good = True
    for cid, why, good, got in rows:
        all_good &= good
        print(f"{'OK ' if good else 'BAD'} {cid}  → {got}   # {why}")
    print("결과: 모든 사례가 기대와 일치" if all_good else "결과: 기대와 다른 사례가 있다")
    return 0 if all_good else 1


if __name__ == "__main__":
    sys.exit(main())
