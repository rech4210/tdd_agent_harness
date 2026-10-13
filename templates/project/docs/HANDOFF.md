# HANDOFF — {{PROJECT}}

이어 가는 사람 · 에이전트가 먼저 읽는 문서. 장소(클라우드 · 로컬)와 세션이 바뀌어도 이 문서와 저장소만으로 이어 간다.

상태: S0
> **진행 상태 문서 갱신 규칙**: `docs/HANDOFF.md` 의 `상태:` 는 **lead 만, 판정 구간 밖에서**(기준 태그 전 또는 판정 뒤) 갱신한다. 다른 역할은 자기 산출물 커밋까지만 하고 상태를 바꾸지 않는다 (역할 판정 구간 안의 lead 커밋은 그 역할의 G0 위반으로 집계되기 때문이다).

## 1. 무엇을, 왜

- 서비스: (사람이 한 줄로)
- 측정: 사람 개입 시간 (`python harness/kpi.py` → `reports/kpi.md`). 막힌 지점 수 · 결정 수 · 대기 시간.

## 2. 지금 상태 ({{DATE}})

| 항목 | 상태 |
|:---|:---|
| 하네스 | `python harness/new_project.py` 로 생성 (기준 `{{HARNESS_REF}}`), 원격 `upstream` = 하네스 |
| init | 패키지 `{{PACKAGE}}`, 골격 · 헌법(`.specify/memory/constitution.md`) 생성 |
| 역할 | `python harness/roles.py --list` 로 프리셋 확인 → 적용 여부를 여기에 적는다 |
| 커밋 | 아직 없음 (사람이 확인 후 첫 커밋) |

## 3. 다음 일

0. (lead → 사람) 제품 명세 `docs/PRODUCT.md` (상태 P, `docs/cloud-sdd-workflow.md`): 요구를 묻고 얕은 전체 명세 · 슬라이스 순서를 제안, 사람 승인.
1. (사람) 생성된 파일을 확인하고 첫 커밋. 프로젝트 원격(`origin`)을 추가해 push.
2. (사람) Spec Kit: `specify init --here` → `specify preset add --dev templates/sdd/preset --priority 1`.
3. (사람) 역할 프리셋 적용: `python harness/roles.py --preset <이름>` (안내 문서 · 에이전트 등록). 첫 슬라이스 범위를 정한 뒤 `python harness/roles.py --preset <이름> --slice <ID>` (배정, GA 가 검사).
4. (lead) 안내 문서(`AGENTS.md` 또는 `CLAUDE.md` 의 역할 구간) 흐름의 1단계부터.

## 4. 결정 · 교훈

| 날짜 | 내용 |
|:---|:---|
