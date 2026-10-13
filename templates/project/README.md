# {{PROJECT}}

agent-harness(TDD 하네스 + Spec Kit SDD)로 만드는 프로젝트. 패키지: `{{PACKAGE}}`. 하네스 기준: `{{HARNESS_REF}}` ({{DATE}} 생성).

| 먼저 읽기 | 내용 |
|:---|:---|
| `docs/HANDOFF.md` | 지금 상태 · 다음 일 (이어 가는 사람 · 에이전트가 먼저 읽는다) |
| `docs/PRODUCT.md` | 제품 명세: 대상 · 범위 · 전역 불변식 · 슬라이스 순서 (슬라이스 전에 한 번, 사람 승인) |
| `docs/cloud-sdd-workflow.md` | 슬라이스 상태 기계 (P · S0 ~ S10), 클라우드 진행 방식 |
| `docs/sdd-integration.md` · `docs/slice-lifecycle.md` | 명세 → 테스트 → 구현 → 판정 흐름 |
| `docs/human-zones.md` | 사람이 정하는 곳 (H1 · H2 · H3 · 병합) |
| `docs/harness-sync.md` | 하네스 받기 · 하네스 결함 올려 보내기 |

하네스 소유 경로(`harness/` · `adapters/` · `templates/`)는 이 저장소에서 고치지 않는다. 설정(`config/`)은 사람만 고친다.
