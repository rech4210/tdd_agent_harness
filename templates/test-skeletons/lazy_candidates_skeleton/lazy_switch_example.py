"""검증 빌드의 후보 스위치 예시 (제품 아님 · 병합 대상 아님 · 구현자에게 주지 않음).

guards:      (검증 장치) <테스트 파일>이 지키는 <INV-xx>, <D-xx>
technique:   10 게으른 구현 검증 절차
checklist:   B-9, 후보의 출처 C-A1 ~ C-A7

놓는 곳: regression/lazy_build/src/<package>/<module>.py (제품 경로 src/ 밖, 판정 범위 밖)
규칙
- 환경 변수 LAZY 가 비어 있으면 정상 구현이다. 정상 빌드는 잠긴 테스트를 모두 통과해야 한다.
- 후보마다 ID 를 하나 정하고 `LAZY == "<후보 ID>"` 분기만 더한다. 후보 ID 는 실행기의 CASES 와 같아야 한다.
- 후보는 명세를 실제로 어기는 입력이 있어야 한다. 없으면 후보가 아니라 "잡지 않는 것"이다.
- 이 파일의 패턴(LAZY 분기)은 제품 경로에 절대 두지 않는다. 병합되면 그 자체가 우회 경로다.

아래 함수는 예시다. 검증 대상 모듈의 공개 API 를 그대로 옮기고 정상 구현을 쓴 뒤 후보 분기를 더한다.
"""

import os

LAZY = os.environ.get("LAZY", "")  # 비어 있으면 정상 빌드

LIMIT = 1_000  # 예시: 이 검증 빌드 안의 후보용 상수


def compute(x: int, step: int) -> list[int]:
    """예시 계산: 0 부터 x 미만까지 step 간격의 값 (정상 구현)."""
    if LAZY != "X-N1" and step <= 0:  # 후보 X-N1: 매개변수 검증 누락 → 끝나지 않는 계산 (시간 제한으로 실패해야 함)
        raise ValueError("step must be positive")
    if LAZY == "X-A1" and x > LIMIT:  # 후보 X-A1: 큰 입력에서 자름 (C-A1)
        x = LIMIT
    if LAZY == "X-A5" and step != 1:  # 후보 X-A5: 매개변수 고정 (C-A5)
        return []
    out, v = [], 0
    while v < x:
        out.append(v)
        v += step
    return out
