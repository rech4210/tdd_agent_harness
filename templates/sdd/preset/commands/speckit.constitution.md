---
description: Constitution is strictly generated from config/*.toml by the harness. Agent modification is blocked.
strategy: replace
---

# 헌법(Constitution) 직접 생성/수정 차단 안내

이 프로젝트에서 에이전트 행동 규칙과 거버넌스 헌법의 유일한 권위(Single Source of Truth)는 `config/*.toml` (`config/gates.toml`, `config/roles.toml` 등)입니다.

에이전트는 헌법 파일(`.specify/memory/constitution.md`)을 직접 생성하거나 수정할 수 없습니다.

## 실행 중단 및 안내

1. 에이전트는 헌법 파일을 작성하거나 수정하지 않고 즉시 작업을 멈춥니다.
2. 헌법 파일 생성 및 동기화가 필요하다면 사람(개발자)이 저장소 루트에서 다음 명령을 직접 실행해야 합니다:
   ```sh
   python harness/constitution.py --write
   ```
3. 헌법 규칙을 변경하려면 사람(개발자)이 `config/*.toml` 설정을 직접 수정한 후(H10), 위 명령으로 헌법을 다시 생성해야 합니다.
4. 하네스 게이트 `GC` (`python harness/constitution.py --check`)가 헌법 파일과 설정의 일치 여부를 판정합니다.
