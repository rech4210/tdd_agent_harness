"""하네스 자기 검사: KPI 집계의 결정성과 회차 원인 귀속 (프로젝트 인계 S04, WO-H16).

- 같은 이벤트로 KPI 를 다시 만들면 같은 표가 나와야 한다. "실패가 가장 많은 게이트"가 동률일 때
  집합 순회 순서(PYTHONHASHSEED)로 고르면 실행마다 답이 바뀐다 (T01 · T02).
- max_rounds 는 구현자 원인 회차만 센다 (H-04). 구현자가 아닌 역할의 판정 실행은 원인 표시를 빠뜨려도
  구현자 회차를 쓰지 않아야 한다 (C01 ~ C03). C04 · C05 는 H-04 의미를 지키는 회귀 방지 사례다.

임시 git 저장소에 harness/ 를 복사하고 run_gates.py · kpi.py 를 실제로 실행한다.
사용: python harness/selftest/check_kpi_attribution_cases.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_kpi_cases import PY, ev, events, kpi_rows, make_project, write_events  # noqa: E402

SEEDS = [str(s) for s in range(8)]
FAILING_GATE = '[harness]\nadapter = "python"\n[[gate]]\nid = "G2"\nname = "minimal"\ncmd = ["python", "-c", "raise SystemExit(1)"]\n'
CAUSE_COLS = ["구현자 원인 회차", "테스트 · 재잠금 원인 회차", "max_rounds 산정 회차"]


def kpi_text(proj: Path, seed: str) -> str:
    env = dict(os.environ, PYTHONHASHSEED=seed)
    r = subprocess.run([PY, "harness/kpi.py"], cwd=proj, capture_output=True, text=True, env=env)
    if r.returncode != 0:
        raise RuntimeError(f"kpi.py 실패: {r.stderr.strip()[-300:]}")
    return (proj / "reports" / "kpi.md").read_text(encoding="utf-8")


def most_failed(text: str, wo: str) -> str:
    lines = [l for l in text.splitlines() if l.startswith("| ")]
    head = [c.strip() for c in lines[0].strip("|").split("|")]
    col = head.index("실패가 가장 많은 게이트")
    row = next(l for l in lines if l.startswith(f"| {wo} "))
    return [c.strip() for c in row.strip().strip("|").split("|")][col]


def gate_run(i: int, wo: str, role: str, first_failed: str | None, **extra: object) -> dict:
    return ev(f"2026-10-10T10:{i:02d}:00Z", "gate_run", wo=wo, role=role, passed=False, only=[],
              first_failed=first_failed, g0_violations=0, review_required=[], seconds=1, **extra)


def main() -> int:
    results: list[tuple[str, bool, str]] = []

    def case(name: str, good: bool, detail: str = "") -> None:
        results.append((name, good, detail))

    base = Path(tempfile.mkdtemp(prefix="kpi-attr-"))
    try:
        # T01: 동률(G2 · G3 · G0 각 1회) → 해시 시드와 무관하게 같은 값
        p = make_project(base, "tie")
        tied = ["G2", "G3", "G0"]
        write_events(p, [gate_run(i, "WO-20", "implementer", g, cause="implementer") for i, g in enumerate(tied)])
        seen = {most_failed(kpi_text(p, s), "WO-20") for s in SEEDS}
        case("T01 동률 게이트: PYTHONHASHSEED 0~7 에서 '실패가 가장 많은 게이트' 값이 하나", len(seen) == 1, str(sorted(seen)))

        # T02: 동률이면 동률인 게이트를 모두 보인다 (하나만 고르면 나머지 실패가 표에서 사라진다)
        value = next(iter(seen)) if len(seen) == 1 else ""
        case("T02 동률 게이트는 모두 표시 (G2 · G3 · G0 가 값에 다 있음)",
             len(seen) == 1 and all(g in value for g in tied), value or str(sorted(seen)))

        # T03 (회귀 방지): 최다가 하나면 그 게이트만
        p = make_project(base, "single")
        write_events(p, [gate_run(i, "WO-21", "implementer", g, cause="implementer") for i, g in enumerate(["G3", "G2", "G3"])])
        values = {most_failed(kpi_text(p, s), "WO-21") for s in SEEDS}
        case("T03 최다 게이트가 하나(G3 2회 · G2 1회) → 'G3' (회귀 방지)", values == {"G3"}, str(sorted(values)))

        # C01 · C02: --cause 없이 구현자가 아닌 역할로 판정 → 구현자 원인으로 기록 · 집계되지 않는다
        p = make_project(base, "cause-default")
        (p / "config" / "gates.toml").write_text(FAILING_GATE, encoding="utf-8")
        runs = {role: subprocess.run([PY, "harness/run_gates.py", "--role", role, "--base", "HEAD", "--wo", "WO-22"],
                                     cwd=p, capture_output=True, text=True)
                for role in ("test_author", "spec_author")}
        recorded = [(e.get("role"), e.get("cause")) for e in events(p) if e.get("kind") == "gate_run"]
        # 거부(종료 코드 2, 기록 없음)와 구현자가 아닌 원인으로 기록 둘 다 허용한다. 금지는 "구현자 원인으로 남는 것"뿐
        mislabeled = [r for r in recorded if r[0] != "implementer" and r[1] == "implementer"]
        case("C01 --cause 없는 test_author · spec_author 판정 → 이벤트가 구현자 원인으로 남지 않음",
             not mislabeled and all(r.returncode in (1, 2) for r in runs.values()),
             f"recorded={recorded} exits={[r.returncode for r in runs.values()]}")
        rows = kpi_rows(p)
        w22 = rows.get("WO-22", {})
        case("C02 그 실행들은 max_rounds 산정 회차 · 구현자 원인 회차에 들어가지 않음",
             w22.get("max_rounds 산정 회차", "0") == "0" and w22.get("구현자 원인 회차", "0") == "0", str({c: w22.get(c) for c in CAUSE_COLS}))

        # C03: 이미 기록된 이벤트(역할 test_author, cause implementer)도 구현자 회차에 넣지 않는다
        p = make_project(base, "cause-legacy")
        write_events(p, [gate_run(0, "WO-23", "test_author", "G0", cause="implementer"),
                         gate_run(1, "WO-23", "spec_author", None, cause="implementer"),
                         gate_run(2, "WO-23", "implementer", "G2", cause="implementer")])
        w23 = kpi_rows(p).get("WO-23", {})
        case("C03 기록된 test_author · spec_author 실행(cause=implementer) → max_rounds 산정 1 (구현자 실행만)",
             w23.get("max_rounds 산정 회차") == "1" and w23.get("구현자 원인 회차") == "1", str({c: w23.get(c) for c in CAUSE_COLS}))

        # C04 (회귀 방지, H-04): 구현자 역할의 실행은 표시한 원인대로 센다
        p = make_project(base, "cause-h04")
        write_events(p, [gate_run(i, "WO-24", "implementer", "G2", cause=c)
                         for i, c in enumerate(("implementer", "test", "harness", "implementer"))])
        w24 = kpi_rows(p).get("WO-24", {})
        case("C04 구현자 역할 · 원인 implementer/test/harness/implementer → 구현자 2 · 테스트 1 · 산정 2 (회귀 방지)",
             [w24.get(c) for c in CAUSE_COLS] == ["2", "1", "2"], str({c: w24.get(c) for c in CAUSE_COLS}))

        # C05 (회귀 방지): 구현자 역할에서 --cause 를 빠뜨리면 지금처럼 구현자 원인
        p = make_project(base, "cause-impl-default")
        (p / "config" / "gates.toml").write_text(FAILING_GATE, encoding="utf-8")
        subprocess.run([PY, "harness/run_gates.py", "--role", "implementer", "--base", "HEAD", "--wo", "WO-25"],
                       cwd=p, capture_output=True, text=True)
        recorded = [(e.get("role"), e.get("cause")) for e in events(p) if e.get("kind") == "gate_run"]
        case("C05 --cause 없는 implementer 판정 → cause implementer (회귀 방지)",
             recorded == [("implementer", "implementer")], str(recorded))
    finally:
        shutil.rmtree(base, ignore_errors=True)

    all_good = True
    for name, good, detail in results:
        all_good &= good
        print(f"{'OK ' if good else 'BAD'} {name}{('  — ' + detail) if detail else ''}")
    print("결과: 모든 사례가 기대와 일치" if all_good else "결과: 기대와 다른 사례가 있다")
    return 0 if all_good else 1


if __name__ == "__main__":
    sys.exit(main())
