"""하네스 자기 검사: core-v0.4 의 S04 반영 사례 (WO-H17, dev/UPGRADE-core-v0.4.md V4-04 · 06 · 07 · 08).

- K  KPI 열 이름 (HD-19): "G0 우회 시도(건)" → "G0 위반(건)". 위반에는 권한 · 잠금도 있어 "우회" 는 판단이다
- R  역할 틀 (HD-20 · HD-23): 잠금 전에 일하는 읽기 전용 역할은 잠금 검사 면제, 분리 쌍 implementer ≠ adversary,
     프리셋은 reviewer 를 배정하지 않는다 (품질 검토 = adversary)
- B  GT 경계 표 승격 근거 (HD-23 Q3 · Q4): 경계 표의 행마다 마지막 칸이 D-## · 표본:<원문> · 피해:<시나리오> ·
     DEF-## · 계약 밖 중 하나. 표본은 기능 폴더의 samples/ 안 사람 원문만 (이름이 derived 로 시작하는 파생 표본 불가)
- F  판정 전 피드백 모드 (HD-24): run_gates.py --feedback 은 설정 feedback_gates 의 게이트만 돌리고(커버리지 종류 불가),
     gate_run 이 아니라 feedback_run 을 남기며, 판정 보고서를 덮지 않는다. KPI 는 피드백 실행을 판정 회차에 넣지 않는다.
     위임 도구가 격리 작업 공간에서 돌린 피드백은 이벤트가 본 저장소로 오지 않으므로, config/delegation.toml 의
     feedback_key 가 있으면 KPI 가 지시서 job 의 위임 기록에서 반복 수를 읽는다 (F10 ~ F13)

고치기 전 Red 24개. 지금도 통과하는 11개는 회귀 방지(B01 · B12 · B15 · R04 · R05 · F04 · F09)와
과잉 거부 방지(B04 · B08 · B10 · B13: 근거가 있는 행은 통과해야 한다)다.
사용: python harness/selftest/check_v04_cases.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import check_g0_spec_cases as g0c  # noqa: E402
from check_kpi_cases import PY, ev, events, kpi_rows, make_project, write_events  # noqa: E402
from traceability import check_traceability  # noqa: E402
from harness_fixture import DEFAULT_CONFIG

ROOT = HERE.parents[1]
FEEDBACK_ONLY_G2 = (
    '[harness]\nadapter = "python"\nfeedback_gates = ["G2"]\n'
    '[[gate]]\nid = "G2"\nname = "tests"\ncmd = ["python", "-c", "raise SystemExit(1)"]\n'
    '[[gate]]\nid = "G3"\nname = "coverage"\nkind = "coverage"\nreport = "reports/coverage.json"\nmin_branch_percent = 100\n'
)

# ---------- B: 경계 표 ----------

SPEC_HEAD = """# 기능

## Requirements

- **FR-001**: 입력을 검증한다

## Boundary tables

<!--
| 주석 안 | 표 | 는 | [결정 대기] |
-->

| 입력 | 정의역 | 경계 | 승격 근거 |
|:---|:---|:---|:---|
"""
DECISIONS = "# 결정 기록\n\n## D-01 단위\n\n정수.\n"
# 승인 칸 = 정의된 D-## (WO-H88: 승인되지 않은 DEF 는 인용할 수 없다)
DEFAULTS = "# 공통 기본값\n\n| ID | 규칙 | 승인 |\n|:---|:---|:---|\n| **DEF-01** | 거부는 입력 · 상태를 바꾸지 않는다 | D-01 |\n"
TEST = '"""t.\n\nguards:      FR-001\ntechnique:   05\nchecklist:   B-2\n"""\n'
CFG = {"requirements": ["specs/*/spec.md"], "decisions": ["reports/decisions.md"], "modules": ["tests/**/test_*.py"],
       "defaults": ["docs/DEFAULTS.md"]}


def gt(rows: list[str], cfg_extra: dict | None = None, spec: str | None = None) -> tuple[bool, list[str], list[str]]:
    root = Path(tempfile.mkdtemp(prefix="v04-gt-"))
    try:
        files = {"specs/001-input/spec.md": spec if spec is not None else SPEC_HEAD + "".join(r + "\n" for r in rows),
                 "specs/001-input/samples/original-1.json": "{}\n",
                 "specs/001-input/samples/derived-1.json": "{}\n",
                 "reports/decisions.md": DECISIONS, "docs/DEFAULTS.md": DEFAULTS, "tests/unit/test_input.py": TEST}
        for rel, text in files.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text, encoding="utf-8", newline="\n")
        cfg = {**CFG, **(cfg_extra or {})}
        return check_traceability(cfg, root)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def boundary_bad(bad: list[str]) -> list[str]:
    return [b for b in bad if b.startswith("[경계]")]


def b_cases(results: list[tuple[str, bool, str]]) -> None:
    def case(cid: str, rows: list[str], expect_ok: bool, need: str = "") -> None:
        ok, bad, _ = gt(rows)
        bb = boundary_bad(bad)
        good = (ok and not bb) if expect_ok else (not ok and bool(bb) and (need in " ".join(bb)))
        results.append((cid, good, "; ".join(bad)[:160] or "통과"))

    case("B01 D-## (정의됨) → 통과 (회귀 방지: 지금 형식)", ["| n | 0 ~ | 0 포함 | D-01 |"], True)
    case("B02 근거 칸이 빈 행 → [경계] 실패", ["| n | 0 ~ | 0 포함 |  |"], False)
    case("B03 [결정 대기] → [경계] 실패", ["| n | 0 ~ | 0 포함 | [결정 대기] |"], False)
    case("B04 표본:samples/original-1.json (기능 폴더 안 원문) → 통과", ["| n | 0 ~ | 0 포함 | 표본:samples/original-1.json |"], True)
    case("B05 없는 표본 파일 → [경계] 실패 (경로가 메시지에)", ["| n | 0 ~ | 0 포함 | 표본:samples/none.json |"], False, "samples/none.json")
    case("B06 파생 표본(derived-*) → [경계] 실패", ["| n | 0 ~ | 0 포함 | 표본:samples/derived-1.json |"], False, "derived-1.json")
    case("B07 기능 폴더 밖 표본(../) → [경계] 실패", ["| n | 0 ~ | 0 포함 | 표본:../../docs/DEFAULTS.md |"], False)
    case("B08 피해:<시나리오> → 통과", ["| n | 0 ~ | 0 포함 | 피해: 중복 저장으로 기록이 사라진다 |"], True)
    case("B09 피해: (빈 시나리오) → [경계] 실패", ["| n | 0 ~ | 0 포함 | 피해: |"], False)
    case("B10 DEF-01 (공통 기본값 문서에 정의) → 통과", ["| n | 0 ~ | 0 포함 | DEF-01 |"], True)
    case("B11 DEF-09 (정의 안 됨) · D-99 (정의 안 됨) → [경계] 실패",
         ["| n | 0 ~ | 0 포함 | DEF-09 |", "| m | 0 ~ | 0 포함 | D-99 |"], False)
    case("B13 계약 밖 → 통과", ["| n | 0 ~ | 0 포함 | 계약 밖 |"], True)

    ok, bad, _ = gt([], spec="# 기능\n\n## Requirements\n\n- **FR-001**: 입력을 검증한다\n")
    results.append(("B12 경계 표가 없는 명세 → 경계 위반 없음 (회귀 방지)", not boundary_bad(bad), "; ".join(bad)[:120]))

    ok, bad, notes = gt(["| a | 0 ~ | 0 포함 | D-01 |", "| b | 0 ~ | 0 포함 | 계약 밖 |", "| c | 0 ~ | 0 포함 | 계약 밖 |"])
    joined = " ".join(notes)
    results.append(("B14 참고(notes)에 계약 안 · 밖 개수 (계약 안 1 · 계약 밖 2)",
                    ok and "계약 안 1" in joined and "계약 밖 2" in joined, joined[:160] or "notes 없음"))

    ok, bad, _ = gt(["| n | 0 ~ | 0 포함 | [결정 대기] |"], cfg_extra={"checks": ["header", "forward"]})
    results.append(("B15 checks 에 boundaries 가 없으면 경계 검사 안 함 (고를 수 있음)", not boundary_bad(bad), "; ".join(bad)[:120]))


# ---------- R: 역할 틀 ----------

def r_cases(results: list[tuple[str, bool, str]]) -> None:
    roles = tomllib.loads((DEFAULT_CONFIG / "roles.toml").read_text(encoding="utf-8"))
    r = roles["roles"]
    readonly = [name for name in ("adversary", "mutation_auditor", "reviewer") if name in r]
    missing = [name for name in readonly if r[name].get("lock_exempt") is not True]
    results.append(("R01 역할 틀: 읽기 전용 역할(adversary · mutation_auditor · reviewer) lock_exempt = true",
                    "adversary" in readonly and not missing, f"빠짐={missing}"))
    pairs = {frozenset(p) for p in roles.get("separation", {}).get("pairs", [])}
    results.append(("R02 역할 틀: 분리 쌍에 implementer ≠ adversary", frozenset(("implementer", "adversary")) in pairs,
                    str(sorted(sorted(p) for p in pairs))))

    repo = g0c.make_repo()
    try:
        (repo / "config" / "roles.toml").write_bytes((DEFAULT_CONFIG / "roles.toml").read_bytes())
        g0c.write_lock(repo)
        g0c.git(repo, "add", "-A")
        g0c.git(repo, "commit", "-qm", "lock with template roles")
        (repo / "specs" / "002-next").mkdir(parents=True)
        (repo / "specs" / "002-next" / "spec.md").write_text("- **FR-010**: 다음 기능\n", encoding="utf-8")
        g0c.git(repo, "add", "-A")
        g0c.git(repo, "commit", "-qm", "next spec (spec_author, before next lock)")
        g0c.git(repo, "tag", "-f", "base")
        (repo / "reports").mkdir(exist_ok=True)
        (repo / "reports" / "review.md").write_text("검토\n", encoding="utf-8")
        ok, bad = g0c.g0(repo, "adversary")
        results.append(("R03 잠금 뒤 남의 새 명세가 있을 때 adversary 가 보고서만 추가 → G0 통과 (#43)", ok, "; ".join(bad)[:160]))

        (repo / "tests" / "unit" / "test_money.py").write_text("def test_x() -> None:\n    assert False\n", encoding="utf-8")
        ok, bad = g0c.g0(repo, "adversary")
        results.append(("R04 adversary 가 잠긴 테스트를 고침 → [권한] 실패 (회귀 방지)", not ok and any("[권한]" in b for b in bad),
                        "; ".join(bad)[:160]))
        ok, bad = g0c.g0(repo, "implementer")
        results.append(("R05 implementer 는 여전히 잠금 검사 대상 → [잠금] 실패 (회귀 방지)", not ok and any("[잠금]" in b for b in bad),
                        "; ".join(bad)[:160]))
    finally:
        shutil.rmtree(repo, ignore_errors=True)

    assigned = {}
    for preset in sorted((ROOT / "templates" / "roles").glob("*/preset.toml")):
        assign = tomllib.loads(preset.read_text(encoding="utf-8")).get("assign", {})
        assigned[preset.parent.name] = assign
    bad_presets = [n for n, a in assigned.items() if "reviewer" in a or "adversary" not in a]
    results.append(("R06 프리셋은 reviewer 를 배정하지 않고 adversary(품질 검토)를 배정", bool(assigned) and not bad_presets,
                    f"문제 프리셋={bad_presets}"))


# ---------- K · F: KPI 와 피드백 모드 ----------

def gate_run_ev(i: int, wo: str, **extra: object) -> dict:
    return ev(f"2026-10-10T10:{i:02d}:00Z", "gate_run", wo=wo, role="implementer", cause="implementer", passed=False,
              only=[], first_failed="G2", g0_violations=0, review_required=[], seconds=1, **extra)


def kf_cases(results: list[tuple[str, bool, str]]) -> None:
    base = Path(tempfile.mkdtemp(prefix="v04-kf-"))
    try:
        p = make_project(base, "kpi-label")
        write_events(p, [gate_run_ev(0, "WO-30")])
        row = kpi_rows(p).get("WO-30", {})
        results.append(("K01 KPI 열 'G0 위반(건)' 이 있고 'G0 우회 시도(건)' 은 없다",
                        "G0 위반(건)" in row and "G0 우회 시도(건)" not in row, str(list(row))[:160]))

        def known(r: subprocess.CompletedProcess[str]) -> bool:
            # 거부가 "모르는 인자" 때문이 아니라 피드백 모드의 판단이어야 한다
            return "unrecognized arguments" not in r.stderr

        def feedback(proj: Path, *extra: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run([PY, "harness/run_gates.py", "--feedback", "--role", "implementer", "--base", "HEAD",
                                   "--wo", "WO-31", *extra], cwd=proj, capture_output=True, text=True, encoding="utf-8")

        p = make_project(base, "fb-none")
        (p / "config" / "gates.toml").write_text(FEEDBACK_ONLY_G2.replace('feedback_gates = ["G2"]\n', ""), encoding="utf-8")
        r = feedback(p)
        results.append(("F01 feedback_gates 설정이 없으면 --feedback 거부 (종료 코드 2), 이벤트 없음",
                        r.returncode == 2 and known(r) and not events(p), f"exit={r.returncode} {r.stdout[-120:]}"))

        p = make_project(base, "fb-run")
        (p / "config" / "gates.toml").write_text(FEEDBACK_ONLY_G2, encoding="utf-8")
        report = p / "reports" / "gate_report.json"
        report.write_text('{"judged": true}\n', encoding="utf-8")
        before_runs = sorted((p / "reports").glob("runs/*"))
        r = feedback(p)
        fb_path = p / "reports" / "feedback_report.json"
        fb = json.loads(fb_path.read_text(encoding="utf-8")) if fb_path.exists() else {}
        ran = [g.get("id") for g in fb.get("gates", [])]
        results.append(("F02 --feedback 은 feedback_gates(G2)만 돌린다 (G3 안 돌림), 실패면 종료 코드 1, feedback_report.json",
                        r.returncode == 1 and ran == ["G2"], f"exit={r.returncode} ran={ran}"))
        kinds = [e.get("kind") for e in events(p)]
        results.append(("F03 이벤트는 feedback_run 하나 (gate_run 아님), wo 기록",
                        kinds == ["feedback_run"] and events(p)[0].get("wo") == "WO-31", str(kinds)))
        results.append(("F04 판정 보고서 gate_report.json · reports/runs 를 바꾸지 않는다",
                        report.read_text(encoding="utf-8") == '{"judged": true}\n'
                        and sorted((p / "reports").glob("runs/*")) == before_runs, ""))

        n_before = len(events(p))
        r = feedback(p, "--only", "G3")
        results.append(("F05 --feedback --only 가 feedback_gates 밖(G3) → 거부 (2), 이벤트 추가 없음",
                        r.returncode == 2 and known(r) and len(events(p)) == n_before, f"exit={r.returncode}"))

        p = make_project(base, "fb-cov")
        (p / "config" / "gates.toml").write_text(FEEDBACK_ONLY_G2.replace('["G2"]', '["G2", "G3"]'), encoding="utf-8")
        r = feedback(p)
        results.append(("F06 feedback_gates 에 커버리지 종류 게이트 → 거부 (2, 커버리지 신호는 구현자에게 주지 않는다), 이벤트 없음",
                        r.returncode == 2 and known(r) and not events(p), f"exit={r.returncode} {r.stdout[-120:]}"))

        p = make_project(base, "fb-kpi")
        write_events(p, [ev("2026-10-10T10:00:00Z", "feedback_run", wo="WO-32", role="implementer", passed=False, gates={"G2": False}),
                         ev("2026-10-10T10:01:00Z", "feedback_run", wo="WO-32", role="implementer", passed=True, gates={"G2": True}),
                         gate_run_ev(2, "WO-32")])
        row = kpi_rows(p).get("WO-32", {})
        results.append(("F07 KPI: 피드백 2 · 판정 1 → 구현 실행 수 1, max_rounds 산정 1, '판정 전 피드백 실행 수' 2",
                        row.get("구현 실행 수") == "1" and row.get("max_rounds 산정 회차") == "1"
                        and row.get("판정 전 피드백 실행 수") == "2", str({k: row.get(k) for k in ("구현 실행 수", "max_rounds 산정 회차", "판정 전 피드백 실행 수")})))

        gates_doc = tomllib.loads((DEFAULT_CONFIG / "gates.toml").read_text(encoding="utf-8"))
        fbg = gates_doc.get("harness", {}).get("feedback_gates")
        kinds_by_id = {g["id"]: g.get("kind", "") for g in gates_doc.get("gate", [])}
        results.append(("F08 설정 틀: feedback_gates 에 G2 가 있고, 모든 ID 가 게이트에 있으며, 커버리지 종류가 없다",
                        isinstance(fbg, list) and "G2" in fbg and all(g in kinds_by_id for g in fbg)
                        and all(kinds_by_id.get(g) != "coverage" for g in fbg), f"feedback_gates={fbg}"))

        p = make_project(base, "judge-still")
        (p / "config" / "gates.toml").write_text(FEEDBACK_ONLY_G2, encoding="utf-8")
        r = subprocess.run([PY, "harness/run_gates.py", "--role", "implementer", "--base", "HEAD", "--wo", "WO-33"],
                           cwd=p, capture_output=True, text=True, encoding="utf-8")
        kinds = [e.get("kind") for e in events(p)]
        results.append(("F09 --feedback 없는 판정은 지금처럼 gate_run · gate_report.json (회귀 방지)",
                        r.returncode == 1 and kinds == ["gate_run"] and (p / "reports" / "gate_report.json").exists(), str(kinds)))

        # F10 ~ F13: 위임 도구는 격리 작업 공간에서 --feedback 을 돌리므로 feedback_run 이벤트가 본 저장소로 오지 않는다.
        # config/delegation.toml 의 feedback_key 로 위임 기록(지시서 머리말 job)에서 반복 수를 읽는다 (HD-18 연결점, HD-24)
        def delegated(name: str, delegation: str | None, record: str | None, fb_events: int = 0) -> dict:
            proj = make_project(base, name)
            wo = proj / "work_orders" / "WO-40.md"
            wo.parent.mkdir(parents=True, exist_ok=True)
            wo.write_text("# WO-40\n\n```yaml\nagent: agy\njob: wo40-r1\n```\n", encoding="utf-8", newline="\n")
            if delegation is not None:
                (proj / "config" / "delegation.toml").write_text(delegation, encoding="utf-8", newline="\n")
            if record is not None:
                rec = proj / ".jobs" / "wo40-r1" / "meta.json"
                rec.parent.mkdir(parents=True, exist_ok=True)
                rec.write_text(record, encoding="utf-8", newline="\n")
            evs = [ev(f"2026-10-10T09:{i:02d}:00Z", "feedback_run", wo="WO-40", role="implementer", passed=False, gates={"G2": False})
                   for i in range(fb_events)]
            write_events(proj, evs + [gate_run_ev(30, "WO-40")])
            return kpi_rows(proj).get("WO-40", {})

        col = "판정 전 피드백 실행 수"
        with_key = '[delegation]\nrecord = ".jobs/{job}/meta.json"\nfamily_key = "family"\nfeedback_key = "feedback_iterations"\n'
        row = delegated("fb-record", with_key, '{"family": "google", "feedback_iterations": 3}\n')
        results.append(("F10 delegation.toml feedback_key + 지시서 job → 위임 기록의 반복 수 3 (본 저장소에 feedback_run 이벤트 없음)",
                        row.get(col) == "3", str(row.get(col))))
        row = delegated("fb-record-missing", with_key, None)
        results.append(("F11 feedback_key 가 있는데 위임 기록 파일이 없다 → '기록 없음' (0 으로 숨기지 않는다)",
                        row.get(col) == "기록 없음", str(row.get(col))))
        row = delegated("fb-record-nokey", with_key, '{"family": "google"}\n')
        results.append(("F12 위임 기록에 반복 수 키가 없다 → '기록 없음'", row.get(col) == "기록 없음", str(row.get(col))))
        row = delegated("fb-no-key", '[delegation]\nrecord = ".jobs/{job}/meta.json"\nfamily_key = "family"\n',
                        '{"family": "google", "feedback_iterations": 3}\n', fb_events=2)
        results.append(("F13 feedback_key 가 없으면 지금처럼 feedback_run 이벤트 수 (2)", row.get(col) == "2", str(row.get(col))))

        # F14 (WO-H17 판정에서 발견, N-4): 피드백으로 돈 테스트 게이트가 커버리지 보고서를 파일로 남기면
        # 에이전트가 다음 반복에서 그 파일을 읽어 커버리지 신호를 얻는다 → 피드백 실행 뒤 커버리지 종류 게이트의 보고서는 없어야 한다
        p = make_project(base, "fb-cov-file")
        writes_cov = FEEDBACK_ONLY_G2.replace(
            'cmd = ["python", "-c", "raise SystemExit(1)"]',
            'cmd = ["python", "-c", "import pathlib; pathlib.Path(\'reports/coverage.json\').write_text(\'x\'); raise SystemExit(1)"]')  # 중괄호 금지: 명령 인자는 str.format 을 거친다
        (p / "config" / "gates.toml").write_text(writes_cov, encoding="utf-8")
        r = feedback(p)
        results.append(("F14 --feedback 뒤 커버리지 게이트의 보고서(reports/coverage.json)가 작업 공간에 남지 않는다",
                        r.returncode == 1 and (p / "reports" / "feedback_report.json").exists()
                        and not (p / "reports" / "coverage.json").exists(),
                        f"exit={r.returncode} 남음={(p / 'reports' / 'coverage.json').exists()}"))

        # F15 (N-5): 위임 기록의 반복 수가 정수가 아니면 표에 그 값을 그대로 쓰지 않는다
        row = delegated("fb-record-nonint", with_key, '{"family": "google", "feedback_iterations": "많이"}\n')
        results.append(("F15 위임 기록의 반복 수가 정수가 아님 → '기록 없음'", row.get(col) == "기록 없음", str(row.get(col))))
    finally:
        shutil.rmtree(base, ignore_errors=True)


def main() -> int:
    results: list[tuple[str, bool, str]] = []
    b_cases(results)
    r_cases(results)
    kf_cases(results)
    all_good = True
    for name, good, detail in results:
        all_good &= good
        print(f"{'OK ' if good else 'BAD'} {name}{('  — ' + detail) if detail else ''}")
    print("결과: 모든 사례가 기대와 일치" if all_good else "결과: 기대와 다른 사례가 있다")
    return 0 if all_good else 1


if __name__ == "__main__":
    sys.exit(main())
