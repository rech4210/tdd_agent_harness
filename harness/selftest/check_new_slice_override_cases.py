"""하네스 자기 검사: new_slice 초안의 게이트 덮어쓰기 블록 (WO-H53, 독립 일감 F5).

배경: 시험 저장소의 실제 슬라이스 설정에는 override.G2(테스트 범위) · G3(커버리지 기준) 블록이 있는데 초안에는 없어 리드가
채운다(JUDGE-wo-h46 관찰).

계약
- 어댑터 함수 `slice_override_draft(files: list[str], tests: list[str]) -> dict` (Core 는 도구 이름을 모른다). python 어댑터는
  {"G2": {"cmd": ["python", "-m", "pytest", "-q", *tests, "--cov=<패키지>", "--cov-branch",
                  "--cov-report=json:reports/coverage.json", "--hypothesis-seed=0", "--junit-xml=reports/junit.xml"]},
   "G3": {"min_branch_percent": {f: 100 for f in files}, "min_function_percent": {f: 100 for f in files}}}
  <패키지> = files[0] 이 `src/<패키지>/...` 이면 그 이름, 아니면 files[0] 의 첫 폴더.
- `harness/new_slice.py` slices 절 = 기존 블록 뒤에 `[slice.<ID>.override.<게이트>]` (중첩은 `[slice.<ID>.override.G3.min_branch_percent]`
  처럼) 표로 적는다. 기존 config/slices.toml 에 붙여도 TOML 로 읽히고, 읽은 값이 어댑터 결과와 같다.
- 기존 new_slice 계약(check_new_slice_cases.py)은 그대로.

사용: python harness/selftest/check_new_slice_override_cases.py
"""

from __future__ import annotations

import importlib
import shutil
import sys
import tempfile
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_kpi_cases import ROOT, make_project  # noqa: E402
from check_new_slice_cases import ARGS, ASSIGN, SLICES, run, sections  # noqa: E402


def main() -> int:
    rows: list[tuple[str, bool, str]] = []

    def case(name: str, good: bool, detail: str = "") -> None:
        rows.append((name, bool(good), detail[:300]))

    sys.path.insert(0, str(ROOT))
    ad = importlib.import_module("adapters.python.adapter")
    fn = getattr(ad, "slice_override_draft", None)
    files = ["src/p/expense.py", "src/p/ledger.py"]
    tests = ["tests/unit/test_expense.py"]
    got = fn(files, tests) if fn else {}
    g2 = got.get("G2", {}).get("cmd", [])
    case("O01 어댑터 G2.cmd: 테스트 경로 · --cov=p · --junit-xml=reports/junit.xml · --hypothesis-seed=0",
         bool(fn) and g2[:4] == ["python", "-m", "pytest", "-q"] and "tests/unit/test_expense.py" in g2 and "--cov=p" in g2
         and "--junit-xml=reports/junit.xml" in g2 and "--hypothesis-seed=0" in g2, str(g2))
    g3 = got.get("G3", {})
    case("O02 어댑터 G3: 파일마다 branch · function 100", g3.get("min_branch_percent") == {f: 100 for f in files}
         and g3.get("min_function_percent") == {f: 100 for f in files}, str(g3))
    other = fn(["lib/core.py"], tests) if fn else {}
    case("O03 src/ 밖이면 첫 폴더가 패키지 (--cov=lib)", "--cov=lib" in other.get("G2", {}).get("cmd", []), str(other)[:120])

    base = Path(tempfile.mkdtemp(prefix="nsover-"))
    try:
        p = make_project(base, "p")
        (p / "config" / "slices.toml").write_text(SLICES, encoding="utf-8", newline="\n")
        (p / "config" / "assignments.toml").write_text(ASSIGN, encoding="utf-8", newline="\n")
        r = run(p, *ARGS)
        sl = sections(r.stdout).get("config/slices.toml", "")
        try:
            parsed = tomllib.loads(SLICES + "\n" + sl)["slice"]["WO-61"]
        except Exception as err:
            parsed = {"_err": str(err)}
        ov = parsed.get("override", {})
        case("O04 slices 절에 override.G2 · G3 표 — TOML 로 읽히고 어댑터 결과와 같음",
             r.returncode == 0 and ov.get("G2") == got.get("G2") and ov.get("G3") == got.get("G3"), str(ov)[:200] + str(parsed.get("_err", "")))
        case("O05 표 머리가 [slice.WO-61.override.G2] · [slice.WO-61.override.G3.min_branch_percent] 형식",
             "[slice.WO-61.override.G2]" in sl and "[slice.WO-61.override.G3.min_branch_percent]" in sl, sl[-300:])
    finally:
        shutil.rmtree(base, ignore_errors=True)
    ok = all(g for _, g, _ in rows)
    for n, g, d in rows:
        print(f"{'OK ' if g else 'BAD'} {n}{('  — ' + d) if d and not g else ''}")
    print("결과: 모든 사례가 기대와 일치" if ok else "결과: 기대와 다른 사례가 있다")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
