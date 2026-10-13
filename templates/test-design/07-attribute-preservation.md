# 07 생성 결과 속성 보존

| 항목 | 내용 |
|:---|:---|
| 체크리스트 | C-A7, C-A4 |
| 함께 쓰는 것 | 05 (수용 가지의 값 보존), 02 (파생 필드는 오라클로) |

## 목적

생성자 · 팩토리가 만든 객체의 **모든 공개 속성**이 받은 값과 정확히 같은지 확인한다. 상태나 계산 결과만 보는 테스트는 "받은 값을 저장하지 않는" 구현을 통과시킨다.

## 언제 쓰나

- 값을 저장하는 생성자 · 팩토리 · 매핑 함수
- 다른 테스트가 생성 결과의 일부(상태, 파생 값)만 검사할 때
- 변이 테스트에서 "필드 대입을 없앤" 변이가 살아남았을 때

## 무엇을 막나

| 실패 | 일반화된 사례 |
|:---|:---|
| 받은 값을 저장하지 않음 | 팩토리가 여러 필드를 "없음"으로 저장해도, 상태와 파생 시각만 검사한 테스트가 모두 통과했다. 변이 테스트의 생존 변이로 늦게 발견됐다 |
| 값을 바꿔 저장 (절삭 · 정규화) | 정밀도를 자르는 생성자. 받은 값과 정확히 비교하면 잡힌다 |
| 필드 뒤바뀜 | 두 필드에 같은 값을 넣는 테스트는 못 잡는다. 서로 구별되는 값을 넣는다 |

## 최소 구조

```
입력 = 필드마다 생성기 (기본값과 다르고, 필드끼리 서로 구별되는 값)
obj  = 생성(입력)
for 필드 in 공개 속성 전체:
    obj.필드 == 입력.필드              # 원시 값으로, 정확히
아직 없는 값 (나중 단계에서 채워짐)   → "없음"임을 검사
파생 필드                              → 명세식(입력) 과 같음 (02)

필드 목록 자기 검사: 타입이 선언한 공개 필드 집합 == 이 테스트가 검사하는 필드 집합
                     (필드가 늘면 여기서 먼저 깨진다)
```

## 게으른 구현 예

| 게으른 구현 | 이 테스트가 잡는 방법 |
|:---|:---|
| 일부 필드를 "없음" · 상수로 저장 | 모든 필드를 받은 값과 비교 |
| 두 필드를 바꿔 저장 | 필드마다 구별되는 값 |
| 정밀도를 잘라 저장 | 정밀한 생성기 + 정확한 비교 |

## 흔한 실수

| 실수 | 결과 |
|:---|:---|
| 검사할 필드를 손으로 나열하고 자기 검사가 없음 | 새 필드가 검사에서 빠진다 |
| 모든 필드에 같은 값 · 같은 생성기 | 뒤바뀜을 못 잡는다 |
| 값 객체의 `==`로 비교 | `==`가 항상 참이면 공허하다 (C-A4). 원시 값으로 |
| 고정 예시 하나만 | 특정 값에서만 저장하는 구현은 드물지만, 절삭은 정밀한 무작위 값에서만 드러난다 |

## Python 참조 구현

```python
"""<대상> 생성 결과 속성 보존. 지키는 결정: D-xx (생성 시 받은 값을 그대로 가진다)."""
from dataclasses import fields
from datetime import datetime, timezone

from hypothesis import given
from hypothesis import strategies as st

from mypkg.members import Member  # 예시

ids = st.text(min_size=1, max_size=20).map(lambda s: "id-" + s)  # 필드마다 구별되는 값
names = st.text(min_size=1, max_size=20).map(lambda s: "nm-" + s)
instants = st.datetimes(min_value=datetime(2000, 1, 1), timezones=st.just(timezone.utc))
quotas = st.integers(min_value=1, max_value=10**9)


@given(ids, names, instants, quotas)
def test_register_keeps_every_given_value(member_id, name, joined_at, quota):
    m = Member.register(member_id=member_id, name=name, joined_at=joined_at, quota=quota)
    assert m.member_id == member_id
    assert m.name == name
    assert m.joined_at == joined_at  # 정밀도 그대로
    assert m.quota == quota
    assert m.closed_at is None  # 아직 없는 값


def test_checked_fields_are_all_public_fields():
    """필드가 늘면 여기서 먼저 깨진다. 위 테스트에 새 필드 검사를 더하게 만든다."""
    assert {f.name for f in fields(Member)} == {"member_id", "name", "joined_at", "quota", "closed_at"}
```
