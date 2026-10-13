# 05 거부 영역 전체

| 항목 | 내용 |
|:---|:---|
| 체크리스트 | C-A6, C-A3 |
| 함께 쓰는 것 | 03 표적 생성기 (한 조건만 깨는 입력), 01 (경계 바로 안쪽 수용) |

## 목적

거부 조건(예외)을 고정 예시 1 ~ 2개가 아니라 **거부 영역 전체**에서 뽑아 검사한다. 거부 조건이 여러 부분의 결합이면, **각 부분을 따로 깨는 입력**으로 하나씩 검사한다.

## 언제 쓰나

- 입력을 거부하는 생성자 · 함수 (범위, 단위, 순서, 집합 소속, 형식)
- 설정값을 담는 객체 (결정 기록 형식 C의 "검증 위치 = 도메인이 거부")
- 거부 조건이 "A 그리고 B 그리고 C" 형태일 때

## 무엇을 막나

| 실패 | 일반화된 사례 |
|:---|:---|
| 거부 조건 일부만 구현 | 거부 테스트가 예시 2개(길이 0, 거꾸로)뿐이어서, 두 인자 중 한쪽만 검사하는 구현이 통과했다 |
| 범위는 보고 단위 · 형식은 안 봄 | 범위 밖 예시만 있으면 범위 안의 단위 위반을 받아들이는 구현이 통과한다 |
| 거부 대신 조용히 정규화 | 값을 반올림해 받아들이는 구현. 결정이 "거부"라면 거부 테스트가 잡는다 |
| 너무 많이 거부 | 거부만 검사하면 유효한 값까지 거부하는 구현이 통과한다. 같은 테스트에서 수용도 검사 |

## 최소 구조

```
유효(x) = c1(x) ∧ c2(x) ∧ ... ∧ cn(x)          # 결정 D-xx 그대로 옮긴 오라클

각 ci 마다 생성기 gen_i: ci 만 깨고 나머지 cj 는 만족   # 조건별 독립 검사 (03 표적)
  → 지정된 예외 클래스 (메시지가 계약이 아니면 메시지는 보지 않는다)
  → (가능하면) 부수효과 없음

전체 공간 판정: 넓은 생성기 x
  유효(x)  → 받아들이고 값 보존 (07)
  아니면   → 거부
```

## 게으른 구현 예

| 게으른 구현 | 이 테스트가 잡는 방법 |
|:---|:---|
| c1만 검사 | gen_2 ~ gen_n이 c1을 만족하면서 다른 조건만 깬다 → 받아들임 → 실패 |
| 범위 안의 비정렬 값을 반올림해 받음 | 단위 위반 생성기 → 예외를 기대 |
| 경계 바로 안쪽까지 거부 | 전체 공간 판정의 수용 가지 |

## 흔한 실수

| 실수 | 결과 |
|:---|:---|
| `filter(lambda x: not 유효(x))`로만 거부 영역을 뽑음 | 특정 조건만 깨는 입력이 거의 안 나온다. 조건별 생성기를 직접 만든다 |
| 예외 메시지 문자열 검사 | 메시지가 계약이 아니면 깨지기 쉽다. 예외 클래스로 검사 |
| 거부만 검사 | 과잉 거부 구현이 통과 |
| 거부 조건이 결정에 없음 | 테스트 작성자의 추측이 명세가 된다. H2로 돌린다 (C-A3) |
| 검증 시점이 결정되지 않음 | "다른 조건 때문에 일찍 반환하면 검증을 건너뛰는" 구현이 통과. 결정에 "항상 계산 전에 검증"처럼 적고 그 경로도 검사 |

## Python 참조 구현

```python
"""<대상> 거부 영역. 지키는 결정: D-xx (허용: LO ~ HI, UNIT 의 배수, 도메인이 생성 시 거부)."""
import pytest
from hypothesis import given
from hypothesis import strategies as st

from mypkg.settings import BatchSize, InvalidSetting  # 예시

LO, HI, UNIT = 10, 1_000, 10  # 예시: D-xx 의 값. 사람이 결정한다


def is_valid(n: int) -> bool:  # 오라클: 결정 그대로
    return LO <= n <= HI and n % UNIT == 0


out_of_range = st.one_of(  # 범위만 깬다 (단위는 지킨다)
    st.integers(min_value=-100, max_value=LO // UNIT - 1),
    st.integers(min_value=HI // UNIT + 1, max_value=HI // UNIT + 100),
).map(lambda k: k * UNIT)
unaligned = st.builds(  # 단위만 깬다 (범위는 지킨다)
    lambda k, r: k * UNIT + r,
    st.integers(min_value=LO // UNIT, max_value=HI // UNIT - 1),
    st.integers(min_value=1, max_value=UNIT - 1),
)


@given(st.one_of(out_of_range, unaligned))
def test_each_condition_rejects_on_its_own(n):
    with pytest.raises(InvalidSetting):
        BatchSize(n)


@given(st.integers(min_value=-10 * HI, max_value=10 * HI))
def test_validity_matches_rule_over_whole_space(n):
    if is_valid(n):
        assert BatchSize(n).value == n
    else:
        with pytest.raises(InvalidSetting):
            BatchSize(n)
```
