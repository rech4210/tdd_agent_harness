---
description: Clarify feature specification with TDD harness constraints (clarification promotion, boundary table sync, human decision linking).
strategy: wrap
---

{CORE_TEMPLATE}

## TDD Harness additions

하네스 연동 지시문:

1. **답변(Clarifications) 표기**:
   - 명확화 세션에서 받아들인 각 답변 줄 끝에 반드시 `→ [결정 대기]` 를 붙인다.
   - 예: `- Q: 통화는 몇 개인가? → A: 원화 하나 → [결정 대기]`
2. **결정 기록 반영 (D-## 승격)**:
   - 결정 번호(`D-##`)와 사람 구간 표시(`H#`)를 스스로 쓰지 않는다. `[결정 대기]` 는 GT 를 통과하지 못한다(의도). 사람이 `reports/decisions.md` 에 결정을 기록하고 `→ D-##` 로 바꾸거나, 테스트로 지키지 않고 사람 구간에서 다루기로 **사람이** 정하면 `→ H#` 로 바꾼다. 에이전트는 `D-##` · `H#` 를 쓰지 않는다.
   - 사람이 `reports/decisions.md` 에 해당 결정(`D-##`)을 기록하면, 그 표시를 `→ D-##` 로 바꾼다 (사람 또는 사람이 지시한 명세 작성자가 갱신).
3. **경계값 표(Boundary tables) 동기화**:
   - 답변이 값(단위, 범위, 정밀도, 경계 등)을 확정하면, `## Boundary tables` 의 해당 항목 결정 출처 칸도 같은 `D-##` 로 바꾼다.
4. **테스트 및 소스 코드 작성 금지**:
   - 테스트(`tests/**`)나 제품 소스(`src/**`)를 작성하거나 수정하지 않는다.
5. **설정 및 헌법 경로 수정 금지**:
   - `config/**` 및 `.specify/memory/**` 경로의 파일을 절대 수정하지 않는다.
