"""게으른 구현 후보 회귀: 정상 빌드는 통과하고, 모든 후보는 테스트 실패로 잡혀야 한다.

guards:      (검증 장치) CASES 의 테스트 파일들이 지키는 <INV-xx>, <D-xx>
technique:   10 게으른 구현 검증 절차
checklist:   B-9

놓는 곳 (예):
  regression/run_lazy_candidates.py         이 파일
  regression/import_probe.py                import 확인 플러그인
  regression/lazy_build/src/<package>/...   검증 빌드 (LAZY 스위치, lazy_switch_example.py 참고)

사용: python regression/run_lazy_candidates.py
종료 코드: 0 = 모든 행이 기대와 일치, 1 = 불일치 (방어가 깨졌거나, 정상 빌드가 실패했거나, 검증 빌드가 import 되지 않음)

행마다 확인하는 것
  1. 기대한 결과인가: 정상 = 종료 코드 0, 후보 = 종료 코드 1 (테스트 실패. 시간 초과 포함)
     2 (중단) · 3 (내부 오류) · 4 (사용 오류) · 5 (수집된 테스트 없음) 은 "잡힘"이 아니다
  2. 검증 빌드를 import 했는가: 설정 파일의 pythonpath 가 PYTHONPATH 보다 앞서 저장소의 실제 구현을
     import 할 수 있다. 그러면 모든 후보가 '통과'로 보인다 (공허한 참). 명령줄에서 덮어쓰고 행마다 확인한다
  3. 후보 ID 가 검증 빌드에 실제로 있는가: 오타 난 ID 는 정상 빌드처럼 동작해 '못 잡음'으로 오해된다

필요: pytest, hypothesis, pytest-timeout
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # 저장소 루트 (이 파일이 regression/ 에 있을 때)
PROBE_DIR = Path(__file__).resolve().parent
BUILD = PROBE_DIR / "lazy_build" / "src"  # 검증 빌드
PACKAGE = "<package>"  # [사람 결정] 대상 패키지 이름

# [사람 결정] 게이트와 같은 환경 변수 (게이트 [env] 를 그대로 옮긴다)
GATE_ENV: dict[str, str] = {}
TIMEOUT_SECONDS = 60  # 테스트 단위 시간 제한. 끝나지 않는 후보를 실패로 만든다

# 첫 행은 정상 빌드("")다. 후보 ID 는 검증 빌드의 LAZY 분기와 같아야 한다
CASES: list[tuple[str, str]] = [
    ("", "<tests/unit/test_target.py>"),
    ("<후보 ID 1>", "<tests/unit/test_target.py>"),
    ("<후보 ID 2>", "<tests/unit/test_target.py>"),
]


def candidate_exists(lazy: str) -> bool:
    pattern = re.compile(r"LAZY\s*[!=]=\s*[\"']" + re.escape(lazy) + r"[\"']")
    return any(pattern.search(p.read_text(encoding="utf-8")) for p in BUILD.rglob("*.py"))


def run(lazy: str, test: str, probe_out: Path) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        **GATE_ENV,
        "LAZY": lazy,
        "PROBE_PACKAGE": PACKAGE,
        "PROBE_OUT": str(probe_out),
        "PYTHONPATH": os.pathsep.join([str(BUILD), str(PROBE_DIR)]),
    }
    return subprocess.run(
        [
            sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "import_probe", test,
            "--hypothesis-seed=0", f"--timeout={TIMEOUT_SECONDS}",
            # 설정 파일의 pythonpath 를 명령줄에서 덮어쓴다. 검증 빌드를 맨 앞에, '.' 은 tests 패키지 import 용
            "-o", f"pythonpath={BUILD.as_posix()} .",
        ],
        cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def main() -> int:
    if not CASES or CASES[0][0] != "" or len(CASES) < 2:
        print("BAD CASES: 첫 행은 정상 빌드, 후보가 1개 이상 있어야 한다")
        return 1
    ok = True
    for lazy, test in CASES:
        if lazy and not candidate_exists(lazy):
            print(f"BAD {lazy:10} 검증 빌드에 이 후보의 LAZY 분기가 없다")
            ok = False
            continue
        fd, name = tempfile.mkstemp(prefix="import_probe_")  # 저장소 밖 (G0 가 새 파일로 집계하지 않게)
        os.close(fd)
        probe_out = Path(name)
        try:
            p = run(lazy, test, probe_out)
            imported = probe_out.read_text(encoding="utf-8").strip() or "None"
        finally:
            probe_out.unlink(missing_ok=True)
        from_build = imported != "None" and Path(imported).resolve().is_relative_to(BUILD.resolve())
        expected = 0 if lazy == "" else 1
        good = p.returncode == expected and from_build
        ok &= good
        tail = (p.stdout.strip().splitlines() or [""])[-1][:70]
        print(f"{'OK ' if good else 'BAD'} {lazy or '정상':10} exit={p.returncode} build={from_build} {test}  {tail}")
        if not from_build:
            print(f"    import 대상: {imported}  (검증 빌드 {BUILD} 가 아니다)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
