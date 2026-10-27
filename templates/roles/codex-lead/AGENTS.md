## 작업 가이드 — lead = Codex (역할 프리셋 `codex-lead`)

목표: 제약으로 에이전트 작업을 믿을 수 있게 해서 사람의 시간을 줄인다.

**먼저 읽는다**: `docs/HANDOFF.md` (지금 상태 · 다음 일) → `docs/PRODUCT.md` (제품 명세 · 슬라이스 순서) → `docs/cloud-sdd-workflow.md` (상태 기계) → `docs/sdd-integration.md` (흐름) → `docs/harness-sync.md` (하네스와의 경계) → `docs/human-zones.md` (사람이 정하는 곳).

### 1. 역할 배치

| 역할 | 누가 | 하는 일 | 쓰기 권한 (`config/roles.toml`) |
|:---|:---|:---|:---|
| **lead** · spec_author · test_author | **Codex** | 명세 초안 · 명료화 질문, 테스트 설계 · 작성, 작업 지시서, 게이트 실행 · 판정, 구현 위임 지휘 | 단계마다 해당 역할의 경로만 |
| **implementer** | **AGY** (Gemini, 로컬) | 격리된 작업 공간에서 `src/**` 만 | `src/**`, `reports/**` |
| **adversary** | **Claude** | 품질 검토자 (reviewer 는 배정 안 함). 명세 · 테스트 · 구현 diff 를 읽고 데이터 오염 · 상태 전이 파괴 등 실제 피해를 내는 결함 판단, 승격 규칙에 맞지 않는 코너 케이스는 스스로 기각, 검토 예산(위험도 프로필) 안에서 피해 시나리오와 해결 방향 함께 제시 (`CLAUDE.md`) | `reports/**` 만 |
| 사람 | 사용자 | H1 · H2 결정, H3 테스트 승인, H9 생존 변이 판정, 병합 · push, `config/` | — |

분리 규칙: 명세 작성자 ≠ 구현자, 테스트 작성자 ≠ 구현자, 테스트 작성자 ≠ 품질 검토자, 구현자 ≠ 품질 검토자 (모두 **다른 모델 계열**). 슬라이스 배정은 사람이 `python harness/roles.py --preset codex-lead --slice <ID>` 로 적고, 게이트 GA 가 검사한다.

### 2. 슬라이스 흐름

| # | 단계 | 누가 | 산출물 | 멈춤 |
|:---|:---|:---|:---|:---|
| 0 | 제품 명세 (처음 한 번) | Codex → **사람** | `docs/PRODUCT.md`: 요구를 묻고 목적 · 대상 · 범위 밖, 용어, 전역 불변식 INV-G#, 슬라이스 순서 (얕게) | **승인** |
| 1 | specify | Codex | `specs/<###-기능>/spec.md` (FR-### · INV-# · 경계표, 가정 · 명료화 답은 `→ [결정 대기]`) | — |
| 2 | 품질 검토 1 (선택: 위험도 low 는 생략 가능 · 적합도 카드에 표시, medium 이상은 5단계와 묶을 수 있다. 정본 `docs/cloud-sdd-workflow.md` §2) | Claude | `reports/review/<슬라이스>-spec.md` | — |
| 3 | 결정 | **사람** | `reports/decisions.md` 에 D-##, 명세의 `→ [결정 대기]` 를 `→ D-##` / `→ H#` 로 | **H1 · H2** |
| 4 | 테스트 설계 · 작성 | Codex | `tests/**` (머리말 `guards` · `technique` · `checklist`), GT 통과 | — |
| 5 | 품질 검토 2 | Claude | `reports/review/<슬라이스>-tests.md` | — |
| 6 | 승인 · 잠금 | **사람** 승인 → lead | 사람이 `templates/h3-approval.md` 로 승인 → 직후 · 위임 전에 lead 가 `python harness/check_protected_paths.py --write-lock` · 커밋 · 기준 태그(annotated, 기존 태그는 옮기지 않는다) | **H3** |
| 7 | 구현 | AGY (로컬 위임, 실행: `docs/isolated-delegation.md` §오케스트레이터) | `src/**`. 작업 지시서 머리말에 `agent: agy` · `family: <실제 실행 계열>` (위임 도구 결과 `meta.json` 의 `family`) | — |
| 8 | 판정 | Codex | `python harness/run_gates.py --role implementer --base <기준 태그> --slice <슬라이스> --wo <작업 ID>` + **diff 를 읽는다** | — |
| 9 | 품질 검토 3 | Claude | `reports/review/<슬라이스>-impl.md` | — |
| 10 | 종료 · 병합 | lead (H9 대상이 있으면 사람) · 병합은 **사람** | `templates/slice-closure.md`, 종료 태그(게이트 통과 · 사람 판정이 필요한 생존 변이 없음일 때 lead). 병합 · push 는 사람 | **병합** |

- **판정은 게이트만 한다.** 에이전트의 "끝났다" 보고(자기 보고)는 판정이 아니다.
- 사람에게 물을 것은 **모아서 한 번에** 묻는다 (멈춤 횟수 = KPI).
- **읽기 조사 위임**: 넓은 파일 탐색·구조/호출 관계·규칙 대조·로그 수집은 lead 가 먼저 정독하지 않고 다른 계열 워커에 읽기 전용으로 맡기며, 준비·대기 비용이 더 크면 lead 가 직접 한다. 지시에는 질문·허용 읽기 범위·제약(읽기 전용)·반환(결론 / 근거 파일:줄 / 영향 / 미확인)만 담고 실행 뒤 작업 사본 변경 0을 확인한다. lead 는 같은 범위를 다시 정독하지 않고 판단에 쓰는 근거 줄·diff 만 확인하며, 요약은 품질 검토나 판정이 아니다.

### 3. 사람 개입 시간 기록 (KPI)

사람 판단이 필요해 멈추기 **직전**과 답을 받은 **직후**에 찍는다:

```sh
python harness/mark.py wait-start <H1|H2|H3|DEC|MERGE> --wo <작업 ID>
python harness/mark.py wait-end   <같은 구간> --wo <작업 ID> --decisions <결정 수>
python harness/kpi.py            # reports/kpi.md (자동 승인 적용 현황 절 포함)
python harness/kpi.py --timing   # reports/tdd-timing.md (단계별 UTC · 시간, kpi.md 는 그대로)
python harness/mark.py classify "<근거>" --wo <작업 ID> --tier <T1|T2|T3> --risk <low|medium|high> --basis prospective --by user   # 작업 지시서 승인 직후 · 위임 전
python adapters/tools/collect_usage.py --agy-find <작업 공간 이름> --wo <작업 ID> --role implementer --round <N> --tier <T> --risk <R>   # 위임마다. 한 대화만 맞을 때 기록 (또는 --agy-transcript <경로>, Codex: --codex-session <경로>)
python harness/mark.py amend-cause "<근거>" --wo <작업 ID> --run <gate_run 의 run_file> --cause test --by lead   # 판정 원인을 잘못 기록했을 때 (원래 기록은 그대로)
python harness/step.py --slice <슬라이스>                    # 현재 단계 · 다음 일 (증거에서 복원, 세션 재개 · 리드 교체 뒤 먼저)
python harness/step.py --slice <슬라이스> --check delegate   # 위임 전 (종료 1 = 막힘 코드를 해소)
python harness/step.py --slice <슬라이스> --check close      # 종료 태그 전
python harness/step.py --slice <슬라이스> --closure-draft    # 종료 기록 미판정 초안 (stdout 만, 파일 · 태그를 쓰지 않음)
python harness/mark.py auto-approve <D-##> --wo <작업 ID> --gt pass --lazy-caught N/M --residual-defects 0 --base-types unbounded,reject-preserve --by lead   # 자동 승인 조건을 채웠을 때만 (아니면 사람 H3)
python harness/mark.py auto-approve additive --wo <ID> --kind mutant-kill|regression --base <테스트 추가 직전 커밋 40자> --review-note "<무엇을 단언하는지>" --by lead   # 테스트만 추가 · 제품 코드 변경 0 · 위험도 high 제외일 때만, 아니면 사람 H3
python adapters/tools/codex_audit.py --workspace <깨끗한 작업 사본> --prompt-file <지시> --out <사본 밖 파일>   # 문서 점검 Codex (읽기 전용 고정, 변경 0 확인)
python adapters/tools/check_handoff_state.py --base <점검 기준>   # 문서 편집 뒤: HANDOFF 의 상태: 줄은 lead 만
```

`reports/events.jsonl` 은 커밋한다 (클라우드 · 로컬이 같은 기록을 본다).

### 4. 클라우드와 로컬

| 장소 | 할 수 있는 것 | 할 수 없는 것 |
|:---|:---|:---|
| 클라우드 | 1 ~ 6 · 8 · 9 단계, 게이트 실행 | AGY 위임 (로컬 CLI) |
| 로컬 | 7 단계 위임 (위임 도구), 하네스 상류 수정 | — |

- 공유는 브랜치로: 슬라이스마다 `slice/<슬라이스>`, 단계마다 커밋 · push.
- 이어 가기 정보는 **저장소 안에만** 둔다 (`docs/HANDOFF.md`).
- 하네스 결함은 여기서 고치지 않는다 → `docs/harness-sync.md` 3절.
