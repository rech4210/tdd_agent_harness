# 하네스 ↔ 프로젝트 작업 방법

이 프로젝트는 `agent-harness` 에서 만들었다 (`harness/new_project.py`, 이전 방식은 clone). 하네스(판정 엔진 · 템플릿)와 프로젝트(도메인 코드 · 설정)는 **소유가 다르고, 고치는 곳도 다르다.**

## 0. 새 프로젝트 만들기 (agent-harness 에서)

```sh
python harness/new_project.py <대상 폴더> --package <이름> [--roles <프리셋>] [--upstream <하네스 주소>]
```

- 하네스 저장소의 **커밋된 내용**에서 `harness/` · `adapters/` · `templates/` · `config/` · `docs/` 만 복사하고, `README.md` · `docs/HANDOFF.md` 는 `templates/project/` 틀로 만든다. 하네스 개발 이력(`dev/` · `HARNESS_*` · git 이력)은 들어가지 않는다.
- 새 저장소: `git init` (커밋 없음, 사람이 첫 커밋), 원격 `upstream` = 하네스, `harness/UPSTREAM` = 기준 커밋 → 2절 받기가 그대로 돈다.
- 역할: `python harness/roles.py --preset <이름> [--slice <ID>]` (사람이 실행). 에이전트 등록 · 배정을 `config/` 에 덧붙이고, 안내 문서(`AGENTS.md` · `CLAUDE.md`)는 `harness:roles` 표시 구간만 바꾼다. 위임 도구의 구간은 건드리지 않는다.
- 작업 지시서 머리말에 `family: <실제 실행 계열>` 을 적으면 GA 가 `config/agents.toml` 과 대조한다 (위임 도구의 프로필이 다른 계열 모델을 돌리는 경우를 잡는다).

## 1. 소유 경계

| 경로 | 소유 | 누가 고치나 | 업그레이드 때 |
|:---|:---|:---|:---|
| `harness/` · `adapters/` · `templates/` | **하네스** | agent-harness 저장소에서만 | 상류 내용으로 바뀐다 |
| `config/*.toml` | 프로젝트 | 사람만 (게이트 · 역할 · 배정 = 규칙) | 그대로 |
| `src/` · `tests/` · `specs/` · `reports/` · `docs/` | 프로젝트 | 역할 규칙(`config/roles.toml`)대로 | 그대로 |

원칙: **프로젝트 안의 `harness/` 를 직접 고치지 않는다.** 업그레이드 때 덮어쓰이고, 에이전트 역할은 `harness/**` 를 금지당한다(G0).

## 2. 하네스 → 프로젝트 (받기)

```sh
git status                                                   # 하네스 소유 경로에 커밋 안 된 변경이 있으면 upgrade 가 멈춘다
python harness/upgrade.py --remote upstream --ref <태그 또는 main> --dry-run   # 바뀔 파일 보기
python harness/upgrade.py --remote upstream --ref <태그 또는 main>
python harness/doctor.py
python harness/run_gates.py --role implementer --base HEAD   # 전체 게이트로 판정이 그대로인지
git commit -m "Upgrade harness from agent-harness <ref> (<무엇이 바뀌었나>)"
```

- 받을 때: 태그(`core-v0.x`) 를 기본으로. `main` 은 필요한 수정이 아직 태그에 없을 때만.
- 판정이 바뀌면(통과하던 게 실패 등) 커밋하지 말고 3절의 문제 기록으로.
- 설정 형식이 바뀐 업그레이드는 상류 `config/` 템플릿과 비교해 사람이 `config/*.toml` 을 맞춘다 (업그레이드는 `config/` 를 건드리지 않는다).

## 3. 프로젝트 → 하네스 (고치기)

프로젝트에서 하네스 결함 · 빠진 기능을 찾았을 때:

| 단계 | 어디서 | 하는 일 |
|:---|:---|:---|
| 1 | 프로젝트 | `reports/spec_issues.md` (없으면 `templates/issue-log.md` 로 만든다) 에 **유형 = 하네스** 로 적는다: 재현 방법 · 기대 · 실제 · 영향 |
| 2 | 프로젝트 | 막히면 임시 우회는 **설정(`config/`)으로만**, 이유를 결정 기록에. `harness/` 패치는 하지 않는다 |
| 3 | agent-harness | 하네스 개발 방식으로 고친다: Red 판정 사례(`harness/selftest/check_*.py`) → 작업 지시서 → 구현 위임 → 세션 밖 판정 + diff 읽기 → 레드팀 → 사람 병합 → 태그 |
| 4 | 프로젝트 | 2절로 받는다. 문제 기록에 "하네스 `<태그>` 에서 고침" |

- 프로젝트 이름 · 도메인 단어는 하네스 Core 에 넣지 않는다 (`harness/selftest/check_core_agnostic.py` 가 검사).
- 하네스 쪽 판정 사례에는 프로젝트 코드를 복사하지 말고, 같은 상황을 만드는 **최소 사례**를 새로 쓴다.

## 4. 문서

`docs/` 는 업그레이드되지 않는다. 상류의 `docs/*.md` 가 바뀌었으면 필요한 것만 사람이 옮긴다. 이 문서도 같다 (상류: agent-harness `docs/harness-sync.md`).
