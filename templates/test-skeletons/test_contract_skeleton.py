"""<Port> 계약: 같은 테스트 본문을 Fake 와 실제 어댑터에 돌린다.

guards:      <FR-###>, <D-xx> (포트 의미: 복사 반환 · 원자적 거부 · 경계 포함 여부), <INV-xx>
technique:   08 계약 테스트 (Fake 와 실제), 07 왕복 속성 보존, 09 공허한 참 방지 (조회의 완전성)
checklist:   B-4, B-6, B-7
decisions:   <D-xx> (포트 의미, H4)
not-covered: <동시성 (구조적 보증은 H5 에서 확인)>

골격 사용법: `<...>` 자리표시자를 모두 바꾼다. 남아 있으면 SyntaxError 로 즉시 실패한다.
게이트가 다르면 파일을 나눈다: 계약 본문 + Fake 하위 = 빠른 게이트, 실제 하위 = 통합 게이트.
Fake 는 테스트의 일부다. 테스트 작성자가 고치고 H3 에서 승인한다 (구현자는 고치지 않는다).
"""

import os

import pytest

from <package>.<ports_module> import <Conflict>, <Item>, <NotFound>
from <package>.<real_adapter_module> import <RealAdapter>
from <tests_package>.fakes import <FakeAdapter>

# [사람 결정 H4] 실제 어댑터가 쓰는 연결 정보의 환경 변수 이름
REAL_ENV = "<ENV_VAR_NAME>"


def make(key: str, **overrides) -> "<Item>":
    """모든 필드에 서로 구별되는 값을 넣는다 (뒤바뀐 매핑을 잡는다)."""
    values = {"<key_field>": key, "<field_1>": <구별되는 값 1>, "<field_2>": <구별되는 값 2>}
    values.update(overrides)
    return <Item>(**values)


def observe(item) -> tuple:
    """비교는 원시 값으로. 모든 공개 필드를 넣는다."""
    return <(item.<key_field>, item.<field_1>, item.<field_2>)>


class PortContract:
    """하위 클래스는 port 픽스처만 제공한다. 계약 본문은 포트의 공개 메서드로만 관찰한다."""

    @pytest.fixture
    def port(self):
        raise NotImplementedError("하위 클래스가 제공한다")

    # ── 저장 · 조회 ──

    def test_round_trip_keeps_every_field(self, port):
        """<D-xx>: 저장한 것을 그대로 읽는다. 실제 어댑터의 매핑 누락을 잡는다."""
        item = make("k-1")
        port.<add>(item)
        assert observe(port.<get>("k-1")) == observe(item)

    def test_unknown_key_raises(self, port):
        with pytest.raises(<NotFound>):
            port.<get>("missing")

    def test_returned_object_is_a_copy(self, port):
        """<D-xx>: 저장하지 않고 바꾼 것은 저장되지 않는다. 객체를 공유하는 Fake 를 잡는다."""
        port.<add>(make("k-1"))
        got = port.<get>("k-1")
        got.<field_1> = <다른 값>
        assert observe(port.<get>("k-1")) == observe(make("k-1"))

    # ── 거부의 원자성 ──

    def test_rejected_write_leaves_no_trace(self, port):
        """<D-xx>: 거부된 쓰기는 흔적을 남기지 않는다 (B-4)."""
        port.<add>(make("k-1"))
        with pytest.raises(<Conflict>):
            port.<add>(make("k-1", <field_1>=<다른 값>))
        assert observe(port.<get>("k-1")) == observe(make("k-1"))

    # ── 조회 질의: 경계 포함 여부와 완전성 ──

    def test_query_includes_exact_boundary_and_excludes_just_outside(self, port):
        """<D-xx>: 정확히 경계는 <포함 / 제외>, 경계 바로 밖은 반대. Fake 와 실제의 경계 차이를 잡는다."""
        port.<add>(make("inside", <boundary_field>=<정확히 경계 값>))
        port.<add>(make("outside", <boundary_field>=<경계 바로 밖 값 (최소 해상도만큼)>))
        got = sorted(i.<key_field> for i in port.<query>(<경계 조건>))
        assert got == <D-xx: ["inside"] 또는 []>

    def test_query_returns_all_and_only_matching(self, port):
        """완전성: 맞는 것은 모두, 맞지 않는 것은 하나도. 빈 결과를 돌려주는 구현을 잡는다 (09)."""
        for key, matches in [("a", True), ("b", False), ("c", True)]:
            port.<add>(make(key, <match_field>=<matches 에 따라 맞는 값 / 맞지 않는 값>))
        got = sorted(i.<key_field> for i in port.<query>(<조건>))
        assert got == ["a", "c"]


class TestFakeContract(PortContract):  # 빠른 게이트
    @pytest.fixture
    def port(self):
        return <FakeAdapter>()


@pytest.mark.integration  # 통합 게이트. 이 게이트를 돌릴지는 게이트 실행기가 정하고 판정 보고에 SKIP 으로 드러낸다
class TestRealContract(PortContract):
    @pytest.fixture
    def port(self):
        url = os.environ.get(REAL_ENV)
        if not url:
            # skip 하지 않는다: 한 번도 돌지 않은 계약이 초록불로 보이면 안 된다 (B-7)
            pytest.fail(f"{REAL_ENV} 가 없다. 실제 어댑터 계약을 검사할 수 없다")
        adapter = <RealAdapter>.<connect>(url)
        adapter.<reset_for_test>()  # 테스트마다 깨끗한 상태
        yield adapter
        adapter.<close>()
