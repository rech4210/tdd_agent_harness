# Spec Kit SDD Preset (`tdd-harness`)

TDD 하네스(`agent-harness`)와 Spec Kit(명세 중심 개발 도구)을 연동하기 위한 공식 프리셋입니다. 명세 작성(`specify`) 및 모호성 해소(`clarify`) 단계에 TDD 하네스의 불변식, 경계값 표, 권위 순서 제약을 부여합니다.

## 1. 설치 및 설정

| 단계 | 명령 | 설명 |
|:---|:---|:---|
| 개발 모드 설치 | `specify preset add --dev templates/sdd/preset` | 저장소 내 로컬 프리셋 경로 등록 |
| 우선순위 지정 | `specify preset add --dev templates/sdd/preset --priority 1` | 낮은 숫자가 높은 우선순위 (코어 및 타 프리셋보다 우선) |
| 템플릿 해결 확인 | `specify preset resolve spec-template` | 합성된 명세 템플릿 결과 출력 |
| 명령 해결 확인 | `specify preset resolve speckit.specify` | 합성된 specify 명령 지시문 출력 |

## 2. 파일별 변경 사항

| 파일 | 대상 | 합성 전략 | 역할 및 변경 내용 |
|:---|:---|:---|:---|
| `preset.yml` | 프리셋 정의 | — | ID `tdd-harness`, 버전 `1.0.0`, 제공 템플릿/명령 선언 |
| `templates/spec-template.md` | `spec-template` | `wrap` | 코어 템플릿 뒤에 `## Invariants` 및 `## Boundary tables` 절 추가. GT 인식용 주석 안내 |
| `commands/speckit.specify.md` | `speckit.specify` | `wrap` | 코어 명령 뒤에 하네스 지시문 추가 (가정 `→ [결정 대기]` 표기, `D-##` · `H#` 작성 금지, 코드 미작성, 설정 보호) |
| `commands/speckit.clarify.md` | `speckit.clarify` | `wrap` | 코어 명령 뒤에 하네스 지시문 추가 (답변 `→ [결정 대기]` 표기, `D-##` 연계, Boundary tables 동기화) |
| `commands/speckit.constitution.md` | `speckit.constitution` | `replace` | 코어 명령 대체. 직접 편집 차단 및 `python harness/constitution.py --write` 안내 |

## 3. `constitution-sync` 를 쓰지 않는 이유 (HD-04)

- **단일 진실 원천(E5)**: 하네스 판정의 유일한 권위는 사람이 관리하는 `config/*.toml`이다.
- **복제 배제**: Spec Kit의 `constitution-sync`는 "진실의 원천을 복제한다"고 명시한 선택 기능으로, 다중 원천에 따른 불일치 위험을 만든다.
- **에이전트 쓰기 차단**: `.specify/memory/**`는 에이전트 수정 금지 경로(`config/roles.toml`, G0)이며, 헌법은 오직 `python harness/constitution.py --write` 명령으로만 생성한다.

## 4. Spec Kit 버전 기준

- **기준 커밋**: `985e637`
- **기준 버전**: `1.1.4.dev0`
