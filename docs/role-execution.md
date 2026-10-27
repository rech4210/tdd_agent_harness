# 역할 전환과 수동 실행

> WO-H64 공통 구현에 반영된 사용 안내다. 고정119개와 수동 CLI 검사를 통과했다. 소비 프로젝트에는 해당 공통 커밋을 upgrade한 뒤 사용한다.

역할 상태·문서 갱신은 자동화하고, 실제 런타임을 관측할 수 없는 경우에는 리드가 수동으로 확인한다. 수동 확인은 매번 사용자에게 재승인을 요청하는 절차가 아니다. 기존 승인 범위에서 리드가 지정 모델·배정·허용 경로와 실제 변경·검사 결과를 대조하고 근거를 기록한다.

실제 모델이나 적용 권한을 확인할 근거가 없으면 **미확인**으로 남긴다. 지정된 모델 이름은 실제 실행 모델을 관측했다는 증거가 아니다. 파일 검사 통과와 런타임 검증 완료를 구별한다.

## 1. 역할 상태 확인과 lead 인계

```powershell
python -B harness/roles.py --check
python -B harness/roles.py --activate --handover --preset <새-프리셋> --approval-ref '<승인-commit>:<승인-JSON-경로>#<승인-ID>'
```

역할 승인은 기존 `kind=role-transition` 객체를 사용한다. 프리셋과 승인 `models`에 지정된 모델·계열을 기록하고 관리 문서를 함께 갱신한다. 모델명은 특정 목록에 고정하지 않으며, 실제 대화 모델 변경을 자동 감지하는 기능은 아니다.

`--handover`는 진행 중 작업의 담당자·모델·역할·기준 SHA·승인 범위를 보존하고 조율 lead를 교체한다. 새 작업은 새 활성 기본 배정을 사용한다. 기존 작업은 시작 당시 계약으로 검사한다. 기존 작업 자체의 담당 변경은 새 배정·승인을 사용하는 별도 작업이다. lead 교체가 쓰기 권한을 넓히지 않는다.

`--handover` 없이 실행하면 기존 엄격 전환의 충돌 차단을 유지한다. 실패한 전환은 성공 인계가 아니며, 오류를 확인하고 명시적 `--recover`로 복구한다. HANDOFF 상태줄에 옛 lead가 명시된 경우에는 lead가 그 상태줄을 별도로 정리해야 한다. 일반 역할 안내 갱신이 진행 상태줄까지 임의로 수정하지 않는다.

## 2. 수동 작업 등록

작업 시작 전에 깨끗한 격리 Git 사본을 준비한다. 원본과 작업 사본의 기준 SHA가 같아야 하며 서로 포함하는 경로는 허용하지 않는다. 실행 승인은 기존 `kind=execution` 객체에 `runner="manual"`을 명시한다. 작업 역할·배정 agent·선언 모델/계열·허용 paths를 기존 승인 근거와 대조한다.

```powershell
python -B harness/execution.py manual-start --wo <WO> --slice <슬라이스> --role <작업-역할> --base <전체-SHA> --approval-ref '<승인-commit>:<승인-JSON-경로>#<승인-ID>' --workspace '<격리-사본-절대경로>' --note '리드가 지정 모델·배정·승인 범위를 확인함; 실제 런타임 관측은 미확인'
```

이 명령은 작업 계약과 시작 스냅샷을 기록한다. Orca나 모델 워커를 자동 실행하지 않는다. 이후 Orca의 기존 워크트리·터미널 도구로 워커를 운용할 수 있다. 등록 이후의 작업 내용은 등록한 격리 사본에서 작성해야 한다. 결과를 먼저 작성하고 나중에 시작 스냅샷으로 등록하지 않는다.

## 3. 완료 확인과 채택

```powershell
python -B harness/execution.py manual-check --run <실행-ID> --completion-note '리드가 작업 결과와 완료 근거를 확인함'
python -B harness/execution.py manual-accept --run <실행-ID> --review-note '리드가 diff·승인 경로·검사 결과를 검토하고 미확인 런타임 한계를 확인한 뒤 채택함'
```

완료 확인과 채택 근거는 빈 문자열일 수 없다. `manual-check`를 성공한 뒤 후보가 바뀌면 채택을 거부한다. 구현자 작업은 해당 실행과 실제 후보에 결합된 품질 게이트 판정도 필요하다. 해당 작업의 요구에 맞춰 `harness/run_gates.py --execution <실행-ID>`를 수행한다. 수동 메모만으로 품질 게이트를 대체하지 않는다.

수동 결과에는 `static_validation=passed`, `runtime_verification=unverified`를 구분해 남긴다. 기존 `start/check/accept`는 엄격 런타임 관측 경로이며, 수동 실행을 이 경로의 관측 성공으로 승격하지 않는다.

계속 차단하는 사항: 역할/계열 분리 위반, 역할·승인 밖 변경, 보호 경로·HANDOFF 상태줄 변경, ignored 파일의 금지 변경, 금지 파일의 속성 변경, 검사 후 후보의 내용·속성 변경, 원본의 동시 수정, 손상된 역할 상태·인계 기록, 무관한 원본 HEAD 변경, 중복 채택. 이 검사는 OS가 모든 금지 파일 쓰기를 즉시 막는다는 보장이 아니다.

## 4. Orca 모니터링에서 확인할 것

Orca CLI의 `worktree list`와 `worktree ps`는 여러 워크트리의 상태·워커 상태를 한 번에 조회한다. `terminal list`로 받은 정확한 handle로 `terminal read`와 `terminal wait`를 사용한다. 설치된 CLI의 `skills get orca-cli` 가이드를 먼저 확인한다.

셸이 running/idle이라는 사실은 AI 작업 완료나 품질 통과를 뜻하지 않는다. 완료 출력·파일·실제 검사 결과를 대조한다. 반환된 모델·권한 정보나 종료 원인이 미확인이면 그대로 기록한다. Orca 관리·모니터링의 동작 여부와 공통 실행기의 엄격 런타임 검증 지원 여부는 별도로 판정한다.

## 5. 준비된 Orca 사본에서 등록·시작을 연결하는 수동 진입점

`python -B adapters/tools/orca_execution.py manual-launch --wo <WO> --slice <ID> --role <role> --base <전체-SHA> --approval-ref '<commit>:<승인-json>#<ID>' --workspace '<준비된 사본>' --worktree 'id:<repo>::<사본경로>' --prompt-file '<지시서>' --note '<리드 확인 근거>'`를 사용한다.

빈 프롬프트·권한 preflight FAIL·실제 worktree 경로 불일치는 등록/실행 전에 거부한다. 기존 manual-start의 승인·역할·GA·구현자 위임 검사를 통과한 뒤 같은 대상에 agent launch를 한 번 요청한다. runtime_verification은 unverified이며 launched는 완료 판정이 아니다. 실패·불확실 응답은 nonzero와 `reports/executions/<실행-ID>/orca-launch.json`으로 남긴다. 이미 실행됐을 수 있으므로 터미널 목록/영수증을 확인하기 전에 자동 재시도하지 않는다. 디스크·권한 때문에 영수증 저장까지 실패하면 handle 안내가 남지 않을 수 있다.

사본 생성/setup, 자동 감시·완료·채택은 포함하지 않는다. 이후 기존 감시·수집기에 실행 ID를 전달한다. 기존 직접 manual-start/check/accept와 strict 미지원 차단은 유지한다. 실제 적용 범위와 한계는 WO-H70, 문서 감사·실제 실행·최종 판정은 P2 결과 원본(비공개 개발 기록)을 따른다. 소비 적용은 별도 upgrade 근거가 필요하다.

## 6. 작업 종류별 종료 검사 진입점

`python -B harness/finish_work.py --root '<등록 원본>' --run '<execution-ID>' --kind code --completion-note '<근거>' --audit-report 'reports/audit.txt' --lead-note '<비교 근거>' --audit-input '<기준 입력>'`를 사용한다. 종류는 code/doc-audit/doc-edit이며 모르면 수동 확인이다. 기존 Core check_manual과 해당 코드의 step close 차단을 유지하고 문서 작업에 구현자 게이트를 요구하지 않는다. 문서 감사는 변경0, 편집은 기존 승인 경로를 따른다.

문서 검사기가 있으면 실제 실행하고 실패는 차단, 없으면 수동 확인한다. KPI/timing·실제 execution ID 사용량·감사 파일/리드 근거 누락도 안내한다. 명시한 감사 입력은 시작 시점 worker_start_snapshot과만 비교하며 관련성·후속 감사 의미를 자동 판정하지 않는다. `checks_passed`나 CLI0은 완료·채택이 아니고, accept는 리드의 별도 동작이다. `manual_confirmation`은 남은 확인 사항을 JSON에 담고 `blocked`는 nonzero다. 범위·상한/usage 한계·마무리 체크포인트는 WO-H71, 감사·실제 적용·최종 판정은 P3 결과(비공개 개발 기록)를 따른다.

## 7. 명시적 수동 채택 직전의 P3 검사

`python -B harness/execution.py manual-accept --run <실행-ID> --review-note '<리드 검토 근거>'`는 성공한 기존 수동 Core 검사를 먼저 요구하고, 파일 반영 전에 기존 P3 후속 검사를 호출한다. 검사한 후보의 스냅샷·검사 필드를 다시 등록하지 않는다. 등록 역할에 맞는 기존 필수 검사 실패는 채택 전에 차단하며 CLI nonzero다. 코드 종료 검사기의 JSON을 확인할 수 없는 경우도 차단한다.

명시적 채택 성공 응답의 `closure`에는 검사 상태·차단·안내·세부 결과가 남는다. 선택적 계측·감사 참조 누락의 `manual_confirmation`은 리드 확인이 남아 있다는 뜻이며, 구현 완료로 판정하지 않는다. `finish_work` 자체는 채택하지 않는다. 이 연결은 기존 `manual-accept`에 한정되며 strict 채택·직접 파일 반영 등 모든 경로의 통제를 주장하지 않는다. WO-H72와 최종 상태 원본(비공개 개발 기록)을 따른다. 커밋 전 P4 검사는 별도 후속이다.


## 8. 등록·실행·감시·실제 사용량 수집을 연결하는 진입점

AGY·Codex·Claude 작업을 실행하고 관측하려면 `python -B adapters/tools/orca_execution.py manual-run --root '<등록 원본>' --wo <WO> --slice <ID> --role <역할> --base <전체-SHA> --approval-ref '<commit>:<승인-json>#<ID>' --workspace '<준비된 사본>' --worktree 'id:<repo>::<사본경로>' --prompt-file '<지시서>' --note '<리드 확인 근거>' --cap-seconds 600 --round 1`을 사용한다. 선택 인자는 `--interval`·`--transcript-root`이며 상한은 해당 작업의 승인에 맞춰 지정한다. 에이전트는 전달 인자가 아닌 고정 승인과 등록 계약에서 확인한다.

기존 P2 검사를 통과한 뒤 한 번 시작하고, 반환된 정확한 터미널·워크스페이스·실행 ID와 같은 설정의 Orca CLI를 기존 감시기에 전달한다. `manual-run`은 감시 프로세스가 끝날 때까지 기다린다. 감시기의 `--collect-on-exit`은 정상 유휴·터미널 접근 불가에서도 기존 실제 공급자 수집기를 호출한다. cap 경로의 기존 수집·중복 방지와 변경 없는 기본 감시 동작을 유지한다. 필요한 인자·도구·지원 에이전트 확인 실패는 시작 전 차단한다.

결과는 `reports/executions/<실행-ID>/orca-run.json`과 최종 JSON에 남는다. 일치하는 유휴 관측은 `observed`·종료0, 상한 중단 요청/미확인은 `capped`·종료3, 감시 오류·필수 관측 누락·터미널 접근 불가는 `monitoring_unverified`·nonzero다. 시작 실패/불확실 영수증은 기존 상태로 남기며 재시도하지 않는다. 실제 사용량이 없으면 `usage_status=missing`과 안내를 남기고 빈 성공 이벤트를 만들지 않는다. 선택적 사용량 누락만으로 유효한 유휴 관측을 막지는 않는다.

Claude도 같은 P2·감시 경로에서 유휴·상한·오류를 관측한다. 이 경로의 Claude 사용량 수집은 미지원이므로 공급자 로그 검색·파싱을 건너뛰고 `usage_status=missing`과 안내를 남긴다. 다른 에이전트의 로그를 Claude 사용량으로 대체하지 않는다. 필요한 실제 사용량은 리드가 별도 공급자 원본과 대조하며, 이 수동 회수가 자동 수집 지원을 뜻하지 않는다.

`runner_wall`은 시작 전 검사부터 결과 기록까지의 실행기 구간이고, 기존 `watcher_wall`·`transcript_span`·미상의 모델 활동 시간과 다르다. 감시 상한의 중단 요청은 정확한 프로세스 종료 보증이 아니다. 모든 결과의 `requires_manual_confirmation=true`는 유휴·상한·종료가 구현 완료나 채택이 아니라는 뜻이다. 완료 근거·기존 검사·명시적 리드 채택은 계속 필요하다.

강제 프로세스 종료 뒤의 감시 지속, 직접 Orca 호출·기존 `manual-launch`·strict 경로 등 모든 실행의 자동 관측을 보장하지 않는다. 기존 `manual-launch`는 §5의 시작 전용 경로로 남는다. 공유 훅 설치·커밋·소비 적용 상태는 WO-H73과 최종 판정 원본(비공개 개발 기록)에서 구분한다. P4~P7·검토 예산 강제·H3 근거 연결은 별도 후속이다.

## 9. 독립 작업의 병렬 운용

서로 의존하지 않는 작업은 각각 깨끗한 등록 원본과 Orca 격리 워크트리·승인·실행 ID를 준비해 동시에 운용할 수 있다. 각 실행은 기존 P2와 감시 상한을 유지하고, 결과·검사·명시적 채택은 해당 원본에서 수행한다. 공통 원본의 HEAD·역할·신뢰 디렉터리가 바뀌면 다른 실행의 드리프트 검사가 차단할 수 있으므로, 파일이 다르다는 이유만으로 독립 채택이 보장되지는 않는다. 최종 루트 통합은 리드가 맡는다. 선행 결과가 필요 없는 부분은 준비되는 대로 시작하고, 선행 결과가 필요한 부분만 기다린다. 공유 상태·계약 변경이 얽혀 있거나 분할·통합 비용이 더 크면 순차로 처리하며, 파일 수 같은 수치로 병렬화를 강제하지 않는다.

자동 `touches` 스케줄러·새 예산 원장·자동 의미 판정은 이 운용의 선행 조건이 아니다. 먼저 실제 독립 일감에서 중첩·결과·개입·사용량을 확인한다. 병렬 실행 중첩과 시간 합계만으로 순차 실행 대비 속도 향상이나 품질 동등성을 입증했다고 하지 않는다. WO-H74와 실행·판정 원본(비공개 개발 기록)에서 후보·루트 채택·커밋·실운영 설치·소비 적용을 구분한다.
