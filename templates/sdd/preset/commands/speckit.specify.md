---
description: Create or update feature specification with TDD harness constraints (invariants, boundary tables, assumption promotion).
strategy: wrap
---

{CORE_TEMPLATE}

## TDD Harness additions

하네스 연동 지시문:

1. **가정(Assumptions) 표기**:
   - 가정 목록의 모든 항목 끝에 반드시 `→ [결정 대기]` 를 덧붙인다.
2. **결정 번호 · 사람 구간 표시 금지**:
   - 결정 번호(`D-##`)와 사람 구간 표시(`H#`)를 스스로 쓰지 않는다. 둘 다 사람만 쓴다.
   - `[결정 대기]` 는 GT 를 통과하지 못한다(의도). 사람이 `reports/decisions.md` 에 결정을 기록하고 `→ D-##` 로 바꾸거나, 테스트로 지키지 않고 사람 구간에서 다루기로 **사람이** 정하면 `→ H#` 로 바꾼다. 에이전트는 `D-##` · `H#` 를 쓰지 않는다.
3. **불변식 및 경계값 표 작성**:
   - `## Invariants` 절에 시스템 불변식을 `- **INV-1**: ...` 형식으로 정의한다.
   - `## Boundary tables` 절의 표를 채운다. 결정되지 않았거나 모르는 값 칸은 반드시 `[결정 대기]` 으로 적는다.
4. **테스트 및 소스 코드 작성 금지**:
   - 테스트(`tests/**`)나 제품 소스(`src/**`)를 작성하거나 수정하지 않는다. 명세 파일만 다룬다.
5. **설정 및 헌법 경로 수정 금지**:
   - `config/**` 및 `.specify/memory/**` 경로의 파일을 절대 수정하지 않는다.
