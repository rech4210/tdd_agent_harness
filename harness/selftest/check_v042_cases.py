"""하네스 자기 검사: core-v0.4.2 (HD-27, WO-H23).

P01 ~ P25: GT 경계표 승격 근거로 제품 불변식 INV-G# (설정 product_invariants 의 정의 파일).
고치기 전 Red 17개 / 전체 31개 (2026-10-14, Windows · Python 3.11.9, 구현 전 엔진):
P01 ~ P12 · P22a ~ P22c · P23 · P25. 나머지 14개 (섞기 · 형식 거부, 기존 형식 회귀) 는 OK.
사용: python harness/selftest/check_v042_cases.py
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
from check_v04_cases import CFG, DEFAULTS, SPEC_HEAD, TEST, boundary_bad  # noqa: E402
from traceability import check_traceability  # noqa: E402

DECISIONS = "# 결정 기록\n\n## D-01 n\n\n정수.\n\n## D-02 n\n\n정수.\n"
PRODUCT = """# 제품

## 불변식

| ID | 불변식 |
|:---|:---|
| INV-G1 | n 은 정수다 |
| **INV-G2** | n 은 0 이상이다 |

<!--
| INV-G8 | 주석 안 |
-->

```
| INV-G9 | 코드 블록 안 |
```

슬라이스 명세는 `INV-G7` 처럼 불변식을 그대로 인용한다.
"""
U = "정의되지 않은 불변식: '{}'"
PCFG = {**CFG, "product_invariants": ["docs/PRODUCT.md"]}


def gt(cell: str, cfg: dict | None = None, product: str | None = PRODUCT,
       spec_head: str = SPEC_HEAD) -> tuple[bool, list[str], list[str]]:
    root = Path(tempfile.mkdtemp(prefix="v042-gt-"))
    try:
        files = {
            "specs/001-input/spec.md": spec_head + f"| n | 0 ~ | 0 포함 | {cell} |\n",
            "specs/001-input/samples/original-1.json": "{}\n",
            "reports/decisions.md": DECISIONS,
            "docs/DEFAULTS.md": DEFAULTS,
            "tests/unit/test_input.py": TEST,
        }
        if product is not None:
            files["docs/PRODUCT.md"] = product
        for rel, content in files.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
        return check_traceability(PCFG if cfg is None else cfg, root)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def p_cases(results: list[tuple[str, bool, str]]) -> None:
    def case(cid: str, cell: str, expect_ok: bool, need: str = "", **kw) -> None:
        ok, bad, notes = gt(cell, **kw)
        bb = boundary_bad(bad)
        if expect_ok:
            good = ok and not bad and "계약 안 1 · 계약 밖 0" in " ".join(notes)
        else:
            good = not ok and bool(bb) and need in " ".join(bb)
        results.append((cid, good, f"{cell!r}: " + ("; ".join(bad) or "; ".join(notes))))

    # 통과: 정의된 INV-G 단독 · D-## 와 섞기
    case("P01 정의된 INV-G 단독, 계약 안 1", "INV-G1", True)
    case("P02 INV-G 와 D-## 섞기", "INV-G1, D-01", True)
    case("P03 D-## 먼저, 굵은 글씨로 정의된 INV-G", "D-01, INV-G2", True)
    case("P04 INV-G 둘", "INV-G1, INV-G2", True)
    case("P05 공백 없음 · 항목 앞뒤 공백", "INV-G1,D-01 , D-02", True)

    # 거부: 미정의
    case("P06 미정의 INV-G5, 메시지에 INV-G5", "INV-G5", False, U.format("INV-G5"))
    case("P07 정의된 INV-G 와 미정의 D-99, 메시지에 D-99", "INV-G1, D-99", False, "정의되지 않은 결정: 'D-99'")
    case("P08 미정의 INV-G5 와 정의된 D-01, 메시지에 INV-G5", "D-01, INV-G5", False, U.format("INV-G5"))
    case("P09 HTML 주석 안의 정의는 정의가 아니다", "INV-G8", False, U.format("INV-G8"))
    case("P10 코드 블록 안의 정의는 정의가 아니다", "INV-G9", False, U.format("INV-G9"))
    case("P11 본문 언급은 정의가 아니다", "INV-G7", False, U.format("INV-G7"))
    case("P12 명세의 인용 블록은 정의가 아니다", "INV-G3", False, U.format("INV-G3"),
         spec_head=SPEC_HEAD.replace("## Requirements", "> **INV-G3**: n 은 정수다\n\n## Requirements"))

    # 거부: 다른 종류와 섞기 · 형식 (HD-26 유지)
    case("P13 정의된 DEF 와 섞기", "INV-G1, DEF-01", False, "승격 근거 올바르지 않음")
    case("P14 표본과 섞기", "INV-G1, 표본:samples/original-1.json", False, "승격 근거 올바르지 않음")
    case("P15 피해와 섞기", "INV-G1, 피해: n", False, "승격 근거 올바르지 않음")
    case("P16 계약 밖과 섞기", "INV-G1, 계약 밖", False, "승격 근거 올바르지 않음")
    case("P17 결정 대기와 섞기", "INV-G1, [결정 대기]", False, "승격 근거 올바르지 않음")
    case("P18a 끝 빈 항목", "INV-G1,", False, "승격 근거 올바르지 않음")
    case("P18b 첫 빈 항목", ", INV-G1", False, "승격 근거 올바르지 않음")
    case("P19 쉼표 없음", "INV-G1 INV-G2", False, "승격 근거 올바르지 않음")
    case("P20 전각 쉼표", "INV-G1，D-01", False, "")
    case("P21 소문자", "inv-g1", False, "")

    # 설정
    case("P22a 설정 없으면 INV-G 단독 거부 (기존 동작)", "INV-G1", False, U.format("INV-G1"), cfg=CFG)
    case("P22b 설정 없으면 INV-G 섞기 거부 (기존 동작)", "INV-G1, D-01", False, U.format("INV-G1"), cfg=CFG)
    case("P22c 설정했지만 파일 없음", "INV-G1", False, U.format("INV-G1"), product=None)
    case("P23 ID 형식 바꾸기 (id_patterns.product_invariant)", "PI-1, D-01", True,
         cfg={**PCFG, "id_patterns": {"product_invariant": r"PI-\d+"}},
         product="# 제품\n\n| ID | 불변식 |\n|:---|:---|\n| PI-1 | n 은 정수다 |\n")

    # 회귀: 설정이 있어도 기존 형식은 그대로
    for cid, cell in (("P24a", "D-01"), ("P24b", "D-01, D-02"), ("P24c", "DEF-01"), ("P24d", "계약 밖")):
        ok, bad, notes = gt(cell)
        want = "계약 안 0 · 계약 밖 1" if cell == "계약 밖" else "계약 안 1 · 계약 밖 0"
        results.append((f"{cid} 회귀 {cell}", ok and not bad and want in " ".join(notes),
                        f"{cell!r}: " + ("; ".join(bad) or "; ".join(notes))))

    # 정의 파일의 형식 오류도 보고한다 (다른 정의 파일과 같다)
    ok, bad, _ = gt("D-01", product="# 제품\n\n```\n| INV-G1 | 닫히지 않음 |\n")
    results.append(("P25 정의 파일의 닫히지 않은 코드 블록은 [형식] 위반",
                    not ok and any(b.startswith("[형식] docs/PRODUCT.md") for b in bad), "; ".join(bad)))


def main() -> int:
    results: list[tuple[str, bool, str]] = []
    p_cases(results)
    for name, good, detail in results:
        print(f"{'OK ' if good else 'BAD'} {name}  — {detail}")
    bad_count = sum(not good for _, good, _ in results)
    print(f"결과: OK {len(results) - bad_count} / BAD {bad_count} / 합계 {len(results)}")
    return 1 if bad_count else 0


if __name__ == "__main__":
    sys.exit(main())
