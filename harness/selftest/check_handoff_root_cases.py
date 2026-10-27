"""하네스 자기 검사: 상태 줄 검사의 기준 저장소 (WO-H54, 독립 일감 F6).

배경: check_handoff_state.py 는 실행한 폴더의 git 저장소를 기준으로 삼는다. 다른 도구(adapters/tools/*)는 자기 저장소를
기준으로 한다(WO-H40 원칙). 다른 저장소 안에서 실행하면 엉뚱한 HANDOFF 를 비교한다(JUDGE-wo-h48 관찰).

계약 (adapters/tools/check_handoff_state.py)
- 기준 저장소 = 도구 파일이 있는 저장소(`<도구>/../..`). 실행 폴더(cwd)와 무관하게 같은 결과.
- 그 밖의 계약(check_doc_roles_cases.py 의 H · B09 ~ B12)은 그대로.

사용: python harness/selftest/check_handoff_root_cases.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_kpi_cases import PY, make_project  # noqa: E402


def g(p: Path, *a: str) -> None:
    subprocess.run(["git", *a], cwd=p, capture_output=True, text=True, check=True)


def main() -> int:
    rows: list[tuple[str, bool, str]] = []

    def case(name: str, good: bool, detail: str = "") -> None:
        rows.append((name, bool(good), detail[:300]))

    base = Path(tempfile.mkdtemp(prefix="hroot-"))
    try:
        p = make_project(base, "own")
        (p / "docs").mkdir(exist_ok=True)
        (p / "docs" / "HANDOFF.md").write_text("상태: 1단계\n", encoding="utf-8")
        g(p, "add", "-A")
        g(p, "commit", "-qm", "h")
        other = make_project(base, "other")
        (other / "docs").mkdir(exist_ok=True)
        (other / "docs" / "HANDOFF.md").write_text("상태: A\n", encoding="utf-8")
        g(other, "add", "-A")
        g(other, "commit", "-qm", "h")
        (other / "docs" / "HANDOFF.md").write_text("상태: B\n", encoding="utf-8")   # 다른 저장소에서는 상태 줄이 바뀜
        tool = str(p / "adapters" / "tools" / "check_handoff_state.py")

        def run_in(cwd: Path) -> int:
            return subprocess.run([PY, tool, "--base", "HEAD"], cwd=cwd, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace").returncode

        case("C01 자기 저장소에서 실행 → 0 (상태 줄 그대로)", run_in(p) == 0, "")
        rc_other = run_in(other)
        case("C02 다른 저장소 안에서 실행해도 자기 저장소 기준 → 0 (other 의 바뀐 상태 줄을 보지 않음)", rc_other == 0, str(rc_other))
        rc_out = run_in(base)
        case("C03 git 저장소 밖에서 실행해도 자기 저장소 기준 → 0", rc_out == 0, str(rc_out))
        (p / "docs" / "HANDOFF.md").write_text("상태: 2단계\n", encoding="utf-8")
        case("C04 자기 저장소의 상태 줄이 바뀌면 어디서 실행해도 1", run_in(other) == 1 and run_in(p) == 1, "")
    finally:
        shutil.rmtree(base, ignore_errors=True)
    ok = all(g for _, g, _ in rows)
    for n, gd, d in rows:
        print(f"{'OK ' if gd else 'BAD'} {n}{('  — ' + d) if d and not gd else ''}")
    print("결과: 모든 사례가 기대와 일치" if ok else "결과: 기대와 다른 사례가 있다")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
