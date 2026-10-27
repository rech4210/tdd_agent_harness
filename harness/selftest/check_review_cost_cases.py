"""하네스 자기 검사: S5 검토 최적화의 기록 · 집계 (WO-H49, 지시서 §8.1).

배경: 검토 회차마다 “새로 찾은 결함”과 “이전 회차와 겹치는 지적”을 구분하지 않아, 회차를 더 돌릴 가치(새 결함 탐지
실적)를 측정할 수 없다. 위험도별 검토 시간 · 토큰도 표가 없다.

계약
V 기록 (mark.py review)
  `review <검토자> --wo <WO> --round N --defects K [--new-defects M]`
  - --new-defects 는 0 ≤ M ≤ K 정수. 범위 밖이면 종료 2 · 기록 없음. review 밖에서 쓰면 종료 2.
  - review_run 에 new_defects 필드: 주면 M. 안 주면 round ≤ 1 이면 K(첫 회차의 결함은 모두 새 결함), round ≥ 2 이면 None(모름).
  - 기존 필드(reviewer · round · contract_defects · wo)와 기존 오류 규칙은 그대로.
C 집계 (kpi.py) 새 절 “## 검토 비용 (위험도별)” — 표 열:
  위험도 | WO 수 | 검토 회차 | 계약 안 결함 | 새 결함 | 새 결함 모름 | 검토 모델 시간(초) | 검토 입력 토큰 | 검토 출력 토큰
  - 위험도 = 그 WO 의 wo_class 해석값(wo_class_for, 작업 전 우선) 의 risk, 없으면 “미상”. 행 순서 low · medium · high · 미상
    (없는 위험도 행은 생략).
  - 검토 회차 = review_run 수, 계약 안 결함 = contract_defects 합, 새 결함 = new_defects 합(None 제외),
    새 결함 모름 = new_defects 가 None 인 review_run 수.
  - 검토 모델 시간 · 토큰 = model_usage 중 role 이 adversary · reviewer · mutation_auditor 인 기록(run 중복 1번)의 seconds 합,
    tokens.input · tokens.output 합(없으면 0 으로 더하지 않고 건너뜀). 소수 첫째 자리까지.
  - kpi.py 는 이벤트 파일을 바꾸지 않는다.

사용: python harness/selftest/check_review_cost_cases.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_kpi_cases import PY, events, make_project, write_events  # noqa: E402

TITLE = "검토 비용 (위험도별)"


def mark(p: Path, *a: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([PY, "harness/mark.py", *a], cwd=p, capture_output=True, text=True, encoding="utf-8", errors="replace")


def section(p: Path) -> dict[str, dict[str, str]]:
    r = subprocess.run([PY, "harness/kpi.py"], cwd=p, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        return {"_err": {"msg": (r.stdout + r.stderr)[-200:]}}
    text = (p / "reports" / "kpi.md").read_text(encoding="utf-8")
    if f"## {TITLE}" not in text:
        return {}
    sec = text.split(f"## {TITLE}", 1)[1].split("\n## ", 1)[0]
    lines = [l for l in sec.splitlines() if l.startswith("|")]
    if len(lines) < 2:
        return {}
    head = [c.strip() for c in lines[0].strip("|").split("|")]
    rows = [dict(zip(head, [c.strip() for c in l.strip("|").split("|")])) for l in lines[2:]]
    return {r.get("위험도", "?"): r for r in rows}


def cls(wo: str, risk: str) -> dict:
    return {"ts": "2026-10-20T00:00:00Z", "kind": "wo_class", "wo": wo, "tier": "T2", "risk": risk, "reason": "r",
            "basis": "prospective", "confirmed_by": "user"}


def rev(wo: str, rnd: int, k: int, new: int | None) -> dict:
    e = {"ts": "2026-10-20T00:01:00Z", "kind": "review_run", "wo": wo, "reviewer": "codex", "round": rnd, "contract_defects": k}
    if new is not ...:
        e["new_defects"] = new
    return e


def use(wo: str, run: str, role: str, sec: float, i: int, o: int) -> dict:
    return {"ts": "2026-10-20T00:02:00Z", "kind": "model_usage", "wo": wo, "role": role, "agent": "codex", "run_id": run,
            "task": "test-review", "round": 1, "seconds": sec, "time_scope": "model_activity", "time_source": "measured_activity_intervals", "tokens": {"input": i, "output": o, "cache_read": 0,
                                                                        "cache_in_input": True}, "model": "m"}


def main() -> int:
    results: list[tuple[str, bool, str]] = []

    def case(name: str, good: bool, detail: str = "") -> None:
        results.append((name, good, detail))

    base = Path(tempfile.mkdtemp(prefix="revcost-"))
    try:
        p = make_project(base, "v")
        r1 = mark(p, "review", "codex", "--wo", "WO-71", "--round", "1", "--defects", "3")
        r2 = mark(p, "review", "codex", "--wo", "WO-71", "--round", "2", "--defects", "2", "--new-defects", "1")
        r3 = mark(p, "review", "codex", "--wo", "WO-71", "--round", "3", "--defects", "1")
        rr = [e for e in events(p) if e.get("kind") == "review_run"]
        case("V01 round 1 · --new-defects 없음 → new_defects = defects (3)",
             r1.returncode == 0 and len(rr) >= 1 and rr[0].get("new_defects") == 3, str(rr[:1]))
        case("V02 round 2 · --new-defects 1 → 1, 기존 필드 유지",
             r2.returncode == 0 and len(rr) >= 2 and rr[1].get("new_defects") == 1 and rr[1].get("contract_defects") == 2
             and rr[1].get("round") == 2 and rr[1].get("reviewer") == "codex", str(rr[1:2]))
        case("V03 round 3 · --new-defects 없음 → new_defects 필드는 None (모름)",
             r3.returncode == 0 and len(rr) >= 3 and "new_defects" in rr[2] and rr[2]["new_defects"] is None, str(rr[2:3]))
        before = len(events(p))
        bad = [mark(p, "review", "codex", "--wo", "WO-71", "--round", "2", "--defects", "1", "--new-defects", "2").returncode,
               mark(p, "review", "codex", "--wo", "WO-71", "--round", "2", "--defects", "1", "--new-defects", "-1").returncode,
               mark(p, "note", "m", "--wo", "WO-71", "--new-defects", "0").returncode]
        case("V04 new > defects · 음수 · review 밖 사용 → 종료 2 · 기록 없음", bad == [2, 2, 2] and len(events(p)) == before, str(bad))

        q = make_project(base, "c")
        write_events(q, [
            cls("WO-72", "low"), cls("WO-73", "low"), cls("WO-74", "medium"),
            rev("WO-72", 1, 2, 2), rev("WO-73", 1, 0, 0), rev("WO-74", 1, 4, 4), rev("WO-74", 2, 3, 1), rev("WO-74", 3, 1, None),
            rev("WO-75", 1, 1, ...),                                       # 분류 없음 · new_defects 필드 없음(옛 기록)
            use("WO-72", "a", "adversary", 100.0, 1000, 10), use("WO-72", "a", "adversary", 100.0, 1000, 10),   # 중복 run
            use("WO-74", "b", "adversary", 300.5, 5000, 50), use("WO-74", "c", "implementer", 999.0, 9, 9),
            use("WO-75", "d", "reviewer", 20.0, 200, 2),
        ])
        s = section(q)
        low, med, unk = s.get("low", {}), s.get("medium", {}), s.get("미상", {})
        case("C01 절 · 위험도 행: low · medium · 미상 (high 없음 → 생략), 순서 low → medium → 미상",
             list(s) == ["low", "medium", "미상"], str(list(s)) + str(s.get("_err", "")))
        case("C02 low: WO 2 · 회차 2 · 결함 2 · 새 결함 2 · 모름 0",
             [low.get(k) for k in ("WO 수", "검토 회차", "계약 안 결함", "새 결함", "새 결함 모름")] == ["2", "2", "2", "2", "0"], str(low))
        case("C03 medium: WO 1 · 회차 3 · 결함 8 · 새 결함 5 · 모름 1",
             [med.get(k) for k in ("WO 수", "검토 회차", "계약 안 결함", "새 결함", "새 결함 모름")] == ["1", "3", "8", "5", "1"], str(med))
        case("C04 검토 모델 시간 · 토큰: low 100.0 · 1000 · 10 (중복 run 1번), medium 300.5 · 5000 · 50 (구현 기록 제외)",
             [low.get("검토 모델 시간(초)"), low.get("검토 입력 토큰"), low.get("검토 출력 토큰")] == ["100.0", "1000", "10"]
             and [med.get("검토 모델 시간(초)"), med.get("검토 입력 토큰"), med.get("검토 출력 토큰")] == ["300.5", "5000", "50"],
             f"{low} {med}")
        case("C05 미상: 분류 없는 WO · 옛 기록(new_defects 필드 없음 → 모름 1) · reviewer 사용량 20.0",
             [unk.get(k) for k in ("WO 수", "검토 회차", "새 결함", "새 결함 모름", "검토 모델 시간(초)")] == ["1", "1", "0", "1", "20.0"],
             str(unk))
    finally:
        shutil.rmtree(base, ignore_errors=True)

    all_good = True
    for name, good, detail in results:
        all_good &= good
        print(f"{'OK ' if good else 'BAD'} {name}{('  — ' + detail) if detail and not good else ''}")
    print("결과: 모든 사례가 기대와 일치" if all_good else "결과: 기대와 다른 사례가 있다")
    return 0 if all_good else 1


if __name__ == "__main__":
    sys.exit(main())
