"""하네스 자기 검사: 사람 개입 시간 기록(mark.py)과 KPI 집계(kpi.py) 사례 (HD-16).

사람 시간은 두 가지로 잰다.
- 자동 대기 시간: lead(Claude)가 사람 판단이 필요한 지점에서 멈출 때 `mark.py wait-start`, 답을 받은 직후 `wait-end`
  (시각은 명령이 찍는다). 값은 "답이 오기까지 걸린 시간"이라 사람 시간의 상한이다. 막힌 지점 수 · 결정 수도 함께 센다.
- 사람이 찍는 판단 시간: 기존 `mark.py start/end` (보조, 그대로).

임시 git 저장소에 harness/ 를 복사하고 mark.py · kpi.py 를 실제로 실행한다.
사용: python harness/selftest/check_kpi_cases.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable


def make_project(base: Path, name: str) -> Path:
    proj = base / name
    shutil.copytree(ROOT / "harness", proj / "harness",
                    ignore=shutil.ignore_patterns("__pycache__", "selftest"))
    shutil.copytree(ROOT / "adapters", proj / "adapters", ignore=shutil.ignore_patterns("__pycache__"))
    (proj / "config").mkdir(parents=True)
    (proj / "config" / "gates.toml").write_text('[harness]\nadapter = "python"\n', encoding="utf-8", newline="\n")
    (proj / "reports").mkdir(parents=True)
    for cmd in (["git", "init", "-q", "-b", "main"], ["git", "config", "user.email", "t@example.invalid"],
                ["git", "config", "user.name", "t"], ["git", "add", "-A"], ["git", "commit", "-qm", "base"]):
        subprocess.run(cmd, cwd=proj, capture_output=True, text=True, check=True)
    return proj


def mark(proj: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([PY, "harness/mark.py", *args], cwd=proj, capture_output=True, text=True)


def events(proj: Path) -> list[dict]:
    p = proj / "reports" / "events.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def write_events(proj: Path, evs: list[dict]) -> None:
    lines = [json.dumps({"head": "abc1234", "branch": "main", **e}, ensure_ascii=False) for e in evs]
    (proj / "reports" / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def kpi_rows(proj: Path) -> dict[str, dict[str, str]]:
    """kpi.py 를 돌리고 reports/kpi.md 의 '작업 지시서별' 표를 {WO: {열: 값}} 으로 읽는다."""
    r = subprocess.run([PY, "harness/kpi.py"], cwd=proj, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"kpi.py 실패: {r.stderr.strip()[-300:]}")
    text = (proj / "reports" / "kpi.md").read_text(encoding="utf-8")
    section = text.split("## 작업 지시서별", 1)[1].split("\n## ", 1)[0]
    rows = [l for l in section.splitlines() if l.startswith("|")]
    if len(rows) < 3:
        return {}
    head = [c.strip() for c in rows[0].strip("|").split("|")]
    out = {}
    for l in rows[2:]:
        cells = [c.strip() for c in l.strip("|").split("|")]
        row = dict(zip(head, cells))
        out[row.get("WO", "")] = row
    return out


def ev(ts: str, kind: str, **f: object) -> dict:
    return {"ts": ts, "kind": kind, **f}


def recent(e: dict) -> bool:
    t = datetime.strptime(e["ts"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return abs((datetime.now(timezone.utc) - t).total_seconds()) < 300


def main() -> int:
    results: list[tuple[str, bool, str]] = []

    def case(name: str, good: bool, detail: str = "") -> None:
        results.append((name, good, detail))

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)

        # ---- mark.py: 자동 대기 기록
        p = make_project(base, "m1")
        r = mark(p, "wait-start", "DEC", "--wo", "WO-AK01")
        e = events(p)[-1] if events(p) else {}
        case("K01 wait-start DEC: human_wait_start 이벤트, 구간 · 작업 ID · 지금 시각",
             r.returncode == 0 and e.get("kind") == "human_wait_start" and e.get("stage") == "DEC"
             and e.get("wo") == "WO-AK01" and bool(e) and recent(e),
             f"exit={r.returncode} event={e} {r.stdout.strip()[-120:]} {r.stderr.strip()[-120:]}")
        r = mark(p, "wait-end", "DEC", "--wo", "WO-AK01", "--decisions", "4")
        e = events(p)[-1] if events(p) else {}
        case("K02 wait-end DEC --decisions 4: human_wait_end 이벤트에 decisions=4",
             r.returncode == 0 and e.get("kind") == "human_wait_end" and e.get("stage") == "DEC"
             and e.get("decisions") == 4 and e.get("wo") == "WO-AK01",
             f"exit={r.returncode} event={e} {r.stderr.strip()[-120:]}")

        p = make_project(base, "m2")
        r = mark(p, "wait-end", "DEC", "--wo", "WO-09", "--decisions", "1")
        case("K03 열린 wait-start 없이 wait-end → 거부(종료 코드 2), 기록 안 함",
             r.returncode == 2 and not events(p), f"exit={r.returncode} events={len(events(p))}")

        p = make_project(base, "m3")
        r1 = mark(p, "wait-start", "MERGE", "--wo", "WO-H11")
        r2 = mark(p, "start", "DEC", "--wo", "WO-H11")
        case("K04 구간 DEC · MERGE 는 wait 와 start/end 모두에서 받음, 하네스 개발 작업 ID WO-H11 받음",
             r1.returncode == 0 and r2.returncode == 0 and len(events(p)) == 2,
             f"wait-start MERGE exit={r1.returncode}, start DEC exit={r2.returncode}, events={len(events(p))} {r1.stdout.strip()[-80:]}")

        p = make_project(base, "m3b")
        c1 = mark(p, "wait-start", "DEC", "--wo", "WO-09").returncode
        c2 = mark(p, "wait-start", "DEC", "--wo", "WO-09").returncode
        c3 = mark(p, "wait-end", "DEC", "--wo", "WO-09", "--decisions", "1").returncode
        c4 = mark(p, "wait-start", "DEC", "--wo", "WO-09").returncode
        kinds = [e.get("kind") for e in events(p)]
        case("K12 같은 구간 · 작업의 대기가 열려 있으면 wait-start 거부(2), 닫힌 뒤에는 다시 받음 (대기가 겹쳐 합산되지 않게)",
             [c1, c2, c3, c4] == [0, 2, 0, 0] and kinds == ["human_wait_start", "human_wait_end", "human_wait_start"],
             f"exit codes={[c1, c2, c3, c4]} kinds={kinds}")

        p = make_project(base, "m4")
        good_ids = ["WO-02", "WO-01b", "JS-01", "WO-AK01", "WO-H11"]
        bad_ids = ["WO_1", "abc", "WO-", "WO-AK"]
        ok_g = [mark(p, "note", "x", "--wo", w).returncode for w in good_ids]
        ok_b = [mark(p, "note", "x", "--wo", w).returncode for w in bad_ids]
        case("K05 작업 ID: WO-02 · WO-01b · JS-01 · WO-AK01 · WO-H11 받음, WO_1 · abc · WO- · WO-AK 거부",
             all(c == 0 for c in ok_g) and all(c == 2 for c in ok_b), f"good={ok_g} bad={ok_b}")

        p = make_project(base, "m5")
        r = mark(p, "wait-start", "DEC", "--wo", "WO-09", "--ts", "2026-01-01T00:00:00Z")
        case("K06 시각 인자는 받지 않음 (사후 기록 금지)", r.returncode != 0 and not events(p),
             f"exit={r.returncode} events={len(events(p))}")

        p = make_project(base, "m6")
        r = mark(p, "wait-start", "H99", "--wo", "WO-09")
        case("K07 모르는 구간 H99 → 거부(종료 코드 2)", r.returncode == 2 and not events(p), f"exit={r.returncode}")

        # ---- kpi.py: 집계
        p = make_project(base, "k1")
        write_events(p, [
            ev("2026-10-10T10:00:00Z", "human_wait_start", stage="DEC", wo="WO-09", note=None),
            ev("2026-10-10T10:02:00Z", "human_start", stage="DEC", wo="WO-09", note=None),
            ev("2026-10-10T10:08:00Z", "human_end", stage="DEC", wo="WO-09", note=None),
            ev("2026-10-10T10:12:00Z", "human_wait_end", stage="DEC", wo="WO-09", decisions=4, note=None),
            ev("2026-10-10T11:00:00Z", "human_wait_start", stage="MERGE", wo="WO-09", note=None),
            ev("2026-10-10T11:03:00Z", "human_wait_end", stage="MERGE", wo="WO-09", decisions=1, note=None),
            ev("2026-10-10T12:00:00Z", "human_wait_start", stage="DEC", wo="WO-10", note=None),
            ev("2026-10-10T12:30:00Z", "human_wait_end", stage="DEC", wo="WO-10", decisions=2, note=None),
            ev("2026-10-10T13:00:00Z", "human_wait_start", stage="H3", wo="WO-10", note=None),
            ev("2026-10-10T09:00:00Z", "human_note", stage=None, note="memo", wo="WO-11"),
        ])
        try:
            rows = kpi_rows(p)
            err = ""
        except RuntimeError as x:
            rows, err = {}, str(x)
        w9, w10, w11 = rows.get("WO-09", {}), rows.get("WO-10", {}), rows.get("WO-11", {})
        cols = ["사람 대기(분, 자동)", "막힌 지점 수", "결정 수", "사람 시간(분, 보조)"]
        case("K08 WO-09: 대기 12 + 3 = 15분, 막힌 지점 2, 결정 5, 사람 시간(mark) 6분",
             [w9.get(c) for c in cols] == ["15.0", "2", "5", "6.0"],
             err or f"{[w9.get(c) for c in cols]}")
        case("K09 WO-10: 닫힌 대기 30분 + 열린 대기 1개는 '진행 중'으로 표시, 막힌 지점 2, 결정 2",
             (w10.get("사람 대기(분, 자동)") or "").startswith("30.0") and "진행 중" in (w10.get("사람 대기(분, 자동)") or "")
             and w10.get("막힌 지점 수") == "2" and w10.get("결정 수") == "2",
             err or f"{[w10.get(c) for c in cols]}")
        case("K10 대기 기록이 없는 작업(WO-11): 대기 '기록 없음', 막힌 지점 0, 결정 0, 사람 시간 '기록 없음'",
             [w11.get(c) for c in cols] == ["기록 없음", "0", "0", "기록 없음"],
             err or f"{[w11.get(c) for c in cols]}")
        case("K11 대기 기록만 있는 작업(WO-10)의 사람 시간(mark)은 0 이 아니라 '기록 없음'",
             w10.get("사람 시간(분, 보조)") == "기록 없음", err or f"{w10.get('사람 시간(분, 보조)')}")

        p = make_project(base, "k2")
        write_events(p, [
            ev("2026-10-10T10:00:00Z", "human_start", stage="H3", wo="WO-12", note=None),
            ev("2026-10-10T10:05:00Z", "human_start", stage="H3", wo="WO-12", note=None),
            ev("2026-10-10T10:10:00Z", "human_end", stage="H3", wo="WO-12", note=None),
        ])
        try:
            w12 = kpi_rows(p).get("WO-12", {})
            err = ""
        except RuntimeError as x:
            w12, err = {}, str(x)
        case("K13 사람 시간(mark): 같은 구간을 다시 start 하면 마지막 start 부터 센다 (HD-16 이전 의미 유지) → 5.0",
             w12.get("사람 시간(분, 보조)") == "5.0", err or f"{w12.get('사람 시간(분, 보조)')}")

        # H-04: 원인은 역할과 별개. 전체 판정마다 --cause로 기록한다.
        p = make_project(base, "causes")
        (p / "config/gates.toml").write_text('[harness]\nadapter = "python"\n[[gate]]\nid = "G2"\nname = "minimal"\ncmd = ["python", "-c", "raise SystemExit(1)"]\n', encoding="utf-8")
        runs = []
        for cause in ("implementer", "test", "harness", "implementer"):
            r = subprocess.run([PY, "harness/run_gates.py", "--role", "implementer", "--base", "HEAD", "--wo", "WO-14", "--cause", cause], cwd=p, capture_output=True, text=True)
            runs.append(r)
        recorded = [e for e in events(p) if e.get("kind") == "gate_run"]
        report_path = p / "reports/gate_report.json"
        last_report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
        case("H04-01 판정 이벤트 · 보고서에 cause 기록 (실패 판정도 기록)",
             [e.get("cause") for e in recorded] == ["implementer", "test", "harness", "implementer"] and last_report.get("cause") == "implementer" and all(r.returncode == 1 for r in runs),
             f"exits={[r.returncode for r in runs]} events={recorded} stderr={runs[0].stderr.strip()}")
        # CLI 지원 여부와 별개로 KPI를 독립적으로 재현한다.
        write_events(p, [ev(f"2026-10-10T10:0{i}:00Z", "gate_run", wo="WO-14", role="implementer", cause=cause, passed=False, only=[], first_failed="G2", g0_violations=0, review_required=[], seconds=1) for i, cause in enumerate(("implementer", "test", "harness", "implementer"))])
        row = kpi_rows(p).get("WO-14", {})
        cause_cols = ["구현자 원인 회차", "테스트 · 재잠금 원인 회차", "하네스 원인 회차"]
        case("H04-02 KPI 원인별 열: 역할이 모두 implementer여도 2 / 1 / 1", [row.get(c) for c in cause_cols] == ["2", "1", "1"], str(row))
        budget = row.get("max_rounds 산정 회차")
        case("H04-03 max_rounds=2: 구현자 2회만 산정, 전체 4회로 초과시키지 않음", budget == "2" and int(budget) <= 2, str(row))
        r = subprocess.run([PY, "harness/run_gates.py", "--role", "implementer", "--base", "HEAD", "--wo", "WO-14", "--cause", "unknown"], cwd=p, capture_output=True, text=True)
        case("H04-R01 알 수 없는 원인 거부 · 이벤트 추가 없음", r.returncode == 2 and len(events(p)) == 4, f"exit={r.returncode}")

        # H-07: 기존 작업 ID와 함께 슬라이스 ID를 받는다.
        p = make_project(base, "slice-id")
        r = mark(p, "wait-start", "DEC", "--wo", "S01-app-state")
        r2 = mark(p, "wait-end", "DEC", "--wo", "S01-app-state", "--decisions", "1")
        case("H07-01 --wo 슬라이스 ID를 실제 대기 이벤트에 보존", r.returncode == 0 and r2.returncode == 0 and [e.get("wo") for e in events(p)] == ["S01-app-state", "S01-app-state"], f"exits={[r.returncode, r2.returncode]} {r.stdout.strip()}")
    all_good = True
    for name, good, detail in results:
        all_good &= good
        print(f"{'OK ' if good else 'BAD'} {name}{('  — ' + detail) if (detail and not good) else ''}")
    print("결과: 모든 사례가 기대와 일치" if all_good else "결과: 기대와 다른 사례가 있다")
    return 0 if all_good else 1


if __name__ == "__main__":
    sys.exit(main())
