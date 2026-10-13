"""<Domain> 값 객체: 생성 시 값 보존, 같음 · 순서는 원시 값과 같음, 거부 영역 전체.

guards:      <FR-###>, <INV-xx>, <D-xx>
technique:   07 생성 결과 속성 보존, 02 독립 오라클(원시 값 비교), 05 거부 영역 전체
checklist:   B-2, C-A1, C-A3, C-A4, C-A6, C-A7
decisions:   <D-xx> (정의역 · 단위 · 검증 위치)
not-covered: <이 모듈이 검사하지 않는 것과 이유>

골격 사용법: `<...>` 자리표시자를 모두 바꾼다. 남아 있으면 SyntaxError 로 즉시 실패한다 (반쯤 채운 골격이
조용히 통과하지 않게). `[사람 결정` 표시는 결정 기록에서 값을 옮긴 뒤 결정 ID 로 바꾼다.
원시 값은 정수 예시다. 다른 원시 타입(문자열 · 십진수 · 시각)이면 생성기만 그 타입으로 바꾼다.
"""

from dataclasses import fields

import pytest
from hypothesis import given
from hypothesis import strategies as st

from <package>.<module> import <Domain>, <InvalidDomain>

# ── 정의역 (결정 기록 형식 C 에서 옮긴다) ──────────────────────────────────
# [사람 결정 C-A3] 테스트 작성자가 추측하지 않는다. <D-xx> 에 없으면 테스트를 쓰지 말고 H2 로 돌린다
MIN = <D-xx: 최소값>
MAX = <D-xx: 최대값. 상한이 없으면 None ("상한 없음"도 결정이다)>
UNIT = <D-xx: 단위. 값은 UNIT 의 배수. 단위 조건이 없으면 1>

SAMPLE_HI = MAX if MAX is not None else 10**15  # 상한 없음일 때 표본 상한 (정의역 상한 아님, C-A1)


def is_valid(v: int) -> bool:
    """오라클: <D-xx> 의 허용 조건을 그대로 옮긴다. <module> 을 쓰지 않는다."""
    return v >= MIN and (MAX is None or v <= MAX) and v % UNIT == 0


valid_values = st.integers(min_value=-(-MIN // UNIT), max_value=SAMPLE_HI // UNIT).map(lambda k: k * UNIT)


# ── C-A7: 생성 시 받은 값을 정확히 가진다 ─────────────────────────────────


@given(valid_values)
def test_construction_keeps_value_exactly(v):
    """<D-xx>: 생성자는 값을 자르거나 바꾸지 않는다."""
    assert <Domain>(v).<value> == v


def test_checked_fields_are_all_public_fields():
    """공개 필드가 늘면 여기서 먼저 깨진다. 위 테스트에 새 필드 검사를 더하게 만든다."""
    assert {f.name for f in fields(<Domain>)} == {"<value>"}


# ── C-A4: 같음 · 순서 · 연산은 원시 값과 똑같다 ─────────────────────────────


@given(valid_values, valid_values)
def test_equality_and_order_match_primitive(a, b):
    """<D-xx>: 비교가 항상 참인 값 객체를 막는다. 다른 테스트가 이 == 에 기대므로 여기서 직접 고정한다."""
    x, y = <Domain>(a), <Domain>(b)
    assert (x == y) is (a == b)
    assert (x != y) is (a != b)
    assert (x < y) is (a < b)  # 순서가 정의되지 않은 타입이면 지운다 (<D-xx>)
    assert (x <= y) is (a <= b)
    if a == b:
        assert hash(x) == hash(y)


@given(valid_values, valid_values)
def test_operation_matches_primitive(a, b):
    """<D-xx>: 연산이 있으면 결과의 원시 값이 원시 연산과 같다. 연산이 없으면 이 테스트를 지운다."""
    if MAX is None or a + b <= MAX:
        assert (<Domain>(a) + <Domain>(b)).<value> == a + b


# ── C-A6: 거부 영역 전체 (조건마다 따로 깬다) ──────────────────────────────

below = st.integers(max_value=(MIN - 1) // UNIT).map(lambda k: k * UNIT)  # 하한만 깬다 (단위는 지킨다)
above = (
    st.integers(min_value=MAX // UNIT + 1).map(lambda k: k * UNIT) if MAX is not None else st.nothing()
)  # 상한만 깬다
unaligned = (
    st.builds(
        lambda k, r: k * UNIT + r,
        st.integers(min_value=-(-MIN // UNIT), max_value=SAMPLE_HI // UNIT - 1),
        st.integers(min_value=1, max_value=UNIT - 1),
    )
    if UNIT > 1
    else st.nothing()
)  # 단위만 깬다 (범위는 지킨다)


@given(st.one_of(below, above, unaligned))
def test_each_condition_rejects_on_its_own(v):
    """<D-xx>: 조건 하나만 깨진 입력도 거부한다. 예외는 클래스로만 검사한다 (메시지는 계약이 아니면 보지 않는다)."""
    with pytest.raises(<InvalidDomain>):
        <Domain>(v)


@given(st.one_of(st.integers(), valid_values))
def test_validity_matches_rule_over_whole_space(v):
    """<D-xx>: 어떤 입력이든 오라클이 유효라면 받아서 보존하고, 아니면 거부한다 (과잉 거부도 막는다)."""
    if is_valid(v):
        assert <Domain>(v).<value> == v
    else:
        with pytest.raises(<InvalidDomain>):
            <Domain>(v)
