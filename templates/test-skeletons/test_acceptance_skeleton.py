"""<업무 기능> 인수 시나리오의 단계 정의 (pytest-bdd).

guards:      <FR-###>, <INV-xx>, <D-xx>          # .feature 머리말과 같게 둔다
technique:   인수 시나리오 (Gherkin, 업무 언어). 단계는 유스케이스를 직접 부른다 (HTTP · 화면 아님)
checklist:   B-4, B-5, B-8
decisions:   <D-xx>, <D-yy>
not-covered: <표현 계층 (판단 없는 얇은 층) 은 탐색 QA 로>

골격 사용법: `<...>` 자리표시자를 모두 바꾼다. 남아 있으면 SyntaxError 로 즉시 실패한다.
단계 문장은 .feature 의 문장과 글자 그대로 같아야 한다. 단계 안에서 판단을 하지 않는다 (판단은 유스케이스의 일).
포트는 Fake 다. Fake 와 실제의 일치는 계약 골격이 맡는다.
"""

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from <package>.<errors_module> import <DomainError>
from <package>.<usecases_module> import <Command>, <UseCase>
from <tests_package>.fakes import <FakeClock>, <FakeRepository>, <SpyOutbound>

scenarios("<features 디렉터리>/<기능>.feature")


@pytest.fixture
def world():
    """시나리오 하나의 상태."""
    repo, spy = <FakeRepository>(), <SpyOutbound>()
    usecase = <UseCase>(<repo_port>=repo, <outbound_port>=spy, <clock_port>=<FakeClock>(<시작 시각>))
    return {"repo": repo, "spy": spy, "usecase": usecase, "result": None, "error": None, "before": None}


def run(world, command) -> None:
    """유스케이스를 부르고 결과나 도메인 예외를 기록한다. 도메인 예외 밖의 예외는 그대로 올린다."""
    world["before"] = world["repo"].<snapshot>()
    try:
        world["result"] = world["usecase"].<execute>(command)
    except <DomainError> as e:
        world["error"] = e


# ── Given ──


@given("<공통 전제 문장>")
def common_premise(world):
    world["repo"].<add>(<전제 데이터>)


@given(parsers.parse("<기준 값 문장> {base} 이다"))
def base_value(world, base):
    world["base"] = <base 를 도메인 값으로 바꾸는 식>


# ── When ──


@when("<행위자> 가 <행위> 한다")
def act(world):
    run(world, <Command>(<인자>))


@when(parsers.parse("<행위자> 가 {value} 에 <행위> 한다"))
def act_with(world, value):
    run(world, <Command>(<value 를 쓰는 인자>))


@when("같은 <사건> 이 두 번 도착한다")
def same_event_twice(world):
    command = <Command>(<같은 사건 키를 가진 인자>)
    run(world, command)
    world["after_first"] = world["repo"].<snapshot>()
    run(world, command)


# ── Then ──


@then(parsers.parse('결과는 "{expected}" 이다'))
def result_is(world, expected):
    """경계 표의 행마다 업무상 결과를 비교한다. 거절도 업무상 결과의 하나다."""
    got = "<거절을 뜻하는 업무 말>" if world["error"] else <world["result"] 의 업무상 결과를 문자열로>
    assert got == expected


@then("<관찰 가능한 결과> 는 한 번 처리한 것과 같다")
def same_as_once(world):
    assert world["repo"].<snapshot>() == world["after_first"]


@then("요청은 거절된다")
def rejected(world):
    assert isinstance(world["error"], <DomainError>)


@then("아무것도 바뀌지 않는다")
def nothing_changed(world):
    assert world["repo"].<snapshot>() == world["before"]


@then(parsers.parse("<바깥으로 나가는 일> 은 {count:d}번 일어난다"))
def outbound_count(world, count):
    assert len(world["spy"].calls) == count


@then("<바깥으로 나가는 일> 은 일어나지 않는다")
def no_outbound(world):
    assert world["spy"].calls == []
