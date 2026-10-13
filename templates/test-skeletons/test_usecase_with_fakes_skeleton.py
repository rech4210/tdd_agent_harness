"""<UseCase> 유스케이스: 포트를 통한 조립, 부수효과의 횟수, 멱등성.

guards:      <FR-###>, <INV-xx> (멱등성: 같은 사건 두 번 → 효과 한 번), <D-xx>
technique:   Fake 포트 + Spy (부수효과를 횟수로), 멱등성 성질, 09 공허한 참 방지 (양성 대조)
checklist:   B-1, B-4, B-5, B-6
decisions:   <D-xx> (중복 판정 키: 무엇이 "같은 사건"인가), <D-yy> (두 번째 처리의 결과: 같은 결과 / 무시 / 오류)
not-covered: <실제 어댑터와 Fake 의 일치는 계약 골격 (test_contract_skeleton) 이 맡는다>

골격 사용법: `<...>` 자리표시자를 모두 바꾼다. 남아 있으면 SyntaxError 로 즉시 실패한다.
유스케이스는 포트만 안다. 시각 · 식별자 · 외부 호출은 모두 포트로 들어온다 (구조 규칙 골격이 지킨다).
Fake 는 계약 테스트로 실제 어댑터와 묶여 있어야 이 테스트의 초록불이 의미를 가진다.
"""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from <package>.<errors_module> import <DomainError>
from <package>.<usecases_module> import <Command>, <UseCase>
from <tests_package>.fakes import <FakeClock>, <FakeRepository>


class SpyOutbound:
    """바깥으로 나가는 포트(<알림 · 외부 호출>)의 Spy. 호출을 모두 기록한다. 프로젝트의 fakes 로 옮긴다."""

    def __init__(self) -> None:
        self.calls: list = []

    def <send>(self, message) -> None:
        self.calls.append(message)


@pytest.fixture
def world():
    repo = <FakeRepository>()
    spy = SpyOutbound()
    clock = <FakeClock>(<시작 시각>)
    usecase = <UseCase>(<repo_port>=repo, <outbound_port>=spy, <clock_port>=clock)
    return repo, spy, clock, usecase


def valid_command(**overrides):
    values = {"<event_key>": "<사건 키>", "<field_1>": <유효한 값>}
    values.update(overrides)
    return <Command>(**values)


# ── 정상 흐름: 상태 변화 + 부수효과는 정확히 한 번 ───────────────────────────


def test_success_changes_state_and_emits_exactly_once(world):
    """<FR-###>: 결과 상태와 부수효과를 함께 본다. 횟수로 검사한다 (B-5)."""
    repo, spy, _, usecase = world
    result = usecase.<execute>(valid_command())
    assert repo.<get>(result.<id>).<status> == <기대 상태>
    assert len(spy.calls) == 1
    assert spy.calls[0].<field> == <기대 값>  # 양성 대조: 무엇이 나갔는지까지 (09)


# ── 거부 흐름: 예외 + 아무것도 바뀌지 않음 ───────────────────────────────────


def test_rejected_command_changes_nothing(world):
    """<FR-###>, <D-xx>: 거부되면 예외, 저장소 불변, 부수효과 0회 (B-4, B-5)."""
    repo, spy, _, usecase = world
    before = repo.<snapshot>()
    with pytest.raises(<DomainError>):
        usecase.<execute>(valid_command(<field_1>=<거부되는 값>))
    assert repo.<snapshot>() == before
    assert spy.calls == []


# ── 멱등성: 같은 사건 두 번 → 효과 한 번 ─────────────────────────────────────


def test_same_event_twice_has_effect_once(world):
    """<INV-xx>: 같은 사건이 두 번 도착해도 상태 변화와 부수효과는 한 번이다. 두 번째의 결과는 <D-yy>."""
    repo, spy, _, usecase = world
    first = usecase.<execute>(valid_command())
    state_after_first = repo.<snapshot>()
    second = usecase.<execute>(valid_command())
    assert repo.<snapshot>() == state_after_first
    assert len(spy.calls) == 1
    assert second == first  # <D-yy>: 두 번째가 같은 결과를 돌려준다면. 오류라면 pytest.raises 로 바꾼다


@given(st.lists(st.sampled_from(["k-1", "k-2", "k-3"]), min_size=1, max_size=20))
def test_effects_count_distinct_events_only(keys):
    """<INV-xx>: 어떤 순서 · 어떤 중복으로 와도 부수효과 수 = 서로 다른 사건 수."""
    repo = <FakeRepository>()
    spy = SpyOutbound()
    usecase = <UseCase>(<repo_port>=repo, <outbound_port>=spy, <clock_port>=<FakeClock>(<시작 시각>))
    for k in keys:
        usecase.<execute>(valid_command(<event_key>=k))
    assert len(spy.calls) == len(set(keys))
