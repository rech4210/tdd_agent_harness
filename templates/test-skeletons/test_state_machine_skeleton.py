"""<Entity> 상태 기계: 상태 × 동작 전수 표, 금지 전이의 상태 불변, 가드 경계 ± 최소 해상도.

guards:      <FR-###>, <INV-xx> (전이 표), <D-xx> (가드 경계 소속), <D-yy> (해상도)
technique:   04 전수 상태 표, 01 경계값 표, 03 경계 근처 생성기
checklist:   B-3, B-4, C-A2, C-A3
decisions:   <INV-xx> (전이 표), <D-xx> (가드: 정확히 경계에서 허용하는가), <D-yy> (해상도)
not-covered: <생성 결과의 속성 보존은 값 객체 골격 · 07 로>

골격 사용법: `<...>` 자리표시자를 모두 바꾼다. 남아 있으면 SyntaxError 로 즉시 실패한다.
"""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from <package>.<module> import <Entity>, <IllegalTransition>, <Status>

S = <Status>

# ── 전이 표 (명세 그대로) ─────────────────────────────────────────────────
# [사람 결정 H1] <INV-xx> 의 표를 옮긴다. 여기 없는 칸은 전부 금지다
ACTIONS = ["<action_a>", "<action_b>", "<action_c>"]
ALLOWED = {
    (S.<STATE_A>, "<action_a>"): S.<STATE_B>,
    (S.<STATE_B>, "<action_b>"): S.<STATE_C>,
}
# 각 상태로 가는 허용 경로 (공개 동작만. 내부 setter 를 쓰지 않는다)
PATH_TO = {
    S.<STATE_A>: [],
    S.<STATE_B>: ["<action_a>"],
    S.<STATE_C>: ["<action_a>", "<action_b>"],
}
STATE_COUNT = <명세의 상태 수>  # 숫자를 직접 적는다. 상태가 늘면 자기 검사가 먼저 깨진다
ACTION_COUNT = <명세의 동작 수>

# ── 가드 (시각 · 수량 등 값에 걸린 전이 조건) ──────────────────────────────
# [사람 결정 C-A3] 해상도와 경계 소속
EPS = <D-yy: 가드 값의 최소 해상도>
GUARD_ACTION = "<guarded_action>"
GUARD_FROM = S.<GUARD_FROM_STATE>
PASSES_ALL_GUARDS = <모든 가드를 통과하는 값 (전수 표에서 사용)>


def guard_threshold(entity):
    """가드의 경계값. 엔티티가 가진 값에서 계산한다."""
    return entity.<threshold_attr>


def guard_allows(offset_steps: int) -> bool:
    """[사람 결정 <D-xx>] 경계 + offset_steps × EPS 에서 전이를 허용하는가. 예: 정확히 경계부터 허용이면 >= 0."""
    return offset_steps >= 0


def new_entity():
    return <Entity>.<factory>(<생성 인자>)


def apply(entity, action: str, at=PASSES_ALL_GUARDS) -> None:
    getattr(entity, action)(at=at)  # 동작마다 인자가 다르면 여기서 분기한다


def reach(status):
    e = new_entity()
    for action in PATH_TO[status]:
        apply(e, action)
    assert e.status is status
    return e


ALL_CELLS = [(s, a) for s in S for a in ACTIONS]


# ── 04: 전수 표 ──────────────────────────────────────────────────────────


def test_matrix_covers_every_cell():
    """표 자체를 검사한다. 상태나 동작이 추가되면 여기서 먼저 깨져 표를 고치게 만든다."""
    assert set(PATH_TO) == set(S)
    assert len(ALL_CELLS) == STATE_COUNT * ACTION_COUNT
    assert all(a in ACTIONS for (_, a) in ALLOWED)


@pytest.mark.parametrize("status, action", ALL_CELLS, ids=lambda x: getattr(x, "name", x))
def test_transition_matrix(status, action):
    """<INV-xx>: 허용 칸은 결과 상태까지, 금지 칸은 예외와 상태 불변까지."""
    e = reach(status)
    if (status, action) in ALLOWED:
        apply(e, action)
        assert e.status is ALLOWED[(status, action)]
    else:
        with pytest.raises(<IllegalTransition>):
            apply(e, action)
        assert e.status is status, "금지된 전이는 상태를 바꾸면 안 된다"


@given(st.lists(st.sampled_from(ACTIONS), max_size=20))
def test_any_action_sequence_follows_model(actions):
    """<INV-xx>: 경로 의존 버그. 무작위 동작 순서를 표로 걷는 모델과 매 단계 비교한다."""
    e, model = new_entity(), next(s for s, path in PATH_TO.items() if path == [])
    for a in actions:
        if (model, a) in ALLOWED:
            apply(e, a)
            model = ALLOWED[(model, a)]
        else:
            with pytest.raises(<IllegalTransition>):
                apply(e, a)
        assert e.status is model


# ── 01 · 03: 가드 경계 ± 최소 해상도 ──────────────────────────────────────


@pytest.mark.parametrize("steps", [-1, 0, 1], ids=["-eps", "exact", "+eps"])
def test_guard_boundary_table(steps):
    """<D-xx> 경계 소속, <D-yy> 해상도: 경계 바로 앞 · 정확히 · 바로 뒤."""
    e = reach(GUARD_FROM)
    at = guard_threshold(e) + steps * EPS
    if guard_allows(steps):
        apply(e, GUARD_ACTION, at=at)
        assert e.status is ALLOWED[(GUARD_FROM, GUARD_ACTION)]
    else:
        with pytest.raises(<IllegalTransition>):
            apply(e, GUARD_ACTION, at=at)
        assert e.status is GUARD_FROM


near_steps = st.one_of(  # -1 · 0 · +1 은 따로 뽑는다. 넓은 범위에서 고르게 뽑으면 바로 옆 값이 거의 나오지 않는다
    st.sampled_from([-1, 0, 1]), st.integers(min_value=-1_000_000, max_value=1_000_000)
)


@given(near_steps)
def test_guard_matches_rule_near_boundary(steps):
    """<D-xx>: 경계 근처 어디서든 규칙대로. 허용 오차 · 절삭 비교를 막는다 (C-A2). 거부되면 상태 불변.

    경계값이 생성 인자에서 계산되면, 생성 인자도 정밀한(격자에서 벗어난) 값으로 바꿔 가며 뽑는다.
    딱 떨어지는 생성 값만 쓰면 '거친 단위로 잘라 비교하는' 구현이 통과한다.
    """
    e = reach(GUARD_FROM)
    at = guard_threshold(e) + steps * EPS
    if guard_allows(steps):
        apply(e, GUARD_ACTION, at=at)
        assert e.status is ALLOWED[(GUARD_FROM, GUARD_ACTION)]
    else:
        with pytest.raises(<IllegalTransition>):
            apply(e, GUARD_ACTION, at=at)
        assert e.status is GUARD_FROM
