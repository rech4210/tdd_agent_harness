"""하네스 자기 검사: 계측 정합 — 판정/자체 점검 구분 · 원인 정정 · 게이트별 시간 (WO-H44).

배경(시험 저장소 team-expense-split 문제 #16 · #25): `--only GT` · `--only G1` 같은 부분 실행이 구현 실행 · 원인 회차 ·
첫 실행 통과로 집계되고(WO-03b 구현 실행 5 → 실제 판정 2), 이미 기록된 판정의 원인을 바로잡을 방법이 없다
(events.jsonl 은 추가 전용). 게이트별 시간은 커밋되지 않는 reports/runs/ 에만 있다.

계약
M 이벤트 (run_gates.py 의 gate_run, 필드 추가만 · 기존 필드 불변)
  - purpose: --only 가 있으면 "self_check", 없으면 "verdict".
  - gate_seconds: {게이트 ID: 초} — 이번 실행 결과(results)에 있는 게이트마다 하나(SKIP 포함), 값은 run 파일 gates[].seconds 와 같다.
  - target_sha: 판정 대상 HEAD 전체 해시(40자) = run 파일 evidence.target_sha.
K 집계 (kpi.py “작업 지시서별” 표)
  - 판정 실행 = gate_run 중 purpose == "verdict", purpose 가 없는 옛 기록은 only 가 비었으면 판정. 나머지는 자체 점검.
  - “구현 실행 수” · “첫 실행 통과” · “전체 통과까지 실행 수” · “실패가 가장 많은 게이트” · “기계 실행 시간(분)” ·
    원인 회차 열(구현자 · 테스트 · 재잠금 · 하네스 · max_rounds 산정)은 **판정 실행만** 센다(역할 규칙은 지금 그대로).
  - 새 열 “자체 점검 실행 수”: 그 WO 의 자체 점검 gate_run 수(역할 무관).
  - 원인 정정: cause_amend 이벤트가 가리키는 판정 실행(run_file 일치, 같은 WO)은 가장 나중 정정의 cause 로 센다.
    새 열 “원인 정정”: 정정이 적용된 서로 다른 판정 실행 수.
  - 새 열 “명세 확정 후 사람 멈춤”: 그 WO 의 human_wait_start 중 stage 가 H1 · H2 가 아닌 것 + (기준 태그가 있으면)
    기준 태그 시각보다 뒤의 H1 · H2 시작. kpi.py 는 이벤트 파일을 바꾸지 않는다.
A 정정 기록 (mark.py)
  `amend-cause "<근거>" --wo <WO> --run <run_file> --cause implementer|test|harness --by <확인자>`
  → cause_amend 이벤트 {wo, run_file, cause, reason, by}. 근거 · --wo · --run · --by 는 비면 안 되고, --cause 는 목록 안,
  --run 은 events.jsonl 에 같은 WO 의 gate_run run_file 로 있어야 한다. 어기면 종료 2 · 기록 없음.
  --run · --cause 는 amend-cause 에서만, --by 는 classify · amend-cause 에서만 쓸 수 있다(다른 동작에 주면 종료 2).
F 수집기 (adapters/tools/collect_usage.py)
  `--agy-find <문자열> [--agy-root <brain 폴더>]`: <root>/*/.system_generated/logs/transcript.jsonl 중 내용에 그 문자열이
  있는 파일이 정확히 하나면 그것을 --agy-transcript 처럼 쓴다. 0개 · 2개 이상이면 종료 2 · 기록 없음(후보 경로를 출력,
  추측 금지). 출처 인자(--agy-transcript · --codex-session · --delegation-job · --agy-find)는 정확히 하나.
  --agy-root 기본값 = ~/.gemini/antigravity-cli/brain.

사용: python harness/selftest/check_metrics_cases.py
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_kpi_cases import PY, events, make_project, write_events  # noqa: E402
from check_kpi_t2_cases import agy_transcript  # noqa: E402

GATES = ('[harness]\nadapter = "python"\n'
         '[[gate]]\nid = "G1"\nname = "one"\ncmd = ["python", "-c", "pass"]\n'
         '[[gate]]\nid = "G2"\nname = "two"\ncmd = ["python", "-c", "pass"]\n')
OLD = "2000-01-01T00:00:00Z"
NEW = "2099-01-01T00:00:00Z"


def run(proj: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([PY, *args], cwd=proj, capture_output=True, text=True, encoding="utf-8", errors="replace")


def kpi(proj: Path) -> dict[str, dict[str, str]]:
    r = run(proj, "harness/kpi.py")
    if r.returncode != 0:
        return {"_error": {"msg": (r.stdout + r.stderr)[-300:]}}
    text = (proj / "reports" / "kpi.md").read_text(encoding="utf-8")
    section = text.split("## 작업 지시서별", 1)[1].split("\n## ", 1)[0]
    lines = [l for l in section.splitlines() if l.startswith("|")]
    head = [c.strip() for c in lines[0].strip("|").split("|")]
    return {r[0]: dict(zip(head, r)) for r in ([c.strip() for c in l.strip("|").split("|")] for l in lines[2:])}


def gr(t: str, wo: str, role: str, passed: bool, only: list[str], cause: str, run_file: str, **extra: object) -> dict:
    e = {"ts": t, "kind": "gate_run", "wo": wo, "slice": wo, "role": role, "cause": cause, "base": "x", "only": only,
         "passed": passed, "gates": {}, "first_failed": None if passed else "G1", "g0_violations": 0,
         "review_required": [], "skipped": [], "failed_cases": 0, "seconds": 10.0 if not only else 0.0,
         "run_file": run_file}
    e.update(extra)
    return e


def wait(t: str, wo: str, stage: str) -> dict:
    return {"ts": t, "kind": "human_wait_start", "wo": wo, "stage": stage, "note": None}


def main() -> int:
    results: list[tuple[str, bool, str]] = []

    def case(name: str, good: bool, detail: str = "") -> None:
        results.append((name, good, detail))

    base = Path(tempfile.mkdtemp(prefix="metrics-"))
    try:
        # ── M: run_gates 이벤트 필드 ──
        p = make_project(base, "gates")
        (p / "config" / "gates.toml").write_text(GATES, encoding="utf-8", newline="\n")
        subprocess.run(["git", "add", "-A"], cwd=p, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "gates"], cwd=p, check=True, capture_output=True)
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=p, capture_output=True, text=True).stdout.strip()
        r_full = run(p, "harness/run_gates.py", "--role", "implementer", "--base", "HEAD", "--wo", "WO-31")
        r_only = run(p, "harness/run_gates.py", "--role", "implementer", "--base", "HEAD", "--wo", "WO-31", "--only", "G2")
        runs = [e for e in events(p) if e.get("kind") == "gate_run"]
        full = runs[0] if runs else {}
        part = runs[1] if len(runs) > 1 else {}
        case("M01 --only 없는 실행 → gate_run.purpose = verdict",
             r_full.returncode == 0 and full.get("purpose") == "verdict", f"exit={r_full.returncode} purpose={full.get('purpose')}")
        case("M02 --only G2 실행 → gate_run.purpose = self_check",
             r_only.returncode == 0 and part.get("purpose") == "self_check", f"exit={r_only.returncode} purpose={part.get('purpose')}")
        rf = p / full.get("run_file", "missing.json")
        run_doc = json.loads(rf.read_text(encoding="utf-8")) if rf.is_file() else {}
        want = {g["id"]: g["seconds"] for g in run_doc.get("gates", [])}
        gs = full.get("gate_seconds")
        case("M03 gate_seconds = run 파일 gates 의 {ID: seconds} (G1 · G2 둘 다, 부분 실행은 G2 만)",
             bool(want) and gs == want and set(want) == {"G1", "G2"} and set((part.get("gate_seconds") or {})) == {"G2"},
             f"event={gs} run={want} partial={part.get('gate_seconds')}")
        case("M04 target_sha = HEAD 전체 해시 = run 파일 evidence.target_sha",
             full.get("target_sha") == head and run_doc.get("evidence", {}).get("target_sha") == head
             and bool(re.fullmatch(r"[0-9a-f]{40}", full.get("target_sha") or "")),
             f"event={full.get('target_sha')} head={head}")
        case("M05 기존 필드 유지 (only · seconds · run_file · cause · passed)",
             all(k in full for k in ("only", "seconds", "run_file", "cause", "passed")) and full.get("only") == []
             and part.get("only") == ["G2"], str(sorted(full)))
        # M06 (리드 보정): 같은 초에 두 번 실행해도 run 파일이 서로 달라 앞 기록이 덮이지 않는다
        (p / "reports" / "runs").mkdir(parents=True, exist_ok=True)
        seen_files = {e.get("run_file") for e in runs}
        r_a = run(p, "harness/run_gates.py", "--role", "implementer", "--base", "HEAD", "--wo", "WO-32")
        r_b = run(p, "harness/run_gates.py", "--role", "implementer", "--base", "HEAD", "--wo", "WO-32")
        w32 = [e for e in events(p) if e.get("kind") == "gate_run" and e.get("wo") == "WO-32"]
        docs = [json.loads((p / e["run_file"]).read_text(encoding="utf-8")) for e in w32 if (p / e.get("run_file", "x")).is_file()]
        case("M06 연속 두 실행 → run_file 서로 다름 · 둘 다 남음 (같은 초 덮어쓰기 없음)",
             r_a.returncode == 0 and r_b.returncode == 0 and len(w32) == 2 and len({e["run_file"] for e in w32}) == 2
             and len(docs) == 2 and len(seen_files) == len(runs), str([e.get("run_file") for e in w32]))

        # ── K: 판정만 세기 (시험 저장소 WO-03b · WO-02b · WO-01 재현) ──
        p = make_project(base, "kpi")
        evs = [
            # WO-01: test_author 자체 점검 3 + 구현 판정 1(통과)
            *[gr(f"2026-10-19T01:4{i}:00Z", "WO-01", "test_author", True, ["GT"], "test", f"reports/runs/t{i}.json") for i in range(3)],
            gr("2026-10-19T02:04:00Z", "WO-01", "implementer", True, [], "implementer", "reports/runs/i1.json"),
            # WO-03b: 판정 실패(원인 잘못 기록) → G1 자체 점검 3 → 판정 통과(cause=test)
            gr("2026-10-19T09:48:26Z", "WO-03b", "implementer", False, [], "implementer", "reports/runs/b1.json"),
            *[gr(f"2026-10-19T09:5{i}:00Z", "WO-03b", "implementer", True, ["G1"], "test", f"reports/runs/b-g1-{i}.json") for i in range(3)],
            gr("2026-10-19T09:58:10Z", "WO-03b", "implementer", True, [], "test", "reports/runs/b2.json"),
            # WO-02b: G1 자체 점검(구현자 원인 기본값) 먼저 → 판정 통과. purpose 필드가 있는 새 기록
            gr("2026-10-19T10:31:04Z", "WO-02b", "implementer", True, ["G1"], "implementer", "reports/runs/c0.json", purpose="self_check"),
            gr("2026-10-19T10:32:50Z", "WO-02b", "implementer", True, [], "implementer", "reports/runs/c1.json", purpose="verdict"),
        ]
        write_events(p, evs)
        rows = kpi(p)
        w01, w3b, w2b = rows.get("WO-01", {}), rows.get("WO-03b", {}), rows.get("WO-02b", {})
        case("K01 WO-03b 구현 실행 수 = 판정 2 (G1 자체 점검 3 제외)", w3b.get("구현 실행 수") == "2", str(w3b.get("구현 실행 수") or rows.get("_error")))
        case("K02 WO-02b 첫 실행 통과 = 예 · 구현 실행 수 1 (앞선 G1 자체 점검 무시)",
             w2b.get("첫 실행 통과") == "예" and w2b.get("구현 실행 수") == "1", f"{w2b.get('첫 실행 통과')} / {w2b.get('구현 실행 수')}")
        case("K03 WO-01 테스트 · 재잠금 원인 회차 0 (GT 자체 점검 3 제외) · 구현자 원인 1",
             w01.get("테스트 · 재잠금 원인 회차") == "0" and w01.get("구현자 원인 회차") == "1",
             f"{w01.get('테스트 · 재잠금 원인 회차')} / {w01.get('구현자 원인 회차')}")
        case("K04 WO-02b 구현자 원인 · max_rounds 산정 1 (자체 점검의 cause=implementer 제외)",
             w2b.get("구현자 원인 회차") == "1" and w2b.get("max_rounds 산정 회차") == "1",
             f"{w2b.get('구현자 원인 회차')} / {w2b.get('max_rounds 산정 회차')}")
        case("K05 자체 점검 실행 수 열: WO-01 3 · WO-03b 3 · WO-02b 1",
             [w01.get("자체 점검 실행 수"), w3b.get("자체 점검 실행 수"), w2b.get("자체 점검 실행 수")] == ["3", "3", "1"],
             str([w01.get("자체 점검 실행 수"), w3b.get("자체 점검 실행 수"), w2b.get("자체 점검 실행 수")]))
        case("K06 기계 실행 시간 = 판정 실행 합 (WO-03b 20초 → 0.3분)", w3b.get("기계 실행 시간(분)") == "0.3", str(w3b.get("기계 실행 시간(분)")))

        # ── A: 정정 기록 ──
        r_ok = run(p, "harness/mark.py", "amend-cause", "지시서 오류 (#24) - 구현자 원인 아님", "--wo", "WO-03b",
                   "--run", "reports/runs/b1.json", "--cause", "test", "--by", "lead")
        am = [e for e in events(p) if e.get("kind") == "cause_amend"]
        case("A01 amend-cause → cause_amend {wo, run_file, cause, reason, by} 1건",
             r_ok.returncode == 0 and len(am) == 1 and am[0].get("wo") == "WO-03b" and am[0].get("run_file") == "reports/runs/b1.json"
             and am[0].get("cause") == "test" and am[0].get("by") == "lead" and "#24" in (am[0].get("reason") or ""),
             f"exit={r_ok.returncode} {am} {(r_ok.stdout + r_ok.stderr)[-150:]}")
        bad = [
            ("없는 run", ["x", "--wo", "WO-03b", "--run", "reports/runs/none.json", "--cause", "test", "--by", "lead"]),
            ("다른 WO 의 run", ["x", "--wo", "WO-01", "--run", "reports/runs/b1.json", "--cause", "test", "--by", "lead"]),
            ("cause 목록 밖", ["x", "--wo", "WO-03b", "--run", "reports/runs/b1.json", "--cause", "lead", "--by", "lead"]),
            ("--by 없음", ["x", "--wo", "WO-03b", "--run", "reports/runs/b1.json", "--cause", "test"]),
            ("근거 비어 있음", ["  ", "--wo", "WO-03b", "--run", "reports/runs/b1.json", "--cause", "test", "--by", "lead"]),
            ("--wo 없음", ["x", "--run", "reports/runs/b1.json", "--cause", "test", "--by", "lead"]),
        ]
        before = len(events(p))
        codes = {n: run(p, "harness/mark.py", "amend-cause", *a).returncode for n, a in bad}
        case("A02 잘못된 정정(없는 run · 다른 WO · cause 밖 · --by 없음 · 근거 빈칸 · --wo 없음) → 종료 2 · 기록 없음",
             all(c == 2 for c in codes.values()) and len(events(p)) == before, f"{codes} added={len(events(p)) - before}")
        r_x = run(p, "harness/mark.py", "note", "메모", "--wo", "WO-03b", "--cause", "test")
        r_y = run(p, "harness/mark.py", "note", "메모", "--wo", "WO-03b", "--run", "reports/runs/b1.json")
        case("A03 --cause · --run 을 amend-cause 밖에서 쓰면 종료 2", r_x.returncode == 2 and r_y.returncode == 2,
             f"{r_x.returncode} {r_y.returncode}")
        rows = kpi(p)
        w3b = rows.get("WO-03b", {})
        case("A04 정정 반영: WO-03b 구현자 원인 0 · 테스트 원인 2 · 원인 정정 1",
             w3b.get("구현자 원인 회차") == "0" and w3b.get("테스트 · 재잠금 원인 회차") == "2" and w3b.get("원인 정정") == "1",
             f"{w3b.get('구현자 원인 회차')} / {w3b.get('테스트 · 재잠금 원인 회차')} / {w3b.get('원인 정정')}")
        run(p, "harness/mark.py", "amend-cause", "다시 확인: 구현자 원인", "--wo", "WO-03b", "--run", "reports/runs/b1.json",
            "--cause", "implementer", "--by", "user")
        w3b = kpi(p).get("WO-03b", {})
        case("A05 같은 판정 재정정 → 가장 나중 정정(implementer) 우선, 원인 정정 1 (서로 다른 판정 수)",
             w3b.get("구현자 원인 회차") == "1" and w3b.get("원인 정정") == "1", f"{w3b.get('구현자 원인 회차')} / {w3b.get('원인 정정')}")
        lines = (p / "reports" / "events.jsonl").read_text(encoding="utf-8").splitlines()
        b1 = next(json.loads(l) for l in lines if '"reports/runs/b1.json"' in l and '"gate_run"' in l)
        case("A06 정정은 원래 gate_run 을 바꾸지 않음 (cause=implementer 그대로)", b1.get("cause") == "implementer", str(b1.get("cause")))

        # ── K07: 명세 확정 후 사람 멈춤 ──
        p = make_project(base, "stops")
        subprocess.run(["git", "tag", "-a", "wo41-base", "-m", "base"], cwd=p, check=True, capture_output=True)
        write_events(p, [
            wait(OLD, "WO-41", "H1"), wait(OLD, "WO-41", "H3"), wait(NEW, "WO-41", "H2"),   # H3 1 + 태그 뒤 H2 1 = 2
            wait(OLD, "WO-42", "H1"),                                                     # H1 만 = 0
            wait(OLD, "WO-43", "H1"), wait(NEW, "WO-43", "DEC"),                          # 태그 없음: DEC 1, H1 0 = 1
            gr(OLD, "WO-42", "implementer", True, [], "implementer", "reports/runs/z.json"),
        ])
        rows = kpi(p)
        got = [rows.get(w, {}).get("명세 확정 후 사람 멈춤") for w in ("WO-41", "WO-42", "WO-43")]
        case("K07 명세 확정 후 사람 멈춤: WO-41 2 (H3 + 기준 태그 뒤 H2) · WO-42 0 · WO-43 1 (태그 없음 → H1 · H2 제외만)",
             got == ["2", "0", "1"], str(got))

        # ── F: 수집기 대화 찾기 ──
        p = make_project(base, "find")
        has_tool = (p / "adapters" / "tools" / "collect_usage.py").exists()
        root = base / "agyroot"
        t1 = agy_transcript(root, "conv-mine")
        t1.write_text(t1.read_text(encoding="utf-8").replace('"USER_EXPLICIT"', '"USER_EXPLICIT", "text": "work_orders/WO-44.md"'),
                      encoding="utf-8")
        agy_transcript(root, "conv-other")
        common = ["--wo", "WO-44", "--role", "implementer", "--agy-root", str((root / "brain"))]
        r1 = run(p, "adapters/tools/collect_usage.py", "--agy-find", "work_orders/WO-44.md", *common)
        u = [e for e in events(p) if e.get("kind") == "model_usage"]
        case("F01 --agy-find 이 한 대화만 맞으면 그 대화(run_id conv-mine)로 기록",
             has_tool and r1.returncode == 0 and len(u) == 1 and u[0].get("run_id") == "conv-mine",
             f"exit={r1.returncode} {[e.get('run_id') for e in u]} {(r1.stdout + r1.stderr)[-200:]}")
        r0 = run(p, "adapters/tools/collect_usage.py", "--agy-find", "work_orders/NONE.md", *common)
        r2 = run(p, "adapters/tools/collect_usage.py", "--agy-find", "USER_EXPLICIT", *common)
        u2 = [e for e in events(p) if e.get("kind") == "model_usage"]
        case("F02 0개 · 2개 이상 일치 → 종료 2 · 기록 없음 (2개면 두 경로 출력)",
             r0.returncode == 2 and r2.returncode == 2 and len(u2) == 1
             and "conv-mine" in (r2.stdout + r2.stderr) and "conv-other" in (r2.stdout + r2.stderr),
             f"exits={r0.returncode},{r2.returncode} added={len(u2) - 1}")
        r3 = run(p, "adapters/tools/collect_usage.py", "--agy-find", "work_orders/WO-44.md", "--agy-transcript", str(t1), *common)
        case("F03 --agy-find 과 다른 출처를 함께 주면 종료 2", r3.returncode == 2, str(r3.returncode))
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
