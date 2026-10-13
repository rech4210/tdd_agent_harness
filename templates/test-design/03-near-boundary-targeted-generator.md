# 03 경계 근처 표적 생성기

| 항목 | 내용 |
|:---|:---|
| 체크리스트 | C-A2 |
| 함께 쓰는 것 | 02 독립 오라클 (표적 입력의 정답을 오라클로), 05 거부 영역 |

## 목적

무작위 생성기가 거의 닿지 않는 구석을 **일부러** 뽑는다. 정의역이 넓고 정밀할수록 "정확히 경계", "경계 ± 최소 해상도", "한 하위 필드만 0이 아님" 같은 입력이 나올 확률은 0에 가깝다. 예시 수를 늘리는 것으로는 해결되지 않는다.

## 언제 쓰나

- 경계 규칙이 있고 입력이 정밀한 타입일 때 (시각, 실수, 큰 정수)
- 반열린 구간 · 맞닿음 판정이 있을 때
- 검증 조건이 입력의 **일부 필드**에만 걸릴 때 (예: 큰 단위는 맞고 작은 하위 단위만 0이 아니어야 하는 조건)
- 변이 테스트에서 비교 연산자 변이(`<` → `<=`)가 살아남았을 때

## 무엇을 막나

| 실패 | 일반화된 사례 |
|:---|:---|
| 경계 근처 허용 오차 · 절삭 | 넓은 무작위 입력만 뽑은 성질 테스트가, 경계에 작은 오차를 둔 구현을 통과시켰다 |
| 맞닿음 판정의 연산자 반전 | 정밀한 무작위 입력에서는 두 구간이 정확히 맞닿는 경우가 거의 나오지 않아 `<` → `<=` 변이가 살아남았다. 맞닿음을 직접 만드는 가지를 넣자 잡혔다 |
| 하위 필드 검증 누락 | 하위 필드(작은 단위)를 검증하지 않는 구현이 무작위 조합 테스트를 통과했다. 그 필드만 위반하는 표적 테스트를 넣자 실패했다 |

## 최소 구조

```
gen = one_of(
  넓은 생성기,                                    # 정의역 전체 (02)
  경계 + k × EPS,   k ∈ {-1, 0, +1} ∪ [-K, K]     # 근처. EPS = 최소 해상도, K 는 작은 수. -1 · 0 · +1 은 따로 뽑는다
  경계를 정확히 만드는 구성 (offset 0 포함)        # 맞닿음: 한 구간의 끝 = 다른 구간의 시작
  표적: 한 하위 필드만 위반, 나머지 필드는 정상    # 05 와 결합
)
경계가 매개변수에 따라 바뀌면 (06): 생성기 안에서 매개변수로 경계를 다시 계산한다
정답은 오라클(02)로 판정한다. 표적 입력에서만 따로 기대값을 손으로 적지 않는다
```

## 게으른 구현 예

| 게으른 구현 | 이 생성기가 잡는 방법 |
|:---|:---|
| `at >= B - 작은 오차` | 경계 ± K × EPS 가지가 오차 구간 안의 입력을 뽑는다 |
| 맞닿음을 겹침으로 판정 (`<=`) | offset 0 입력이 직접 나온다 |
| 하위 필드를 검증하지 않음 | 표적 가지는 그 필드만 위반하므로 반드시 거부돼야 한다 |

## 흔한 실수

| 실수 | 결과 |
|:---|:---|
| "예시 수백 개면 충분하다" | 정밀한 정의역에서 경계에 닿을 확률은 예시 수와 거의 무관하다 |
| 오프셋 단위를 큰 단위로 | 큰 단위 아래의 오차 · 절삭이 숨는다. 오프셋은 최소 해상도 단위로 |
| 오프셋을 [-K, K]에서 고르게만 뽑음 | 바로 옆 값(±1 EPS)이 나올 확률이 1/(2K+1)이다. 실행 확인에서 경계를 1 EPS 옮긴 후보가 300개 예시에서 살아남았고, -1 · 0 · +1을 따로 뽑자 잡혔다 |
| 거부 영역을 `filter(not valid)`로 뽑음 | 특정 조건만 깨는 입력이 거의 안 나온다. 표적 가지를 직접 만든다 |
| 경계를 고정 상수로 두고 매개변수를 바꿈 | 매개변수가 바뀌면 "경계 근처"가 경계 근처가 아니다 |
| 표적 생성 후 대부분을 걸러 냄 | 실효 예시 수가 줄어든다. 걸러 내지 말고 만들 때 맞춘다 |

## Python 참조 구현

```python
"""<대상> 경계 근처. 지키는 결정: D-xx (반열린 구간), D-yy (해상도: 자르지 않음)."""
from datetime import datetime, time, timedelta, timezone

import pytest
from hypothesis import given
from hypothesis import strategies as st

from mypkg.intervals import Interval  # 예시: 반열린 구간 [start, end)
from mypkg.settings import InvalidSetting, Opening  # 예시

EPS = timedelta(microseconds=1)
instants = st.datetimes(  # 정의역 (예시 값, D-zz)
    min_value=datetime(2000, 1, 1), max_value=datetime(2100, 1, 1), timezones=st.just(timezone.utc)
)


@st.composite
def wide(draw):
    a, b = draw(instants), draw(instants)
    return Interval(min(a, b), max(a, b) + EPS)


@st.composite
def near_pairs(draw):
    """둘째 구간이 첫 구간의 끝 ±1000 EPS 안에서 시작한다. k = 0 이면 정확히 맞닿는다."""
    a = draw(wide())
    k = draw(st.one_of(st.sampled_from([-1, 0, 1]), st.integers(min_value=-1_000, max_value=1_000)))
    start = a.end + k * EPS
    return a, Interval(start, start + draw(st.integers(min_value=1, max_value=10**9)) * EPS)


def overlap_oracle(a, b) -> bool:
    return not (a.end <= b.start or b.end <= a.start)


@given(st.one_of(st.tuples(wide(), wide()), near_pairs()))
def test_overlap_matches_oracle(pair):
    a, b = pair
    assert a.overlaps(b) == overlap_oracle(a, b)


@given(st.integers(min_value=0, max_value=59), st.integers(min_value=0, max_value=999_999))
def test_only_subfield_violation_is_rejected(sec, us):
    """표적: 분은 정상, 초 · 마이크로초만 0 이 아니다 (D-ww: 분 단위만 허용)."""
    if sec == 0 and us == 0:
        sec = 1
    with pytest.raises(InvalidSetting):
        Opening(at=time(10, 0, sec, us))
```
