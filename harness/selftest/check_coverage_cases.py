"""하네스 자기 검사: 커버리지 게이트(G3, run_gates.coverage_gate)와 Python 어댑터의 함수 커버리지 사례 (F-1).

F-1: 분기가 모두 덮인 파일 안의 한 번도 실행되지 않은 함수가 G3 를 통과한다. 함수 커버리지는 **선택**(min_function_percent 가
있을 때만)이다 — 설정이 없는 기존 프로젝트의 판정은 바뀌지 않는다 (S1).

사용: python harness/selftest/check_coverage_cases.py
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "harness"), str(ROOT)]
import run_gates  # noqa: E402  (모듈을 불러오기만 한다. 게이트를 실행하지 않는다)
from adapters.python import adapter as py_adapter  # noqa: E402


def fn(stmts: int, covered: int) -> dict:
    return {"summary": {"num_statements": stmts, "covered_lines": covered, "num_branches": 0, "covered_branches": 0}}


def report(functions: dict | None) -> dict:
    """coverage.py 7.x JSON 모양. 분기 2개는 모두 덮였다."""
    entry = {"summary": {"num_branches": 2, "covered_branches": 2, "num_statements": 10, "covered_lines": 8}}
    if functions is not None:
        entry["functions"] = functions
    return {"files": {"src/demo/money.py": entry}}


ALL_RUN = {"": fn(3, 3), "Money.__post_init__": fn(3, 3), "Money.__add__": fn(2, 2)}
ONE_DEAD = {"": fn(3, 3), "Money.__post_init__": fn(3, 3), "stamp": fn(2, 0)}


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="cov-case-"))
    rows: list[tuple[str, bool, str]] = []
    try:
        def gate(rep: dict, **extra) -> tuple[bool, str]:
            path = tmp / "coverage.json"
            path.write_text(json.dumps(rep), encoding="utf-8")
            return run_gates.coverage_gate({"report": str(path), "min_branch_percent": {"src/demo": 100}, **extra})

        ok, d = gate(report(ONE_DEAD), min_function_percent={"src/demo": 100})
        rows.append(("C01 실행되지 않은 함수 + min_function_percent → 실패", not ok and "함수" in d, d.replace("\n", " | ")[:110]))
        ok, d = gate(report(ONE_DEAD))
        rows.append(("C02 min_function_percent 가 없으면 지금처럼 분기만 (기존 판정 불변)", ok, d.replace("\n", " | ")[:110]))
        ok, d = gate(report(ALL_RUN), min_function_percent={"src/demo": 100})
        rows.append(("C03 모든 함수 실행 → 통과, 함수 줄이 보고된다", ok and "함수 100.0%" in d, d.replace("\n", " | ")[:110]))
        ok, d = gate(report(None), min_function_percent={"src/demo": 100})
        rows.append(("C04 함수 측정이 없는데 min_function_percent 를 요구 → 실패 (공허한 참 방지)", not ok and "함수" in d,
                     d.replace("\n", " | ")[:110]))
        ok, d = gate(report(ALL_RUN), min_function_percent={"src/other": 100})
        rows.append(("C05 min_function_percent 의 접두가 아무 파일과도 맞지 않으면 실패", not ok, d.replace("\n", " | ")[:110]))

        # WO-H09 레드팀에서 나온 정책 공백 (HD-11 · HD-12)
        def two(a: str, fa: dict | None, b: str, fb: dict | None) -> dict:
            r = {"files": {}}
            for name, f in ((a, fa), (b, fb)):
                e = {"summary": {"num_branches": 2, "covered_branches": 2, "num_statements": 10, "covered_lines": 10}}
                if f is not None:
                    e["functions"] = f
                r["files"][name] = e
            return r
        ok, d = gate(two("src/demox/a.py", ALL_RUN, "src/other/b.py", ALL_RUN), min_branch_percent={"src/demo": 100})
        rows.append(("C08 HD-11 접두는 경로 경계: 'src/demo' 가 'src/demox' 를 측정으로 쓰지 않는다 (분기)", not ok,
                     d.replace("\n", " | ")[:110]))
        ok, d = gate(two("src/demo/a.py", ALL_RUN, "src/demox/b.py", ONE_DEAD), min_function_percent={"src/demo": 100})
        rows.append(("C09 HD-11 'src/demox' 의 미실행 함수가 'src/demo' 판정을 낮추지 않는다 (함수)", ok, d.replace("\n", " | ")[:110]))
        ok, d = gate(two("src/demo/a.py", ALL_RUN, "src/demo/b.py", None), min_function_percent={"src/demo": 100})
        rows.append(("C10 HD-12 함수 기준이 있으면 맞는 파일마다 함수 측정이 있어야 한다 (일부 누락 → 실패)", not ok
                     and "b.py" in d, d.replace("\n", " | ")[:110]))
        ok, d = gate(two("src/demo/a.py", ALL_RUN, "src/demo/c.py", {"": fn(2, 2)}), min_function_percent={"src/demo": 100})
        rows.append(("C11 HD-12 함수 0개(모듈 수준만)로 측정된 상수 파일은 누락이 아니다", ok, d.replace("\n", " | ")[:110]))
        ok, d = gate(two("src/demo/a.py", ALL_RUN, "src/demo/b.py", ALL_RUN), min_branch_percent={"src/demo/a.py": 100})
        rows.append(("C12 파일 경로를 그대로 쓴 접두는 그 파일만 (기존 슬라이스 설정 호환)", ok, d.replace("\n", " | ")[:110]))

        path = tmp / "c.json"
        path.write_text(json.dumps(report(ONE_DEAD)), encoding="utf-8")
        norm = py_adapter.read_coverage(path)["src/demo/money.py"]
        rows.append(("C06 Python 어댑터: 모듈 수준('')을 빼고 함수 수 · 실행된 함수 수", norm.get("functions_total") == 2
                     and norm.get("functions_covered") == 1, str({k: v for k, v in norm.items() if k.startswith("functions")})))
        path.write_text(json.dumps(report(None)), encoding="utf-8")
        norm = py_adapter.read_coverage(path)["src/demo/money.py"]
        rows.append(("C07 Python 어댑터: functions 가 없는 보고서는 함수 키를 내지 않는다", "functions_total" not in norm, str(norm)))
        # H-02: 실제 생성 설정과 최소 coverage JSON.
        import re
        import tomllib
        generated = tmp / "generated"
        py_adapter.scaffold(generated, "sample")
        patterns = tomllib.loads((generated / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["coverage"]["report"]["exclude_lines"]
        declaration = "def sample() -> tuple[int, ...]:"
        rows.append(("H02-01 tuple 반환형은 제외하지 않고 독립 ellipsis 줄만 제외", r"^\s*\.\.\.\s*$" in patterns and not any(re.search(p, declaration) for p in patterns) and any(re.search(p, "    ...") for p in patterns), repr(patterns)))
        excluded = fn(0, 0)
        excluded.update(executed_lines=[], missing_lines=[], excluded_lines=[5, 6])
        rep = report({"": fn(1, 1), "active": fn(1, 1), "empty_zero": fn(0, 0), "excluded_stub": excluded})
        rep["files"]["src/demo/sample.py"] = rep["files"].pop("src/demo/money.py")
        path = tmp / "excluded.json"
        path.write_text(json.dumps(rep), encoding="utf-8")
        norm = py_adapter.read_coverage(path)["src/demo/sample.py"]
        rows.append(("H02-02 문장 0개 제외 함수는 안 덮인 함수로 세지 않음", norm.get("functions_total") == 1 and norm.get("functions_covered") == 1, str(norm)))
        ok, d = gate(rep, min_function_percent={"src/demo": 100})
        rows.append(("H02-03 G3 통과 + 제외로 측정 안 된 함수 이름 공개", ok and "제외로 측정 안 된 함수" in d and "excluded_stub" in d, d.replace("\n", " | ")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    all_good = True
    for name, good, detail in rows:
        all_good &= good
        print(f"{'OK ' if good else 'BAD'} {name}  — {detail}")
    print("결과: 모든 사례가 기대와 일치" if all_good else "결과: 기대와 다른 사례가 있다")
    return 0 if all_good else 1


if __name__ == "__main__":
    sys.exit(main())
