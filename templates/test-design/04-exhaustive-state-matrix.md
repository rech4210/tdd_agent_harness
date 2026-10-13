# 04 전수 상태 표

| 항목 | 내용 |
|:---|:---|
| 체크리스트 | B-4 |
| 함께 쓰는 것 | 01 · 03 (가드 조건의 경계), 07 (생성 결과 속성) |

## 목적

유한한 **상태 × 동작** 공간을 샘플링하지 않고 **전부** 검사한다. 허용 전이는 결과 상태까지, 금지 전이는 예외와 **상태 불변**까지 확인한다.

## 언제 쓰나

- 상태 기계, 권한 표, 열거형 조합처럼 칸 수가 작을 때 (수백 칸 이하)
- 공간이 작으면 무작위로 뽑을 이유가 없다. 전부 검사하는 것이 가장 싸고 확실하다

## 무엇을 막나

| 실패 | 일반화된 사례 |
|:---|:---|
| 금지 전이가 예외를 던지기 **전에** 상태를 바꿈 | "예외가 난다"만 검사하면 통과한다. 상태 불변 검사가 잡는다 |
| 표에 없는 칸 | 상태나 동작이 추가되어도 표가 그대로면 새 칸이 검사되지 않는다. 표 자기 검사가 먼저 깨진다 |
| 허용 전이 일부 누락 · 오배정 | 칸마다 결과 상태를 비교한다 |
| 경로 의존 버그 | 한 단계 표는 못 잡는다. 무작위 동작 순서열을 모델과 비교하는 가지를 더한다 |

## 최소 구조

```
ALLOWED = {(상태, 동작): 다음 상태}          # 명세 그대로. 여기 없는 칸은 전부 금지
PATH_TO = {상태: 그 상태로 가는 허용 동작 열}  # 공개 동작만 사용 (내부 setter 금지)

표 자기 검사:
  PATH_TO 의 키 == 모든 상태
  칸 수 == <명세의 상태 수> × <명세의 동작 수>   # 숫자를 직접 적는다. 상태가 늘면 여기서 먼저 깨진다

for (s, a) in 모든 칸:
  obj = 도달(s)                 # PATH_TO 로
  if (s, a) in ALLOWED:
      실행; 상태 == ALLOWED[(s, a)]
  else:
      지정된 예외 기대
      상태 == s                 # 불변
      (다른 관측 가능한 속성도 불변)

가드 조건 (시각 · 수량 등): 전수 표에 섞지 말고 01 · 03 으로 경계 ± 최소 해상도를 따로
경로 의존: 무작위 동작 순서열 → 모델(ALLOWED 로 걷는 단순 상태 변수)과 매 단계 비교
```

## 게으른 구현 예

| 게으른 구현 | 이 표가 잡는 방법 |
|:---|:---|
| 상태를 먼저 바꾸고 조건을 검사해 예외 | 금지 칸의 상태 불변 검사 |
| 모든 동작을 허용 | 금지 칸이 예외를 기대 |
| 일부 동작이 아무 일도 하지 않음 | 허용 칸의 결과 상태 비교 |

## 흔한 실수

| 실수 | 결과 |
|:---|:---|
| 허용 칸만 검사 | 금지 전이를 허용하는 구현이 통과 |
| 예외만 검사하고 상태 불변을 빠뜨림 | 상태를 바꾼 뒤 예외를 내는 구현이 통과 |
| 칸 수 자기 검사를 `len(상태) × len(동작)`으로 계산 | 항상 참이다. 명세의 숫자를 직접 적는다 |
| 내부 setter로 상태에 도달 | 실제 전이 경로의 버그를 우회한다 |
| 가드의 경계 조건을 전수 표의 한 시점으로만 검사 | 가드의 허용 오차 · 절삭이 숨는다 (01 · 03) |

## Python 참조 구현

```python
"""<대상> 상태 기계 전수 표. 지키는 불변식: INV-xx (상태 전이 표)."""
import pytest
from hypothesis import given
from hypothesis import strategies as st

from mypkg.ticket import IllegalTransition, Status, Ticket  # 예시

S = Status
ACTIONS = ["start", "finish", "reopen", "close"]
ALLOWED = {  # 예시 표 (INV-xx)
    (S.OPEN, "start"): S.ACTIVE,
    (S.OPEN, "close"): S.CLOSED,
    (S.ACTIVE, "finish"): S.DONE,
    (S.DONE, "reopen"): S.ACTIVE,
    (S.DONE, "close"): S.CLOSED,
}
PATH_TO = {S.OPEN: [], S.ACTIVE: ["start"], S.DONE: ["start", "finish"], S.CLOSED: ["close"]}
ALL_CELLS = [(s, a) for s in Status for a in ACTIONS]


def reach(status):
    t = Ticket.new()
    for action in PATH_TO[status]:
        getattr(t, action)()
    assert t.status is status
    return t


def test_matrix_covers_every_cell():
    assert set(PATH_TO) == set(Status)
    assert len(ALL_CELLS) == 4 * 4  # 명세의 상태 수 × 동작 수. 상태가 늘면 여기서 먼저 깨진다


@pytest.mark.parametrize("status, action", ALL_CELLS, ids=lambda x: getattr(x, "name", x))
def test_transition_matrix(status, action):
    t = reach(status)
    if (status, action) in ALLOWED:
        getattr(t, action)()
        assert t.status is ALLOWED[(status, action)]
    else:
        with pytest.raises(IllegalTransition):
            getattr(t, action)()
        assert t.status is status, "금지된 전이는 상태를 바꾸면 안 된다"


@given(st.lists(st.sampled_from(ACTIONS), max_size=20))
def test_any_action_sequence_follows_model(actions):
    t, model = Ticket.new(), S.OPEN
    for a in actions:
        if (model, a) in ALLOWED:
            getattr(t, a)()
            model = ALLOWED[(model, a)]
        else:
            with pytest.raises(IllegalTransition):
                getattr(t, a)()
        assert t.status is model
```
