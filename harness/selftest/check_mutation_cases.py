"""H-03: 최소 mutmut 통계 자료를 실제 어댑터와 G6에 통과시킨다."""
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "harness"), str(ROOT)]
import run_gates
from adapters.python import adapter


def main():
    rows = []
    with tempfile.TemporaryDirectory(prefix="mutation-case-") as tmp:
        root = Path(tmp)
        gate = {"run": ["mutmut", "run"], "stats": ["mutmut", "export-cicd-stats"], "stats_file": "stats.json", "min_score": 1.0}
        for cid, counts, expected in [
            ("H03-01", {"total": 3, "killed": 0, "survived": 0, "timeout": 0, "no_tests": 0}, "baseline"),
            ("H03-R01", {"total": 0, "killed": 0, "survived": 0, "timeout": 0, "no_tests": 0}, "zero"),
            ("H03-R02", {"total": 3, "killed": 3, "survived": 0, "timeout": 0, "no_tests": 0}, "normal"),
        ]:
            (root / "stats.json").write_text(json.dumps(counts), encoding="utf-8")
            # 외부 변이 도구 실행만 대체; 파싱과 G6 판정은 실제 코드.
            with patch.object(adapter, "ROOT", root), patch.object(run_gates, "ADAPTER", adapter), patch.object(run_gates, "run", return_value=(0, "")):
                ok, detail = run_gates.mutation_gate(gate)
            good = (not ok and "변이 전 테스트가 실패" in detail and "G2" in detail and "대상 경로" not in detail) if expected == "baseline" else (not ok and detail == "변이가 0개다. 대상 경로 설정을 확인할 것") if expected == "zero" else ok
            rows.append((cid, good, detail))
    for cid, good, detail in rows:
        print(f"{'OK ' if good else 'BAD'} {cid} — {detail}")
    good = all(x[1] for x in rows)
    print("결과: 모든 사례가 기대와 일치" if good else "결과: 기대와 다른 사례가 있다")
    return 0 if good else 1

if __name__ == "__main__":
    sys.exit(main())
