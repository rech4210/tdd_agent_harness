"""pytest 플러그인: 테스트가 실제로 import 한 대상 패키지의 경로를 기록한다 (검증 장치의 공허한 참 방지).

guards:      (검증 장치) B-9
technique:   10 게으른 구현 검증 절차, 단계 4 (import 경로 고정 · 확인)
checklist:   B-9

놓는 곳: regression/import_probe.py. 실행기가 `-p import_probe`로 켠다. 테스트 파일은 고치지 않는다 (잠금 대상).
환경 변수: PROBE_PACKAGE = 대상 패키지 이름, PROBE_OUT = 기록할 파일 경로 (저장소 밖 임시 파일).

수집 직후에 파일로 기록한다. 시간 제한 플러그인이 스레드 방식(Windows 기본)으로 프로세스를 바로 끝내면
끝에서 출력하는 확인은 사라진다. 그러면 시간 초과로 잡힌 후보가 "검증 빌드 아님"으로 잘못 보인다.
테스트 안에서 늦게 import 하는 경우를 위해 끝에서 한 번 더 기록한다.
"""

import os
import sys
from pathlib import Path


def _record() -> None:
    out = os.environ.get("PROBE_OUT")
    if not out:
        return
    mod = sys.modules.get(os.environ.get("PROBE_PACKAGE", ""))
    Path(out).write_text(str(getattr(mod, "__file__", None)), encoding="utf-8")


def pytest_collection_finish(session):
    _record()


def pytest_terminal_summary(terminalreporter):
    _record()
