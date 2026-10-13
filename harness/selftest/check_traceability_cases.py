"""하네스 자기 검사: 추적성 게이트(GT, harness/traceability.py)의 판정 사례.

사례마다 임시 프로젝트(명세 · 결정 기록 · 테스트 모듈)를 만들고 check_traceability 의 판정이 기대와 같은지 본다.
R = 이미 있는 동작의 회귀, N = SDD 통합 보강(Spec Kit 실제 양식 대조에서 나온 요구).

사용: python harness/selftest/check_traceability_cases.py
종료 코드: 0 모든 사례가 기대와 같음, 1 다름
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness"))
from traceability import check_traceability  # noqa: E402

BASE_CFG = {
    "requirements": ["specs/*/spec.md"],
    "decisions": ["reports/decisions.md"],
    "modules": ["tests/**/test_*.py"],
}

DECISIONS = "# 결정 기록\n\n## D-1 금액 단위\n\n센트 정수.\n\n## D-2 통화\n\n원화 하나.\n"

SPEC = """# Feature Specification: 금액

## Clarifications

### Session 2026-10-10

- Q: 통화는 몇 개인가? → A: 원화 하나 → D-2

## Requirements

### Functional Requirements

- **FR-001**: 시스템은 음수 금액을 거부해야 한다
- **FR-002**: 두 금액의 합은 각 금액의 센트 합과 같아야 한다

## Invariants

- **INV-1**: 금액은 0 이상이다

## Assumptions

- 금액 단위는 센트 정수다 → D-1
- 표시 형식은 화면 담당이 정한다 → H5
"""

TEST = '''"""금액 값 객체.

guards:      FR-001, FR-002, INV-1, D-1
technique:   05 거부 영역 전체
checklist:   B-2
"""


def test_placeholder() -> None:
    assert True
'''


@dataclass
class Case:
    id: str
    why: str
    expect_ok: bool
    expect_text: str = ""             # 위반 목록(또는 참고 목록)에 있어야 하는 문자열
    files: dict[str, str] = field(default_factory=dict)   # 기본 파일을 덮어쓰거나 더한다 (빈 문자열 = 파일 지움)
    cfg: dict = field(default_factory=dict)


def spec_with(old: str, new: str) -> str:
    assert old in SPEC, old
    return SPEC.replace(old, new)


CASES = [
    # --- R: 기존 동작 회귀 ---
    Case("R01", "정상: 모든 요구 · 가정 · 명확화가 해소되고 테스트가 지킨다", True),
    Case("R02", "머리말 없는 테스트 모듈", False, "[머리말]",
         files={"tests/unit/test_extra.py": "def test_x() -> None:\n    assert True\n"}),
    Case("R03", "지키는 테스트가 없는 요구", False, "[정방향] FR-003",
         files={"specs/001-money/spec.md": spec_with("- **FR-002**", "- **FR-003**: 금액은 통화를 가진다\n- **FR-002**")}),
    Case("R04", "guards 에 열린 질문", False, "[ID]",
         files={"tests/unit/test_money.py": TEST.replace("D-1\n", "D-1, O-3\n")}),
    Case("R05", "guards 가 결정 기록에 없는 결정을 인용", False, "[역방향] D-9",
         files={"tests/unit/test_money.py": TEST.replace("D-1\n", "D-1, D-9\n")}),
    Case("R06", "승격 · 표시 없는 가정", False, "[가정]",
         files={"specs/001-money/spec.md": spec_with("→ H5", "")}),
    Case("R07", "테스트에 미결정 표시", False, "[미결정]",
         files={"tests/unit/test_money.py": TEST + "\nMIN = 0  # [사람 결정 C-A3]\n"}),
    Case("R08", "테스트 모듈 0개 (공허한 참)", False, "테스트 모듈이 없다",
         files={"tests/unit/test_money.py": ""}),
    Case("R09", "슬라이스: 범위 안 요구를 미루면 통과하고 참고로 남는다", True, "(미룸) FR-002",
         files={"tests/unit/test_money.py": TEST.replace("FR-001, FR-002", "FR-001")},
         cfg={"in_scope": ["FR-001", "FR-002"], "deferred": {"FR-002": "H2: 합산 규칙 미결정"}}),
    Case("R10", "슬라이스: 범위 밖 요구는 테스트가 없어도 된다", True,
         files={"tests/unit/test_money.py": TEST.replace("FR-001, FR-002", "FR-001")},
         cfg={"in_scope": ["FR-001"]}),

    # --- N: SDD 통합 보강 ---
    Case("N01", "가정의 승격 대상 D 가 결정 기록에 없다 (표시만 있는 승격)", False, "D-99",
         files={"specs/001-money/spec.md": spec_with("센트 정수다 → D-1", "센트 정수다 → D-99")}),
    Case("N02", "명확화(clarify) 답변이 결정 기록으로 이어지지 않았다", False, "[명확화]",
         files={"specs/001-money/spec.md": spec_with("→ A: 원화 하나 → D-2", "→ A: 원화 하나")}),
    Case("N03", "명확화 답변의 D 가 결정 기록에 없다", False, "D-77",
         files={"specs/001-money/spec.md": spec_with("→ A: 원화 하나 → D-2", "→ A: 원화 하나 → D-77")}),
    Case("N04", "전체 게이트(슬라이스 아님)에서 미룬 요구가 남아 있다 (릴리스 차단)", False, "[미룸]",
         files={"tests/unit/test_money.py": TEST.replace("FR-001, FR-002", "FR-001")},
         cfg={"deferred": {"FR-002": "H2: 합산 규칙 미결정"}}),
    Case("N05", "명세에 [NEEDS CLARIFICATION] 이 남아 있다", False, "[명확화 필요]",
         files={"specs/001-money/spec.md": spec_with(
             "음수 금액을 거부해야 한다", "음수 금액을 [NEEDS CLARIFICATION: 0 원은 허용하나?] 거부해야 한다")}),
    Case("N06", "슬라이스: 범위 밖 요구 줄의 [NEEDS CLARIFICATION] 은 다음 슬라이스 몫이다", True,
         files={"specs/001-money/spec.md": spec_with(
             "- **FR-002**", "- **FR-003**: 환율은 [NEEDS CLARIFICATION: 출처?] 를 따른다\n- **FR-002**")},
         cfg={"in_scope": ["FR-001", "FR-002"]}),
    Case("N07", "슬라이스: 범위 안 요구 줄의 [NEEDS CLARIFICATION] 은 실패", False, "[명확화 필요]",
         files={"specs/001-money/spec.md": spec_with(
             "합은 각 금액의", "합은 [NEEDS CLARIFICATION: 넘침 처리?] 각 금액의")},
         cfg={"in_scope": ["FR-001", "FR-002"]}),
    Case("N08", "HTML 주석 속 ID 는 요구가 아니다 (양식 안내문)", True,
         files={"specs/001-money/spec.md": spec_with(
             "### Functional Requirements", "### Functional Requirements\n\n<!-- 예: - **FR-009**: 시스템은 ... -->")}),
    Case("N09", "HTML 주석 속 가정 · 명확화 목록은 검사 대상이 아니다", True,
         files={"specs/001-money/spec.md": spec_with(
             "## Assumptions\n", "## Assumptions\n\n<!--\n- [가정 예시: 사용자는 인터넷에 연결되어 있다]\n-->\n")}),
    Case("N10", "HTML 주석 안에서만 정의된 요구를 테스트가 인용하면 역방향 실패", False, "[역방향] FR-009",
         files={"specs/001-money/spec.md": spec_with(
             "### Functional Requirements", "### Functional Requirements\n\n<!-- 예: - **FR-009**: 시스템은 ... -->"),
                "tests/unit/test_money.py": TEST.replace("D-1\n", "D-1, FR-009\n")}),
    Case("N11", "채우지 않은 Spec Kit 양식 모양 (자리표시자 요구 · 가정 · 명확화 필요)", False, "[명확화 필요]",
         files={"specs/001-money/spec.md":
                "# Feature Specification: [FEATURE NAME]\n\n## Requirements\n\n"
                "- **FR-001**: System MUST [specific capability]\n"
                "- **FR-006**: System MUST authenticate users via [NEEDS CLARIFICATION: auth method not specified]\n\n"
                "## Assumptions\n\n- [Assumption about target users]\n"}),

    # --- N12 ~: 2회차 보강 (WO-H02 검토 · WO-H03 레드팀에서 뚫린 것) ---
    Case("N12", "에이전트의 대기 표시 '→ [결정 대기]' 는 해소가 아니다", False, "[가정]",
         files={"specs/001-money/spec.md": spec_with("센트 정수다 → D-1", "센트 정수다 → [결정 대기]")}),
    Case("N13", "화살표 없는 H 단어('H2 데이터베이스')는 해소가 아니다", False, "[가정]",
         files={"specs/001-money/spec.md": spec_with("화면 담당이 정한다 → H5", "저장은 H2 데이터베이스를 쓴다")}),
    Case("N14", "'→ [H2 미결정]' 은 해소가 아니다 (괄호 안 자리표시자)", False, "[가정]",
         files={"specs/001-money/spec.md": spec_with("화면 담당이 정한다 → H5", "화면 담당이 정한다 → [H2 미결정]")}),
    Case("N15", "명확화 답 안의 H2 는 해소가 아니다", False, "[명확화]",
         files={"specs/001-money/spec.md": spec_with("→ A: 원화 하나 → D-2", "→ A: H2 프로토콜을 쓴다")}),
    Case("N16", "결정 기록 본문에서만 언급된 D 는 정의가 아니다 (가정 승격)", False, "D-99",
         files={"reports/decisions.md": DECISIONS + "\n메모: D-99 는 다음 회의에서 정한다.\n",
                "specs/001-money/spec.md": spec_with("센트 정수다 → D-1", "센트 정수다 → D-99")}),
    Case("N17", "결정 기록 본문에서만 언급된 D 를 guards 가 인용 → 역방향 실패", False, "[역방향] D-98",
         files={"reports/decisions.md": DECISIONS + "\n메모: D-98 은 보류.\n",
                "tests/unit/test_money.py": TEST.replace("D-1\n", "D-1, D-98\n")}),
    Case("N18", "명세 산문 속 FR 은 요구 정의가 아니다 → guards 인용은 역방향 실패", False, "[역방향] FR-999",
         files={"specs/001-money/spec.md": spec_with("## Invariants", "참고: FR-999 는 다음 기능에서 다룬다.\n\n## Invariants"),
                "tests/unit/test_money.py": TEST.replace("D-1\n", "D-1, FR-999\n")}),
    Case("N19", "명세 산문 속 FR 언급은 정방향 대상이 아니다", True,
         files={"specs/001-money/spec.md": spec_with("## Invariants", "참고: FR-777 은 폐기되었다.\n\n## Invariants")}),
    Case("N20", "표 첫 칸의 요구 정의도 요구다 → 테스트 없으면 정방향 실패", False, "[정방향] FR-003",
         files={"specs/001-money/spec.md": spec_with("## Invariants", "| ID | 요구 |\n|:---|:---|\n| FR-003 | 환불한다 |\n\n## Invariants")}),
    Case("N21", "소문자 [needs clarification 표시도 잡는다", False, "[명확화 필요]",
         files={"specs/001-money/spec.md": spec_with("음수 금액을 거부해야 한다", "음수 금액을 [needs clarification: 0 원?] 거부해야 한다")}),
    Case("N22", "전각 ［NEEDS CLARIFICATION 표시도 잡는다", False, "[명확화 필요]",
         files={"specs/001-money/spec.md": spec_with("음수 금액을 거부해야 한다", "음수 금액을 ［NEEDS CLARIFICATION: 0 원?］ 거부해야 한다")}),
    Case("N23", "결정 기록의 표 첫 칸(**D-3**) 도 정의다", True,
         files={"reports/decisions.md": DECISIONS + "\n| ID | 결정 |\n|:---|:---|\n| **D-3** | 반올림 없음 |\n",
                "specs/001-money/spec.md": spec_with("→ H5", "→ D-3")}),
    Case("N24", "ASCII 화살표 '->' 도 승격 표시다", True,
         files={"specs/001-money/spec.md": spec_with("센트 정수다 → D-1", "센트 정수다 -> D-1")}),

    # --- N25 ~: 3회차 보강 (WO-H05 2회차 레드팀: 중복 정의 뚫림 · 코드 블록 · 이어진 줄 오탐) ---
    Case("N25", "같은 FR 을 두 번 정의하면 실패 (뜻이 둘이면 테스트가 어느 쪽을 지키는지 모른다)", False, "[중복 정의] FR-001",
         files={"specs/001-money/spec.md": spec_with("## Invariants", "- **FR-001**: 다른 뜻의 중복 정의\n\n## Invariants")}),
    Case("N26", "같은 INV 를 두 번 정의하면 실패", False, "[중복 정의] INV-1",
         files={"specs/001-money/spec.md": spec_with("## Assumptions", "- **INV-1**: 다른 뜻\n\n## Assumptions")}),
    Case("N27", "같은 D 를 두 번 정의하면 실패 (제목 + 표 첫 칸 포함)", False, "[중복 정의] D-1",
         files={"reports/decisions.md": DECISIONS + "\n| ID | 결정 |\n|:---|:---|\n| **D-1** | 다른 뜻 |\n"}),
    Case("N28", "코드 블록(```) 안의 목록 · 표는 정의 · 가정 항목이 아니다 (중복도 아니다)", True,
         files={"specs/001-money/spec.md": spec_with("## Assumptions\n", "## Assumptions\n\n```markdown\n- 가짜 가정\n"
                                                    "- **FR-001**: 예시 속 정의\n| FR-901 | 예시 표 |\n```\n")}),
    Case("N29", "코드 블록(``` · ~~~) 안의 [NEEDS CLARIFICATION 은 표시가 아니다", True,
         files={"specs/001-money/spec.md": SPEC + "\n```text\n- **FR-0XX**: [NEEDS CLARIFICATION: 예시]\n```\n\n"
                "~~~\n［NEEDS CLARIFICATION: 예시］\n~~~\n"}),
    Case("N30", "목록 항목의 이어진 줄(들여쓰기)에 있는 승격도 그 항목의 승격이다", True,
         files={"specs/001-money/spec.md": spec_with("센트 정수다 → D-1", "센트 정수다\n  최종 승격 → D-1")}),
    Case("N31", "이어진 줄 합치기가 다음 항목을 삼키지 않는다 (다음 항목은 따로 검사)", False, "[가정]",
         files={"specs/001-money/spec.md": spec_with("화면 담당이 정한다 → H5", "화면 담당이 정한다 → H5\n- 승격 없는 다음 항목")}),
    Case("N32", "인라인 코드(`...`) 안의 표시는 언급이지 표시가 아니다", True,
         files={"specs/001-money/spec.md": SPEC + "\n표기법 안내: 모르는 값은 `[NEEDS CLARIFICATION: 질문]` 으로 적는다.\n"}),
    Case("N33", "일반 산문 속 표시 문자열은 표시로 본다 (보수적, HD-10: 설명은 인라인 코드나 HTML 주석에)", False, "[명확화 필요]",
         files={"specs/001-money/spec.md": SPEC + "\n이 문서는 ‘[NEEDS CLARIFICATION’ 표기법을 설명한다.\n"}),

    # --- N34 ~: 4회차 (WO-H06 검토에서 Claude 가 발견) ---
    Case("N34", "닫히지 않은 코드 블록은 실패 (뒤의 가정 · 표시를 모두 숨기는 통로)", False, "[형식]",
         files={"specs/001-money/spec.md": spec_with("## Invariants", "```\n\n## Invariants")
                + "\n- 승격 없는 가정\n- **FR-005**: [NEEDS CLARIFICATION: 숨김]\n"}),
    Case("N35", "결정 기록의 닫히지 않은 코드 블록도 실패", False, "[형식]",
         files={"reports/decisions.md": DECISIONS + "\n~~~\n## D-9 숨은 결정\n"}),
]


# H-05: 명세 파일(기능 폴더)별 이름공간 (사람 결정 (a), 2026-10-11). 테스트 폴더 배치에 기대지 않는다 (tests/unit 그대로).
# 규칙: 한정 ID `<기능 폴더>:FR-001` 은 그 명세의 ID. 접두어 없는 ID 는 모든 명세에서 **하나뿐일 때만** 그 ID, 여러 명세에 있으면 [모호].
def namespace_files(a_guard: str, b_guard: str, extra: str = "", duplicate: bool = False) -> dict[str, str]:
    def header(guards: str) -> str:
        return f'"""최소 사례.\nguards: {guards}\ntechnique: 05\nchecklist: B-1\n"""\ndef test_sample():\n    assert True\n'
    return {
        "specs/001-a/spec.md": "# A\n- **FR-001**: 동작 A\n" + ("- **FR-001**: 중복\n" if duplicate else ""),
        "specs/002-b/spec.md": "# B\n- **FR-001**: 동작 B\n" + extra,
        "tests/unit/test_a.py": header(a_guard), "tests/unit/test_b.py": header(b_guard),
    }

CASES += [
    Case("H05-01", "명시 guards로 다른 명세의 동일 ID 구분 (중복 정의 아님)", True,
         files=namespace_files("001-a:FR-001", "002-b:FR-001")),
    Case("H05-02", "여러 명세에 있는 ID를 접두어 없이 인용하면 모호", False, "[모호]",
         files=namespace_files("FR-001", "002-b:FR-001")),
    Case("H05-03", "한 명세에만 있는 ID는 접두어 없이 인용 가능", True,
         files=namespace_files("001-a:FR-001", "002-b:FR-001, FR-002", "- **FR-002**: 동작 C\n")),
    Case("H05-04", "접두어로 다른 슬라이스 요구 인용 허용", True,
         files=namespace_files("001-a:FR-001, 002-b:FR-002", "002-b:FR-001", "- **FR-002**: 동작 C\n")),
    Case("H05-05", "없는 기능 폴더 접두어는 명세에 없는 요구", False, "[역방향]",
         files=namespace_files("001-a:FR-001, 009-x:FR-001", "002-b:FR-001")),
    Case("H05-R01", "같은 파일 안 중복은 여전히 위반", False, "[중복 정의]",
         files=namespace_files("001-a:FR-001", "002-b:FR-001", duplicate=True)),
]


def run(case: Case) -> tuple[bool, str]:
    root = Path(tempfile.mkdtemp(prefix="gt-case-"))
    try:
        files = {"specs/001-money/spec.md": SPEC, "reports/decisions.md": DECISIONS, "tests/unit/test_money.py": TEST}
        if case.id.startswith("H05-"):
            files = {}
        files.update(case.files)
        for rel, text in files.items():
            if text == "":
                continue
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8", newline="\n")
        ok, bad, notes = check_traceability({**BASE_CFG, **case.cfg}, root)
        lines = bad + notes
        good = ok == case.expect_ok and (not case.expect_text or any(case.expect_text in x for x in lines))
        return good, f"{'PASS' if ok else 'FAIL'}  {lines[0][:100] if lines else ''}"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main() -> int:
    all_good = True
    for case in CASES:
        try:
            good, got = run(case)
        except Exception as e:  # 판정기가 죽는 것도 사례 실패다
            good, got = False, f"예외 {type(e).__name__}: {e}"
        all_good &= good
        expect = ("PASS" if case.expect_ok else "FAIL") + (f" '{case.expect_text}'" if case.expect_text else "")
        print(f"{'OK ' if good else 'BAD'} {case.id}  기대 {expect:28} → {got}   # {case.why}")
    print("결과: 모든 사례가 기대와 일치" if all_good else "결과: 기대와 다른 사례가 있다")
    return 0 if all_good else 1


if __name__ == "__main__":
    sys.exit(main())
