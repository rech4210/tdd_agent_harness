# 08 계약 테스트 (Fake와 실제 어댑터에 같은 테스트)

| 항목 | 내용 |
|:---|:---|
| 체크리스트 | B-7, B-6 |
| 사람 구간 | H4 (포트 · 계약 설계). Fake 변경은 테스트 작성자의 일 |

## 목적

**같은 테스트 본문**을 Fake 구현과 실제 어댑터 양쪽에 돌려, 포트의 의미가 둘에서 같음을 확인한다. 유스케이스 테스트는 Fake 위에서 돈다. Fake가 실제와 다르면 유스케이스 테스트 전체가 거짓 초록불이 된다.

## 언제 쓰나

- 포트(저장소, 외부 서비스, 시계, 큐)에 Fake와 실제 구현이 함께 있을 때
- 포트 메서드에 원자성 · 멱등성 · 복사 의미 · 경계 포함 여부 같은 **의미**가 있을 때
- 포트를 바꿀 때 (H4): 계약을 먼저 바꾸고, Fake를 맞추고, 실제 어댑터가 따라온다

## 무엇을 막나

| 실패 | 일반적인 모습 |
|:---|:---|
| Fake가 객체를 공유 | 저장 없이 바꾼 값이 Fake에서는 반영되고 실제에서는 반영되지 않는다 |
| Fake의 원자성 부재 | 실제는 거부하면서 흔적을 남기지 않는데, Fake는 일부를 남긴다 (또는 그 반대) |
| 경계 포함 여부 차이 | "정확히 경계"를 Fake와 실제가 다르게 처리한다 |
| 실제 어댑터의 매핑 누락 | 행 ↔ 객체 변환에서 필드가 빠진다. 사람이 매핑 코드를 읽지 않아도 계약 테스트가 잡는다 |
| 환경이 없어 조용히 skip | 실제 쪽 계약이 한 번도 돌지 않았는데 초록불 |

## 최소 구조

```
계약 본문 (추상, 공통):
  저장 → 조회 왕복: 모든 필드 보존 (07)
  없는 키 → 지정된 예외
  반환은 복사본: 저장 없이 바꾼 것은 저장되지 않음
  거부(충돌) 시 원자적: 흔적 없음
  경계 포함 · 제외: 정확히 경계 값에서 양쪽 행 (01)
  조회 질의: 포함돼야 할 것과 빠져야 할 것을 함께 (09 완전성)

Fake 하위   : 픽스처만 제공 → 빠른 게이트 (단위 · 유스케이스와 함께)
실제 하위   : 픽스처만 제공 → 통합 게이트 (외부 자원 필요)
  환경 변수 · 자원이 없으면: 테스트는 skip 하지 않고 **실패**한다
  "이번 판정에서 통합 게이트를 돌리지 않는다"는 결정은 게이트 실행기가 SKIP 으로 판정 보고에 드러낸다
```

## 게으른 구현 예

| 게으른 구현 | 이 계약이 잡는 방법 |
|:---|:---|
| 저장 객체를 그대로 반환하는 Fake | 반환은 복사본 테스트 |
| 충돌을 검사하기 전에 일부를 쓰는 어댑터 | 거부 후 흔적 없음 테스트 |
| 실제 어댑터가 필드 하나를 저장하지 않음 | 왕복 보존 테스트 (실제 하위에서) |

## 흔한 실수

| 실수 | 결과 |
|:---|:---|
| 계약을 Fake에만 돌림 | Fake끼리 일치할 뿐이다 |
| Fake 전용 · 실제 전용 테스트를 계약 본문에 섞음 | 계약이 아니라 구현 테스트가 된다. 하위 클래스에 둔다 |
| 실제 쪽이 환경 없음으로 skip | 한 번도 검사하지 않은 계약이 통과로 보인다 |
| 구현자가 Fake를 고침 | Fake는 테스트의 일부다. 테스트 작성자가 바꾸고 H3에서 승인 |
| 계약 본문이 구현의 내부 상태를 봄 | 실제 어댑터에서 쓸 수 없다. 포트의 공개 메서드로만 관찰 |

## Python 참조 구현

```python
"""<포트> 계약. 지키는 결정: D-xx (포트 의미: 복사 반환, 원자적 거부). 하위 클래스는 repo 픽스처만 제공한다."""
import os

import pytest

from mypkg.ports import Conflict, Item, NotFound  # 예시
from mypkg.adapters.memory import FakeItemRepository  # 예시
from mypkg.adapters.store import StoreItemRepository  # 예시


class ItemRepositoryContract:
    @pytest.fixture
    def repo(self):
        raise NotImplementedError("하위 클래스가 제공한다")

    def test_added_item_can_be_read_back(self, repo):
        repo.add(Item(id="i-1", qty=3))
        got = repo.get("i-1")
        assert (got.id, got.qty) == ("i-1", 3)

    def test_unknown_id_raises(self, repo):
        with pytest.raises(NotFound):
            repo.get("nope")

    def test_returned_object_is_a_copy(self, repo):
        repo.add(Item(id="i-1", qty=3))
        repo.get("i-1").qty = 99  # 저장하지 않고 바꾼다
        assert repo.get("i-1").qty == 3

    def test_conflicting_add_leaves_no_trace(self, repo):
        repo.add(Item(id="i-1", qty=3))
        with pytest.raises(Conflict):
            repo.add(Item(id="i-1", qty=5))
        assert repo.get("i-1").qty == 3


class TestFakeItemRepository(ItemRepositoryContract):  # 빠른 게이트
    @pytest.fixture
    def repo(self):
        return FakeItemRepository()


@pytest.mark.integration  # 통합 게이트
class TestStoreItemRepository(ItemRepositoryContract):
    @pytest.fixture
    def repo(self):
        url = os.environ.get("STORE_URL")  # 예시 환경 변수
        if not url:
            pytest.fail("STORE_URL 이 없다. skip 하지 않는다: SKIP 여부는 게이트 실행기가 정한다")
        return StoreItemRepository.connect(url, fresh=True)
```
