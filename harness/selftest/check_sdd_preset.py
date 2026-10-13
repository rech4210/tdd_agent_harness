"""하네스 자기 검사: Spec Kit preset(templates/sdd/preset)의 명세 양식이 GT 와 맞물리는가.

Spec Kit 의 wrap 합성(`{CORE_TEMPLATE}` 자리에 코어 양식)을 흉내 내 합성 양식을 만들고,
  P1 양식을 채우지 않은 채 → GT 실패 (공허한 통과 없음)
  P2 에이전트가 preset 지시대로 채운 명세(가정 · 답변 = [결정 대기]) → GT 실패 (사람 결정 전)
  P3 사람이 승격(→ D-## / → H#)한 명세 → GT 통과
  P4 합성 양식의 HTML 주석 안내문이 요구 · 불변식을 만들지 않는다
코어 양식은 Spec Kit 저장소가 있으면 그것을, 없으면 같은 절 구조의 최소 양식을 쓴다.

사용: python harness/selftest/check_sdd_preset.py [--core <Spec Kit 저장소의 templates/spec-template.md>]
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness"))
from traceability import check_traceability, strip_html_comments  # noqa: E402

PRESET = ROOT / "templates" / "sdd" / "preset" / "templates" / "spec-template.md"
MIN_CORE = """# Feature Specification: [FEATURE NAME]

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST [specific capability]
- **FR-006**: System MUST authenticate users via [NEEDS CLARIFICATION: auth method not specified]

## Assumptions

- [Assumption about target users]
"""

FILLED_BY_AGENT = """# Feature Specification: 금액

## Clarifications

### Session 2026-10-10

- Q: 통화는 몇 개인가? → A: 원화 하나 → [결정 대기]

## Requirements

### Functional Requirements

- **FR-001**: 시스템은 음수 금액을 거부해야 한다
- **FR-002**: 두 금액의 합은 각 금액의 센트 합과 같아야 한다

## Assumptions

- 금액 단위는 센트 정수다 → [결정 대기]

## Invariants

- **INV-1**: 금액은 0 이상이다

## Boundary tables

| 입력 | 단위 | 정의역 | 결정 출처 |
|:---|:---|:---|:---|
| cents | 센트 | 0 이상 | [결정 대기] |
"""

DECISIONS = "# 결정 기록\n\n## D-1 금액 단위\n\n센트 정수.\n\n## D-2 통화\n\n원화 하나.\n"
TEST = '"""금액.\n\nguards:      FR-001, FR-002, INV-1, D-1\ntechnique:   05\nchecklist:   B-2\n"""\n'
CFG = {"requirements": ["specs/*/spec.md"], "decisions": ["reports/decisions.md"], "modules": ["tests/**/test_*.py"]}


def gt(spec: str) -> tuple[bool, list[str]]:
    root = Path(tempfile.mkdtemp(prefix="preset-case-"))
    try:
        for rel, text in {"specs/001-money/spec.md": spec, "reports/decisions.md": DECISIONS,
                          "tests/unit/test_money.py": TEST}.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text, encoding="utf-8", newline="\n")
        ok, bad, _ = check_traceability(CFG, root)
        return ok, bad
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--core", type=Path, help="Spec Kit 코어 spec-template.md")
    args = ap.parse_args()
    core = args.core.read_text(encoding="utf-8") if args.core else MIN_CORE
    preset = PRESET.read_text(encoding="utf-8")
    results: list[tuple[str, bool, str]] = []

    results.append(("P0 preset 양식이 wrap 자리표시자로 시작한다", preset.lstrip().startswith("{CORE_TEMPLATE}"), ""))
    composed = preset.replace("{CORE_TEMPLATE}", core)
    ok, bad = gt(composed)
    results.append(("P1 채우지 않은 합성 양식 → GT 실패", not ok, bad[0][:90] if bad else "통과함"))

    ok, bad = gt(FILLED_BY_AGENT)
    pending = [b for b in bad if "[가정]" in b or "[명확화]" in b]
    results.append(("P2 에이전트가 채운 명세([결정 대기]) → GT 실패 (가정 · 명확화)", not ok and len(pending) >= 2,
                    "; ".join(pending)[:120]))

    promoted = FILLED_BY_AGENT.replace("원화 하나 → [결정 대기]", "원화 하나 → D-2") \
                              .replace("센트 정수다 → [결정 대기]", "센트 정수다 → D-1").replace("| [결정 대기] |", "| D-1 |")
    ok, bad = gt(promoted)
    results.append(("P3 사람이 승격한 명세 → GT 통과", ok, "; ".join(bad)[:120]))

    comments = "".join(re.findall(r"<!--.*?-->", preset, flags=re.DOTALL))
    leaked = re.findall(r"\b(FR-\d{3}|INV-\d+)\b", strip_html_comments(preset).replace("{CORE_TEMPLATE}", ""))
    placeholder_ok = set(leaked) <= {"INV-1"}  # 본문의 자리표시자 줄 하나만 허용
    results.append(("P4 주석 안내문의 ID 는 요구 · 불변식을 만들지 않는다",
                    placeholder_ok and bool(re.search(r"INV-\d+|FR-\d{3}", comments)), f"본문 ID={sorted(set(leaked))}"))

    all_good = True
    for name, good, detail in results:
        all_good &= good
        print(f"{'OK ' if good else 'BAD'} {name}{('  — ' + detail) if detail else ''}")
    print("결과: 모든 사례가 기대와 일치" if all_good else "결과: 기대와 다른 사례가 있다")
    return 0 if all_good else 1


if __name__ == "__main__":
    sys.exit(main())
