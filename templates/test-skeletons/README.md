# 테스트 골격 (Python 참조)

## 언제 쓰나

결정 기록에 값(단위 · 범위 · 정밀도 · 검증 위치)이 선언된 뒤, 테스트 작성자가 슬라이스의 테스트를 처음 쓸 때 복사해서 채운다. 테스트 설계 템플릿(`templates/test-design/`)이 "무엇을 왜"를 설명한다면, 골격은 그 설계를 그대로 옮긴 **복사용 코드**다. 골격을 채운 테스트는 H3 승인 자료와 게으른 구현 검증을 거쳐 잠근다.

**언어:** 이 골격은 **Python 참조 구현**이다 (pytest + hypothesis, 인수 시나리오는 pytest-bdd, 게으른 후보 실행기는 pytest-timeout). 구조 · 머리말 규약 · 자리표시자 규칙 · 골격과 설계 템플릿의 대응은 언어와 무관하다. 다른 언어 어댑터는 같은 이름과 구조의 골격을 그 언어의 도구(성질 테스트 · BDD · 시간 제한 도구)로 제공한다.

## 고르는 법

| 테스트 대상 | 골격 |
|:---|:---|
| 값을 담고 비교 · 연산되는 타입 (생성 시 검증) | `test_value_object_skeleton.py` |
| 입력 → 출력 계산 (규칙 · 경계 · 매개변수) | `test_pure_calculation_skeleton.py` |
| 상태와 동작, 값에 걸린 전이 조건(가드) | `test_state_machine_skeleton.py` |
| 포트의 Fake와 실제 어댑터 | `test_contract_skeleton.py` |
| 포트를 조립하는 유스케이스, 부수효과, 멱등성 | `test_usecase_with_fakes_skeleton.py` |
| 레이어 · 금지 호출 · 테스트 도구 import | `test_architecture_rules_skeleton.py` (그대로 실행됨, 설정만 바꿈) |
| 업무 담당자가 승인하는 시나리오 | `acceptance.feature.skeleton` + `test_acceptance_skeleton.py` |
| 테스트가 게으른 구현을 실제로 잡는지 | `lazy_candidates_skeleton/` |

한 대상에 여러 골격이 해당하면 모두 쓴다. 예: 상태 기계의 생성 팩토리는 값 객체 골격의 속성 보존(C-A7)도 함께 쓴다.

## 대응표: 골격 → 테스트 설계 → 체크리스트

| 골격 | 테스트 설계 템플릿 | 체크리스트 항목 |
|:---|:---|:---|
| value_object | 02 독립 오라클(원시 값 비교), 05 거부 영역, 07 속성 보존 | B-2, C-A1, C-A3, C-A4, C-A6, C-A7 |
| pure_calculation | 01 경계값 표, 02 독립 오라클, 03 경계 근처 생성기, 06 매개변수 가변 | B-1, B-3, B-6, C-A1, C-A2, C-A3, C-A5, C-A8 |
| state_machine | 04 전수 상태 표, 01 경계값 표(가드), 03 경계 근처(가드) | B-3, B-4, C-A2, C-A3 |
| contract | 08 계약 테스트, 07 왕복 보존, 09 조회의 완전성 | B-4, B-6, B-7 |
| usecase_with_fakes | 09 공허한 참 방지(양성 대조), Spy 횟수, 멱등성 | B-1, B-4, B-5, B-6 |
| architecture_rules | 09 공허한 참 방지, 구조 적합성 | B-1, B-7, B-9 |
| acceptance | 01 경계값 표(업무 말로), 거부 · 멱등 시나리오 | B-3, B-4, B-5, B-8 |
| lazy_candidates | 10 게으른 구현 검증 절차 | B-9, 후보 출처 C-A1 ~ C-A7 |

| 체크리스트 항목 | 그 항목을 구현한 골격 |
|:---|:---|
| C-A1 정의역 전체 | value_object, pure_calculation |
| C-A2 경계 ± 최소 해상도 | pure_calculation, state_machine |
| C-A3 값의 선언 | 모든 골격의 `[사람 결정 C-A3]` 자리 |
| C-A4 값 객체 비교 | value_object |
| C-A5 매개변수 가변 | pure_calculation |
| C-A6 거부 영역 전체 | value_object |
| C-A7 속성 보존 | value_object, contract (왕복) |
| C-A8 시간 상한 | pure_calculation (`max_examples` 주석), lazy_candidates (시간 제한) |

## 자리표시자 규칙

| 표기 | 뜻 | 남아 있으면 |
|:---|:---|:---|
| 코드 안의 `<...>` | 프로젝트 이름 · 값으로 바꾼다 | **SyntaxError.** 반쯤 채운 골격이 조용히 통과하지 않게 하려는 의도다 |
| 문자열 안의 `"<...>"` | 이름 · 경로 · 단계 문장 | 실행되지만 "검사할 파일이 없다" 등으로 실패한다 |
| 문서 문자열 · 주석의 `<...>` | 지키는 ID 등 | 실행에 영향 없음. 머리말 검사(예정)가 잡는다 |
| `[사람 결정 C-A3]`, `[사람 결정 H1]`, `[사람 결정 H4]` | 사람이 결정 기록에서 옮겨야 하는 값 | 잠금 전에 0건이어야 한다: `grep -rn "\[사람 결정" tests/` |
| `.feature` 의 `[...]` | 사람이 채울 자리 | Gherkin에서 `<...>`는 Scenario Outline 매개변수라서 다른 표기를 쓴다 |
| `raise NotImplementedError(...)` (오라클) | 사람이 · 테스트 작성자가 쓸 정답 | 테스트가 실패한다 |

| 자리표시자 | 바꿀 것 |
|:---|:---|
| `<package>`, `<module>`, `<tests_package>` | 제품 패키지 · 대상 모듈 · 테스트 패키지 |
| `<Domain>`, `<Entity>`, `<Status>`, `<Item>`, `<UseCase>`, `<Command>` | 대상 타입 |
| `<InvalidDomain>`, `<IllegalTransition>`, `<DomainError>`, `<NotFound>`, `<Conflict>` | 계약된 예외 클래스 |
| `<FakeAdapter>`, `<RealAdapter>`, `<FakeRepository>`, `<FakeClock>`, `<SpyOutbound>` | 테스트 대역과 실제 어댑터 |
| `<FR-###>`, `<INV-xx>`, `<D-xx>` | 지키는 ID (`HEADER_CONVENTION.md`) |

## 사용 절차

1. 대상의 모든 입력 · 설정값이 결정 기록에 선언되었는지 확인한다 (C-A3). 없으면 골격을 쓰지 않고 H2로 돌린다
2. 골격을 프로젝트의 테스트 위치로 복사하고 이름에서 `_skeleton`을 뗀다 (`conftest.py`는 복사하지 않는다)
3. 머리말을 채운다 (`HEADER_CONVENTION.md`). 모든 모듈은 지키는 ID를 인용한다
4. `[사람 결정` 자리를 결정 기록의 값과 ID로 바꾼다
5. 코드 자리표시자를 SyntaxError가 없어질 때까지 바꾼다
6. 오라클을 구현과 **다른 형태**로 쓴다 (표 · 집합 · 닫힌 식)
7. 해당하지 않는 테스트는 지우고, 지운 이유를 머리말 `not-covered`에 적는다
8. 게으른 구현 검증(`lazy_candidates_skeleton/`)을 돌려 H3 승인 자료 §2에 붙인다

## 이 골격 자체의 검증 (templates-v0.2)

| 골격 | 확인한 것 |
|:---|:---|
| 모든 `.py` | 자리표시자를 이름으로 바꾸면 컴파일된다. 바꾸기 전에는 코드 자리표시자가 있는 골격만 SyntaxError |
| architecture_rules | 예시 패키지에서 깨끗한 코드 통과. 주입한 위반(레이어 밖 import, 대입 별칭을 거친 현재 시각, 동적 `getattr`, 규칙 없는 파일, 루트 `__init__`의 테스트 도구 import) 모두 실패. `src`가 비면 실패(skip 아님). 설정을 바꾸지 않은 골격도 실패 |
| value_object · pure_calculation · state_machine | 예시 구현으로 채워 정상은 통과. 게으른 변형(단위 검증 누락, 비교 항상 참, 매개변수 고정, 큰 값 자르기, 경계 1 해상도 오차, 금지 전이에서 상태 변경, 가드 허용 오차)은 모두 실패 |
| lazy_candidates | 예시 프로젝트(설정 파일 `pythonpath = [".", "src"]`)에서 정상 통과, 후보 3개 실패(시간 초과 포함), 오타 ID 탐지. 덮어쓰기를 빼면 실제 구현이 import되어 모든 후보가 통과 → 실행기가 BAD로 보고 |
| acceptance | 같은 패턴(한국어 단계, Scenario Outline, `parsers.parse`)이 pytest-bdd 9.0에서 동작 |
| contract · usecase_with_fakes | 문법만 확인. 예시 실행은 하지 않았다 |

검증 중 고친 것: 경계 근처 오프셋을 넓은 범위에서 고르게만 뽑으면 경계를 1 해상도 옮긴 후보가 300개 예시에서 살아남았다 → -1 · 0 · +1을 따로 뽑는다. 시간 제한이 프로세스를 바로 끝내면 끝에서 출력하는 import 확인이 사라졌다 → 수집 직후 파일로 기록한다.
