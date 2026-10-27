# 하네스 ↔ 프로젝트 작업 방법

이 프로젝트는 `agent-harness` 에서 만들었다 (`harness/new_project.py`, 이전 방식은 clone). 하네스(판정 엔진 · 템플릿)와 프로젝트(도메인 코드 · 설정)는 **소유가 다르고, 고치는 곳도 다르다.**

## 0. 새 프로젝트 만들기 (agent-harness 에서)

```sh
python harness/new_project.py <대상 폴더> --package <이름> [--roles <프리셋>] [--upstream <하네스 주소>] [--commit]
```

- 하네스 저장소의 **커밋된 내용**에서 `harness/` · `adapters/` · `templates/` · `config/` · `docs/` 만 복사하고, `README.md` · `docs/HANDOFF.md` 는 `templates/project/` 틀로 만든다. 하네스 개발 이력(`dev/` · `HARNESS_*` · git 이력)은 들어가지 않는다.
- `--commit`(선택, 없으면 커밋 안 함, git 작성자 설정이 없으면 실패하고 이유 출력). push · 병합은 사람.
- 새 저장소: `git init` (기본은 커밋 없음, 사람이 첫 커밋 또는 `--commit` 선택), 원격 `upstream` = 하네스, `harness/UPSTREAM` = 기준 커밋 → 2절 받기가 그대로 돈다.
- 생성 뒤 환경 준비: `python adapters/python/setup_env.py --dry-run` 으로 계획 확인 뒤 실제 실행(선택 `--speckit <연동>`), 단계 순서와 “재생성” 표시 확인.
- 역할: `python harness/roles.py --preset <이름> [--slice <ID>]` (사람이 실행). 에이전트 등록 · 배정을 `config/` 에 덧붙이고, 안내 문서(`AGENTS.md` · `CLAUDE.md`)는 `harness:roles` 표시 구간만 바꾼다. 위임 도구의 구간은 건드리지 않는다.
- 작업 지시서 머리말에 `family: <실제 실행 계열>` 을 적으면 GA 가 `config/agents.toml` 과 대조한다 (위임 도구의 프로필이 다른 계열 모델을 돌리는 경우를 잡는다).

## 1. 소유 경계

| 경로 | 소유 | 누가 고치나 | 업그레이드 때 |
|:---|:---|:---|:---|
| `harness/` · `adapters/` · `templates/` | **하네스** | agent-harness 저장소에서만 | 상류 내용으로 바뀐다 |
| 상류 `docs/` | **하네스 소유 문서** | 필요 시 고친다 | 프로젝트가 안 고친 것은 갱신 · 없는 것은 추가 · 고친 것은 “건너뜀” |
| 루트 `CLAUDE.md` · `AGENTS.md` 역할 구간 | **하네스** | 고치지 않는다 | 고치지 않은 것만 새 템플릿으로 갱신 (고친 것은 건너뜀) |
| `config/*.toml` | 프로젝트 | 사람만 (게이트 · 역할 · 배정 = 규칙) | 그대로 |
| `src/` · `tests/` · `specs/` · `reports/`, 상류에 없는 `docs/` | 프로젝트 | 역할 규칙(`config/roles.toml`)대로 | 그대로 |

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
- upgrade 가 상류 `docs/` 중 프로젝트가 안 고친 것은 갱신 · 없는 것은 추가 · 고친 것은 “건너뜀”으로 알린다. “안 고침”은 프로젝트 사본이 **상류 이력의 어느 판과든** 같다는 뜻이다(프로젝트를 만든 판과 지난 업그레이드 판이 달라도 된다).
- 루트 `CLAUDE.md` · `AGENTS.md`의 역할 구간(`harness:roles`)도 같은 규칙으로 갱신된다. 두 프리셋 이상의 이력과 맞으면 “모호”로 건너뛴다.
- `harness/upgrade.py` 자신이 바뀌는 업그레이드는 새 코드로 한 번 이어 실행해 문서 · 역할 구간까지 처리한다(`--dry-run` 은 “자기 교체”만 알림). **이 기능이 없는 예전 판(2026-10-19 이전)에서 올라올 때는 upgrade 를 두 번 실행한다** — 첫 실행은 하네스 코드만 바꾸고, 두 번째 실행이 문서 · 역할 구간을 갱신한다.
- 바뀐 것이 없어도 `harness/UPSTREAM`에 대상 커밋을 기록한다.
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

상류 `docs/` 중 프로젝트가 안 고친 것은 `upgrade.py` 가 상류 내용으로 갱신하고, 없는 것은 추가하며, 프로젝트가 고친 문서는 건너뜀으로 보호한다. 상류에 없는 프로젝트 고유 문서는 건드리지 않는다.
