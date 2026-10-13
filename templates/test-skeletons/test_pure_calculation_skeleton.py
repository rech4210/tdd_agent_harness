"""<function>: <무엇을 계산하나 한 줄>.

guards:      <FR-###>, <INV-xx>, <D-xx>
technique:   01 경계값 표, 02 독립 오라클 성질, 03 경계 근처 표적 생성기, 06 매개변수 가변
checklist:   B-1, B-3, B-6, C-A1, C-A2, C-A3, C-A5
decisions:   <D-xx> (경계와 소속), <D-yy> (해상도: 자르지 않음), <D-zz> (입력 정의역), <D-ww> (매개변수 범위)
not-covered: <정의역 밖 입력의 거부 (거부 영역은 값 객체 골격 · 05 로)>

골격 사용법: `<...>` 자리표시자를 모두 바꾼다. 남아 있으면 SyntaxError 로 즉시 실패한다.
입력 x 는 해상도 EPS 의 격자 위 값이다 (정수: EPS = 1, 시간 간격: 그 타입의 최소 단위, 십진수: 최소 자릿수).
"""

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from <package>.<module> import <function>

# ── 사람이 정한 값 (결정 기록에서 옮긴다) ──────────────────────────────────
# [사람 결정 C-A3] 해상도: 이 입력에서 구별하는 가장 작은 차이. 반올림 · 절삭을 하는지도 <D-yy> 에 있어야 한다
EPS = <D-yy: 최소 해상도>
BIG_STEP = <큰 단위: 반올림 구현이 흔히 쓰는 단위. 경계값 표의 "B-big" 행용>
# [사람 결정 C-A3 · H2] 경계와 소속: 정확히 경계값은 어느 규칙인가
BOUNDARIES = [<D-xx: 경계 1>, <D-xx: 경계 2>]
# [사람 결정 C-A3] 입력 정의역. 상한이 없으면 None
X_MIN = <D-zz: 최소>
X_MAX = <D-zz: 최대 또는 None>
TYPICAL_HI = <일상적인 입력의 상한: 표본용, 결정 아님>
SAMPLE_HI = X_MAX if X_MAX is not None else <상한 없음일 때 표본 상한: 매우 큰 값 (C-A1)>
# [사람 결정 C-A3 · C-A5] 매개변수 범위. 테스트에서 한 값으로 고정하지 않는다
P_MIN = <D-ww: 최소>
P_MAX = <D-ww: 최대>
P_DEFAULT = <D-ww: 기본값 (경계값 표에서만 사용)>


def observe(result) -> tuple:
    """결과를 원시 값의 튜플로 푼다. 출력의 모든 필드를 넣는다. 값 객체의 == 에 기대지 않는다 (C-A4)."""
    return <(result.<field_1>, result.<field_2>)>


def oracle(x, p) -> tuple:
    """<D-xx> 의 규칙을 구현과 다른 형태로 옮긴 느린 정답 (B-6).

    - <module> 을 import 하지 않는다
    - 매개변수 p 를 그대로 쓴다 (기본값을 박지 않는다, C-A5)
    - observe() 와 같은 모양의 튜플을 돌려준다
    """
    raise NotImplementedError("<D-xx 의 규칙 표를 데이터 · 집합 · 닫힌 식 등 구현과 다른 형태로 옮긴다>")


def boundaries_for(p) -> list:
    """경계가 매개변수에 따라 바뀌면 p 로 다시 계산한다 (03 · 06). 바뀌지 않으면 그대로 둔다."""
    return BOUNDARIES


def xs_between(lo, hi):
    """lo ~ hi 의 입력을 해상도 EPS 격자 위에서 뽑는다."""
    return st.integers(min_value=0, max_value=int((hi - lo) // EPS)).map(lambda k: lo + k * EPS)


def in_domain(x) -> bool:
    return X_MIN <= x and (X_MAX is None or x <= X_MAX)


# ── 01: 경계값 표 (경계 ± 최소 해상도) ───────────────────────────────────

B = BOUNDARIES[0]  # 경계마다 같은 표를 하나씩 둔다


@pytest.mark.parametrize(
    "x, expected",
    [
        (B + BIG_STEP, <위쪽 규칙의 기대 출력 튜플>),
        (B, <D-xx: 정확히 경계의 기대 출력 (어느 쪽인가)>),
        (B - BIG_STEP, <아래쪽 규칙의 기대 출력 튜플>),
        (B - EPS, <아래쪽 규칙의 기대 출력 튜플>),  # 반올림 · 절삭 · 허용 오차를 잡는 행 (C-A2)
        (B + EPS, <위쪽 규칙의 기대 출력 튜플>),
        (X_MIN, <정의역 최소에서의 기대 출력 튜플>),
    ],
    ids=["B+big", "B", "B-big", "B-eps", "B+eps", "min"],
)
def test_boundary_table(x, expected):
    """<D-xx> 경계 소속, <D-yy> 해상도. 기대값은 결정에서 옮겨 적는다 (구현 상수를 import 하지 않는다)."""
    assert observe(<function>(x, <param>=P_DEFAULT)) == expected


# ── 02 · 03 · 06: 정의역 전체 + 경계 근처 + 매개변수 가변 ────────────────────

params = st.integers(min_value=P_MIN, max_value=P_MAX)  # 매개변수 타입이 다르면 같은 범위의 그 타입 생성기로


@st.composite
def scenarios(draw):
    p = draw(params)
    # 오프셋: 정확히 -1 · 0 · +1 EPS 를 따로 뽑는다. 넓은 범위에서 고르게 뽑으면 바로 옆 값이 거의 나오지 않는다
    offset = draw(st.one_of(st.sampled_from([-1, 0, 1]), st.integers(min_value=-1_000, max_value=1_000)))
    near = draw(st.sampled_from(boundaries_for(p))) + offset * EPS
    branches = [
        xs_between(X_MIN, TYPICAL_HI),  # 일상 범위
        xs_between(X_MIN, SAMPLE_HI),  # 정의역 전체, 큰 값 포함 (C-A1)
    ]
    if in_domain(near):
        branches.append(st.just(near))  # 경계 ± 1000 EPS (C-A2). 걸러 내지 않고 만들 때 맞춘다
    return draw(st.one_of(*branches)), p


@settings(max_examples=300)  # 시간을 맞추려고 줄이지 않는다 (C-A8, 사람 승인 필요)
@given(scenarios())
def test_matches_oracle_over_whole_domain(sc):
    """<D-xx>, <D-zz>, <D-ww>: 모든 입력 · 모든 매개변수에서 출력 전체가 오라클과 같다."""
    x, p = sc
    assert observe(<function>(x, <param>=p)) == oracle(x, p)


def test_parameter_range_is_not_a_single_value():
    """공허한 참 방지: 매개변수 범위가 한 값이면 C-A5 가 검사되지 않는다."""
    assert P_MIN < P_MAX
