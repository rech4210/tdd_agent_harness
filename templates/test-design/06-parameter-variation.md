# 06 매개변수 가변

| 항목 | 내용 |
|:---|:---|
| 체크리스트 | C-A5, C-A1, C-A3 |
| 함께 쓰는 것 | 02 독립 오라클 (오라클도 매개변수를 받는다), 03 (경계를 매개변수로 다시 계산) |

## 목적

공개 함수의 **매개변수와 설정값**을 결정에 선언된 범위에서 **바꿔 가며** 검사한다. 테스트 전체가 한 값으로만 호출하면, 그 값에서만 동작하는 구현을 막을 수 없다.

## 언제 쓰나

- 계산에 설정값 · 길이 · 간격 · 개수 같은 매개변수가 있을 때
- 테스트 파일에 매개변수 상수가 하나씩만 있을 때 (공통 픽스처의 기본값 포함)
- 입력 목록의 길이 · 개수에 상한이 선언되지 않았을 때 (상한 없음 → 표본을 넓게)

## 무엇을 막나

| 실패 | 일반화된 사례 |
|:---|:---|
| 매개변수 고정 구현 | 핵심 매개변수가 테스트 전체에서 한 값으로만 쓰여서, "그 값이 아니면 빈 결과"를 돌려주는 구현이 손 계산 예시와 성질 테스트를 모두 통과했다 |
| 목록 길이 과적합 | 입력 목록을 짧게만 뽑아서, 길이가 일정 수를 넘으면 다르게 동작하는 구현이 통과했다 |
| 기본값에만 맞는 계산 | 기본값과 다른 값을 넣었을 때 처음 드러난다 |

## 최소 구조

```
각 매개변수 p: 결정 D-xx 의 단위 · 최소 · 최대 → 생성기 gen_p (단위 격자 위의 값만)
시나리오 = (p1, p2, ..., 입력)                 # 여러 매개변수를 동시에 바꾼다
성질: 구현(시나리오) == 오라클(시나리오)       # 오라클도 모든 매개변수를 그대로 받는다
경계 근처(03): 생성기 안에서 그 시나리오의 매개변수로 경계를 다시 계산
목록 입력: 길이 0 포함, 선언된 상한까지 (상한 없음이면 넓은 표본 + 그 한계를 문서에 적음)
```

## 게으른 구현 예

| 게으른 구현 | 이 테스트가 잡는 방법 |
|:---|:---|
| `if p != 기본값: return []` | 시나리오의 대부분이 기본값이 아니다 |
| `if len(items) > 작은 수: ...` | 길이 생성기가 그 수를 넘는다 |
| 매개변수를 무시하고 기본값으로 계산 | 오라클이 실제 매개변수로 계산한다 |

## 흔한 실수

| 실수 | 결과 |
|:---|:---|
| 오라클 안에 기본값 상수를 박음 | 오라클도 매개변수를 무시한다. 둘이 같이 틀린다 |
| 한 매개변수만 바꾸고 나머지 고정 | 조합에서만 드러나는 오류가 숨는다 |
| 생성 범위가 선언과 다름 | 좁으면 C-A1 위반, 넓으면 거부 영역(05)과 섞인다 |
| 단위 격자 밖 값을 섞음 | 유효 영역 검사와 거부 영역 검사가 섞인다. 거부는 05로 분리 |
| 예시 수를 줄여 시간을 맞춤 | 사람 승인 없이 하지 않는다 (C-A8) |

## Python 참조 구현

```python
"""<대상> 매개변수 가변. 지키는 결정: D-xx (page_size: 1 ~ 500), D-yy (목록 길이 상한 없음)."""
from hypothesis import given, settings
from hypothesis import strategies as st

from mypkg.paging import paginate  # 예시

page_sizes = st.integers(min_value=1, max_value=500)  # 예시: D-xx 의 범위 전체


@st.composite
def scenarios(draw):
    size = draw(page_sizes)
    items = draw(st.lists(st.integers(), max_size=1_000))  # 표본 상한. 정의역 상한이 아니다
    page = draw(st.integers(min_value=0, max_value=len(items) // size + 1))
    return items, size, page


def oracle(items, size, page):  # 구현(슬라이싱)과 다른 형태: 원소마다 소속 페이지 계산
    return [x for i, x in enumerate(items) if i // size == page]


@settings(max_examples=300)
@given(scenarios())
def test_any_page_size_matches_oracle(sc):
    items, size, page = sc
    assert paginate(items, page_size=size, page=page) == oracle(items, size, page)
```
