## 작업 가이드 — lead = Claude (역할 프리셋 `claude-lead`)

목표: 제약으로 에이전트 작업을 믿을 수 있게 해서 사람의 시간을 줄인다.

**먼저 읽는다**: `docs/HANDOFF.md` (지금 상태 · 다음 일) → `docs/PRODUCT.md` (제품 명세 · 슬라이스 순서) → `docs/cloud-sdd-workflow.md` (상태 기계) → `docs/sdd-integration.md` (흐름) → `docs/harness-sync.md` (하네스와의 경계) → `docs/human-zones.md` (사람이 정하는 곳).

### 1. 역할 배치

| 역할 | 누가 | 하는 일 | 쓰기 권한 (`config/roles.toml`) |
|:---|:---|:---|:---|
| **lead** · spec_author · test_author | **Claude** | 명세 초안 · 명료화 질문, 판정 사례 · 테스트 작성, 작업 지시서, 게이트 실행 · 판정, 구현 위임 지휘 | 단계마다 해당 역할의 경로만 |
| **implementer** | **AGY** (Gemini, 로컬) | 격리된 작업 공간에서 `src/**` 만. 명령 실행 권한 없음 (편집만) | `src/**`, `reports/**` |
| **adversary** · mutation_auditor | **Codex** | 품질 검토자 (reviewer 는 배정 안 함) 및 변이 분류자. diff 1차 검토, 데이터 오염 · 상태 파괴 등 실제 피해를 내는 결함 판단, 변이 분류 (`AGENTS.md`) | `reports/**` 만 |
| 사람 | 사용자 | H1 · H2 결정, H3 테스트 승인 · 잠금, 병합, `config/` | — |

분리 규칙: 명세 · 테스트 작성자 ≠ 구현자, 테스트 작성자 ≠ 품질 검토자 · 변이 분류자, 구현자 ≠ 품질 검토자 (모두 **다른 모델 계열**). 슬라이스 배정은 사람이 `python harness/roles.py --preset claude-lead --slice <ID>` (또는 `--ask`) 로 적고, 게이트 GA 가 검사한다.

### 2. 슬라이스 흐름

| # | 단계 | 누가 | 산출물 | 멈춤 |
|:---|:---|:---|:---|:---|
| 0 | 제품 명세 (처음 한 번) | Claude → **사람** | `docs/PRODUCT.md`: 요구를 묻고 목적 · 대상 · 범위 밖, 용어, 전역 불변식 INV-G#, 슬라이스 순서 (얕게) | **승인** |
| 1 | specify · clarify | Claude | `specs/<###-기능>/spec.md` (가정 · 명료화 답은 `→ [결정 대기]`) | — |
| 2 | 결정 | **사람** | `reports/decisions.md` 에 D-##, `→ [결정 대기]` 를 `→ D-##` / `→ H#` 로 | **H1 · H2** |
| 3 | 테스트 설계 · 작성 | Claude | `tests/**` (머리말 `guards` · `technique` · `checklist`), GT 통과, 게으른 구현 검증 | — |
| 4 | 품질 검토 1 | Codex | `reports/review/<슬라이스>-tests.md` (가장 게으른 구현으로 통과할 수 있는가, 피해 시나리오 동봉) | — |
| 5 | 승인 · 잠금 | **사람** | `templates/h3-approval.md`, `python harness/check_protected_paths.py --write-lock`, 기준 태그 | **H3** |
| 6 | 구현 | AGY (로컬 위임) | `src/**`. 작업 지시서 머리말 `agent: agy` · `family: <실제 실행 계열>` (또는 `job: <위임 job>` + `config/delegation.toml`) | — |
| 7 | 판정 | Claude | `python harness/run_gates.py --role implementer --base <기준 태그> --slice <슬라이스> --wo <작업 ID>` + **diff 를 읽는다** (판정 핵심 코드 · 테스트 변경 · 검사를 약하게 하는 삭제는 전부) | — |
| 8 | 품질 검토 2 | Codex | `reports/review/<슬라이스>-impl.md` (사례 맞추기 · 우회 · 신호 삭제) | — |
| 9 | 병합 · 종료 | **사람** | `templates/slice-closure.md`, 종료 태그 | **병합** |

- **판정은 게이트만 한다.** 에이전트의 "끝났다" 보고(자기 보고)와 위임 도구의 SUCCESS 는 판정이 아니다.
- Codex 의 "문제 없음" 을 판정으로 쓰지 않는다. Codex 가 짚은 곳은 Claude 가 직접 읽는다.
- 사람에게 물을 것은 **모아서 한 번에** 묻는다 (멈춤 횟수 = KPI).

### 3. 사람 개입 시간 기록 (KPI)

```sh
python harness/mark.py wait-start <H1|H2|H3|DEC|MERGE> --wo <작업 ID>
python harness/mark.py wait-end   <같은 구간> --wo <작업 ID> --decisions <결정 수>
python harness/kpi.py            # reports/kpi.md
```

### 4. 하네스와의 경계

- 하네스 결함은 여기서 고치지 않는다 → `docs/harness-sync.md` 3절.
- 이어 가기 정보는 **저장소 안에만** 둔다 (`docs/HANDOFF.md`).
