"""하네스 자기 검사: KPI 보정 고리 T2 (HD-35, WO-H34).

계약
A 기록: mark.py `review <검토자> --round N --defects K` → review_run, `budget-extend <이유>` · `budget-stop <이유>` → budget_extend ·
  budget_stop (이유 필수). run_gates.count_failed_cases(JUnit 경로) → 실패 + 오류 사례 수, 파일이 없으면 None.
B 사용량 스키마 (harness/usage.py): record(**필드) 가 model_usage 이벤트를 검증해 남긴다. 같은 (agent, run_id) 는 한 번만
  (두 번째는 None, 기록 안 함). 필수: wo · role · agent · run_id. 토큰 · 모델 · 시간은 모르면 None(unknown).
  cap = {kind: time|rounds|budget, value, unit}, stop = {reason, by}. stop.reason == "cap" 이면 cap 필수.
  tokens = {input, output, cache_read, cache_in_input} — 공급자별 캐시 포함 관계를 보존한다.
C 집계 (kpi.py):
  - “## 사용량 (역할 × 에이전트)”: 위임 요청 수(중복 run 한 번), 모델 내부 호출 수, 토큰(모르는 run 이 있으면 “부분 (확인 a/b)”),
    캐시 입력 포함 여부, 상한 도달 수. 공급자를 넘어 합산하지 않는다(행 = 역할 × 에이전트).
  - “## 기본값 보정 제안 (적용은 사용자 확인)”: 묶음 = 티어 · 위험도 · 역할 · 에이전트 · 모델 · 실행 경로 · 권한.
    티어나 위험도를 모르는 기록은 제외하고 그 수를 적는다. 관측 단위는 작업(wo, task) — 시간 상한은 작업의 첫 요청 시간.
  - p75_proposal(관측): 작업 중복은 처음 것만. 5개 미만 → “표본 부족”. 상한 도달 관측은 모든 값보다 큰 것으로 정렬하고
    최근접 순위 k = ceil(0.75·n) 번째가 상한 도달이면 “산정 불가”, 아니면 그 값으로 “제안”.
  - 작업 지시서별 표에 검토 회차 · 계약 안 결함 · 예산 연장 · 예산 중단 · 실패 사례(첫→마지막).
  - kpi.py 는 설정 파일을 바꾸지 않는다(제안만).
  - 표기: 토큰 · 호출 수는 구분자 없는 정수, 제안 값은 기록 단위(초) 그대로. p75_proposal 반환 = {"status", "value", "n"}
    (n = 중복을 뺀 작업 수, 제안이 아니면 value = None). time_observations(이벤트) → {묶음 키: [{"task", "value", "censored"}]}.
    task 는 (wo, task) 를 구분하는 값, seconds 를 모르는 기록과 time_scope 가 "model_activity" 가 아닌 기록은 빠진다.
  - 수집기는 time_scope 를 남긴다: AGY · Codex = "model_activity", 위임 도구 결과 = "delegation_total" (C11~C13 · D08).
D 수집기 (adapters/tools/collect_usage.py, Core 밖 · 소비 프로젝트에 배포, 지정한 파일만 읽는 오프라인 도구,
  자기 저장소의 harness 로 자기 저장소 reports/ 에 기록 — WO-H40):
  --agy-transcript <transcript.jsonl> | --codex-session <rollout.jsonl> | --delegation-job <위임 도구의 .jobs/<job> 폴더> 중 하나와
  --wo · --role (필수), --task · --round · --tier · --risk · --exec-path · --model · --cap-kind/--cap-value/--cap-unit ·
  --stop-reason/--stop-by. run_id: AGY = transcript 의 대화 폴더 이름, Codex = session_meta.id, 위임 도구 = result.json conversation_id
  (agent 는 result.json 의 agent). 같은 run 을 다른 출처로 다시 넣으면 기록하지 않고 “중복” 출력, 종료 0.
  Codex 는 turn_context 의 sandbox_policy · approval_policy 를 condition.permission 에 남기고, danger-full-access 면 “경고” 출력.
  파일이 없거나 형식을 못 읽으면 종료 2, 기록 없음.

사용: python harness/selftest/check_kpi_t2_cases.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable
sys.path.insert(0, str(ROOT / "harness"))


def make_project(base: Path, name: str) -> Path:
    proj = base / name
    shutil.copytree(ROOT / "harness", proj / "harness", ignore=shutil.ignore_patterns("__pycache__", "selftest"))
    shutil.copytree(ROOT / "adapters", proj / "adapters", ignore=shutil.ignore_patterns("__pycache__"))
    (proj / "config").mkdir()
    (proj / "config" / "gates.toml").write_text('[harness]\nadapter = "python"\n', encoding="utf-8", newline="\n")
    (proj / "reports").mkdir()
    for cmd in (["git", "init", "-q", "-b", "main"], ["git", "config", "user.email", "t@example.invalid"],
                ["git", "config", "user.name", "t"], ["git", "add", "-A"], ["git", "commit", "-qm", "base"]):
        subprocess.run(cmd, cwd=proj, capture_output=True, text=True, check=True)
    return proj


def run(proj: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([PY, *args], cwd=proj, capture_output=True, text=True, encoding="utf-8", errors="replace")


def py(proj: Path, code: str) -> subprocess.CompletedProcess[str]:
    head = "import sys, json; sys.path.insert(0, 'harness')\n"
    return run(proj, "-c", head + code)


def events(proj: Path) -> list[dict]:
    p = proj / "reports" / "events.jsonl"
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()] if p.exists() else []


def write_events(proj: Path, evs: list[dict]) -> None:
    lines = [json.dumps({"ts": "2026-10-18T10:00:00Z", "head": "abc1234", "branch": "main", **e}, ensure_ascii=False)
             for e in evs]
    (proj / "reports" / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def kpi_section(proj: Path, title: str) -> tuple[list[dict[str, str]], str]:
    r = run(proj, "harness/kpi.py")
    if r.returncode != 0:
        return [], f"kpi.py 실패: {(r.stdout + r.stderr)[-200:]}"
    text = (proj / "reports" / "kpi.md").read_text(encoding="utf-8")
    if f"## {title}" not in text:
        return [], f"절 없음: {title}"
    section = text.split(f"## {title}", 1)[1].split("\n## ", 1)[0]
    rows = [l for l in section.splitlines() if l.startswith("|")]
    if len(rows) < 2:
        return [], section
    head = [c.strip() for c in rows[0].strip("|").split("|")]
    return [dict(zip(head, [c.strip() for c in l.strip("|").split("|")])) for l in rows[2:]], section


def usage(run_id: str, agent: str = "agy", **f: object) -> dict:
    e = {"kind": "model_usage", "wo": "WO-X1", "task": "T1", "role": "implementer", "agent": agent, "model": "m1",
         "run_id": run_id, "round": 1, "started": "2026-10-18T10:00:00Z", "ended": "2026-10-18T10:05:00Z",
         "seconds": 300.0, "time_scope": "model_activity", "time_source": "measured_activity_intervals", "model_calls": 10,
         "tokens": {"input": 100, "output": 10, "cache_read": 50, "cache_in_input": False},
         "cap": None, "stop": None, "tier": "T2", "risk": "low",
         "condition": {"exec_path": "orca", "permission": "accept-edits"}, "source": "fixture"}
    e.update(f)
    return e


CAP = {"kind": "time", "value": 600, "unit": "s"}
STOP = {"reason": "cap", "by": "lead"}


# ── 수집기 입력 견본 ──
def agy_transcript(base: Path, conv: str) -> Path:
    d = base / "brain" / conv / ".system_generated" / "logs"
    d.mkdir(parents=True)
    rows = [{"step_index": 0, "source": "USER_EXPLICIT", "created_at": "2026-10-18T08:34:28Z", "status": "DONE"},
            {"step_index": 1, "source": "MODEL", "created_at": "2026-10-18T08:34:40Z", "status": "DONE",
             "input_tokens": 1000, "cache_read_tokens": 500, "output_tokens": 100},
            {"step_index": 2, "source": "MODEL", "created_at": "2026-10-18T08:37:33Z", "status": "DONE",
             "input_tokens": 2000, "cache_read_tokens": 700, "output_tokens": 300}]
    p = d / "transcript.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return p


def codex_session(base: Path, sid: str, sandbox: str) -> Path:
    rows = [{"timestamp": "2026-10-18T11:08:45.098Z", "type": "session_meta", "payload": {"id": sid, "cli_version": "0.160.0"}},
            {"timestamp": "2026-10-18T11:08:46.000Z", "type": "turn_context",
             "payload": {"model": "gpt-6.1-sol", "effort": "medium", "sandbox_policy": {"type": sandbox},
                         "approval_policy": "never"}},
            {"timestamp": "2026-10-18T11:09:00.000Z", "type": "event_msg", "payload": {"type": "token_count", "info": {
                "total_token_usage": {"input_tokens": 1000, "cached_input_tokens": 600, "output_tokens": 50,
                                      "reasoning_output_tokens": 5, "total_tokens": 1050}}}},
            {"timestamp": "2026-10-18T11:11:38.284Z", "type": "event_msg", "payload": {"type": "token_count", "info": {
                "total_token_usage": {"input_tokens": 5000, "cached_input_tokens": 4000, "output_tokens": 200,
                                      "reasoning_output_tokens": 20, "total_tokens": 5200}}}}]
    p = base / f"rollout-{sid}.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return p


def agent_kit_job(base: Path, conv: str) -> Path:
    d = base / ".jobs" / "wo-x1-a"
    d.mkdir(parents=True)
    (d / "result.json").write_text(json.dumps({"agent": "agy", "conversation_id": conv, "duration_seconds": 190.0,
                                               "num_turns": 1, "usage": {"input_tokens": 3000, "output_tokens": 400,
                                                                         "cache_read_tokens": 1200, "total_tokens": 3400}}),
                                   encoding="utf-8-sig")
    (d / "meta.json").write_text(json.dumps({"agent": "agy", "model": "gemini-3.8-flash-high",
                                             "model_reported": "gemini-3.8-flash-high"}), encoding="utf-8-sig")
    return d


def main() -> int:
    base = Path(tempfile.mkdtemp(prefix="kpi-t2-"))
    rows: list[tuple[str, bool, str]] = []

    def case(name: str, good: bool, detail: str = "") -> None:
        rows.append((name, good, detail.replace("\n", " | ")[:170]))

    try:
        # ── A 기록 ──
        p = make_project(base, "a")
        r = run(p, "harness/mark.py", "review", "codex", "--wo", "WO-X1", "--round", "1", "--defects", "2")
        e = events(p)
        case("A01 review → review_run (검토자 · 회차 · 계약 안 결함)", r.returncode == 0 and len(e) == 1
             and e[0].get("kind") == "review_run" and e[0].get("round") == 1 and e[0].get("contract_defects") == 2
             and e[0].get("reviewer") == "codex" and e[0].get("wo") == "WO-X1", f"{r.returncode} {e}")
        r1 = run(p, "harness/mark.py", "review", "codex", "--wo", "WO-X1", "--defects", "1")
        r2 = run(p, "harness/mark.py", "review", "codex", "--wo", "WO-X1", "--round", "1", "--defects", "-1")
        case("A02 review: --round 없음 · 음수 결함 → 2, 기록 없음", r1.returncode == 2 and r2.returncode == 2
             and len(events(p)) == 1, f"{r1.returncode},{r2.returncode} n={len(events(p))}")
        # WO-H37 (결정 a): 모르는 결함 수를 0 으로 남기지 않는다
        r1 = run(p, "harness/mark.py", "review", "codex", "--wo", "WO-X1", "--round", "2")
        r2 = run(p, "harness/mark.py", "note", "메모", "--wo", "WO-X1", "--defects", "0")
        case("A06 review: --defects 생략 → 2 · 다른 동작에 --defects → 2, 기록 없음", r1.returncode == 2
             and r2.returncode == 2 and len(events(p)) == 1, f"{r1.returncode},{r2.returncode} n={len(events(p))}")
        r = run(p, "harness/mark.py", "budget-extend", "실패 사례 4→2 감소", "--wo", "WO-X1")
        e = (events(p) or [{}])[-1]
        case("A03 budget-extend → budget_extend + 이유", r.returncode == 0 and e.get("kind") == "budget_extend"
             and e.get("reason") == "실패 사례 4→2 감소", f"{r.returncode} {e}")
        r1 = run(p, "harness/mark.py", "budget-stop", "", "--wo", "WO-X1")
        r2 = run(p, "harness/mark.py", "budget-stop", "  ", "--wo", "WO-X1")
        r3 = run(p, "harness/mark.py", "budget-stop", "수렴 없음", "--wo", "WO-X1")
        case("A04 budget-stop: 빈 이유 → 2, 이유 있으면 budget_stop", r1.returncode == 2 and r2.returncode == 2
             and r3.returncode == 0 and (events(p) or [{}])[-1].get("kind") == "budget_stop", f"{r1.returncode},{r2.returncode},{r3.returncode}")
        junit = p / "reports" / "junit.xml"
        junit.write_text('<testsuites><testsuite><testcase name="a"/><testcase name="b"><failure/></testcase>'
                         '<testcase name="c"><error/></testcase><testcase name="d"><skipped/></testcase></testsuite></testsuites>',
                         encoding="utf-8")
        r = py(p, "import run_gates as g; print(json.dumps([g.count_failed_cases('reports/junit.xml'), "
                  "g.count_failed_cases('reports/none.xml')]))")
        case("A05 count_failed_cases: 실패 1 + 오류 1 = 2, 파일 없음 → None",
             r.returncode == 0 and r.stdout.strip().splitlines()[-1:] == ["[2, null]"], (r.stdout + r.stderr)[-160:])

        # ── B 사용량 스키마 ──
        p = make_project(base, "b")
        base_fields = {k: v for k, v in usage("conv-1").items() if k != "kind"}
        code = ("import usage as u\n"
                f"f = json.loads({json.dumps(json.dumps(base_fields))})\n"
                "a = u.record(**f); b = u.record(**f)\n"
                "print(json.dumps([a is not None, b is None]))")
        r = py(p, code)
        e = events(p)
        case("B01 record → model_usage 1건, 같은 (agent, run_id) 두 번째 → None (중복 기록 안 함)",
             r.returncode == 0 and r.stdout.strip().endswith("[true, true]") and len(e) == 1
             and e[0].get("kind") == "model_usage" and e[0].get("run_id") == "conv-1", (r.stdout + r.stderr)[-160:])
        bad = []
        for drop in ("wo", "role", "agent", "run_id"):
            f = dict(base_fields)
            f.pop(drop)
            f["run_id"] = f.get("run_id", "x") + "-" + drop if drop != "run_id" else None
            if drop == "run_id":
                f.pop("run_id")
            r = py(p, f"import usage as u\ntry:\n    u.record(**json.loads({json.dumps(json.dumps(f))}))\n    print('NO')\n"
                      "except ValueError:\n    print('VE')")
            bad.append(r.stdout.strip()[-2:])
        case("B02 필수(wo · role · agent · run_id) 빠짐 → ValueError, 기록 없음", bad == ["VE"] * 4 and len(events(p)) == 1, str(bad))
        f = dict(base_fields, run_id="conv-2", tokens=None, model=None, seconds=None, model_calls=None)
        r = py(p, f"import usage as u\nprint(u.record(**json.loads({json.dumps(json.dumps(f))})) is not None)")
        case("B03 토큰 · 모델 · 시간 · 호출 수 unknown(None) 허용", r.stdout.strip().endswith("True") and len(events(p)) == 2,
             (r.stdout + r.stderr)[-160:])
        outs = []
        for extra in ({"stop": STOP}, {"cap": {"kind": "minutes", "value": 10, "unit": "m"}},
                      {"cap": CAP, "stop": {"reason": "", "by": "lead"}}):
            f = dict(base_fields, run_id="conv-bad-" + str(len(outs)), **extra)
            r = py(p, f"import usage as u\ntry:\n    u.record(**json.loads({json.dumps(json.dumps(f))}))\n    print('NO')\n"
                      "except ValueError:\n    print('VE')")
            outs.append(r.stdout.strip()[-2:])
        case("B04 stop=cap 인데 cap 없음 · 모르는 cap 종류 · 빈 중단 이유 → ValueError", outs == ["VE"] * 3
             and len(events(p)) == 2, str(outs))
        f = dict(base_fields, run_id="conv-3", cap=CAP, stop=STOP)
        r = py(p, f"import usage as u\nprint(u.record(**json.loads({json.dumps(json.dumps(f))})) is not None)")
        case("B05 cap + stop(cap) → 기록", r.stdout.strip().endswith("True"), (r.stdout + r.stderr)[-120:])

        # ── C 집계 ──
        p = make_project(base, "c")
        evs = [usage("r1"), usage("r1"),  # 같은 run 이 두 줄 (중복)
               usage("r2", tokens=None, model_calls=None),
               usage("r3", cap=CAP, stop=STOP, seconds=600.0),
               usage("c1", agent="codex", model="gpt-6.1-sol",
                     tokens={"input": 5000, "output": 200, "cache_read": 4000, "cache_in_input": True}),
               {"kind": "review_run", "wo": "WO-X1", "reviewer": "codex", "round": 1, "contract_defects": 0},
               {"kind": "budget_extend", "wo": "WO-X1", "reason": "감소"},
               {"kind": "budget_stop", "wo": "WO-X1", "reason": "수렴 없음"},
               {"kind": "gate_run", "wo": "WO-X1", "role": "implementer", "passed": False, "first_failed": "G2",
                "seconds": 3, "g0_violations": 0, "review_required": [], "failed_cases": 4},
               {"kind": "gate_run", "wo": "WO-X1", "role": "implementer", "passed": True, "first_failed": None,
                "seconds": 3, "g0_violations": 0, "review_required": [], "failed_cases": 0}]
        write_events(p, evs)
        cfg_before = (p / "config" / "gates.toml").read_text(encoding="utf-8")
        table, raw = kpi_section(p, "사용량 (역할 × 에이전트)")
        agy = next((t for t in table if t.get("에이전트") == "agy"), {})
        cdx = next((t for t in table if t.get("에이전트") == "codex"), {})
        case("C01 사용량: agy 위임 요청 수 3 (같은 run 중복은 한 번)", agy.get("위임 요청 수") == "3", str(agy) or raw)
        case("C02 토큰을 모르는 run 이 있으면 ‘부분 (확인 2/3)’", "부분 (확인 2/3)" in agy.get("입력", ""), str(agy))
        case("C03 상한 도달 수 1", agy.get("상한 도달") == "1", str(agy))
        case("C04 캐시 포함 관계 보존: agy 아니오 · codex 예, 공급자별 행 분리",
             agy.get("캐시 입력 포함") == "아니오" and cdx.get("캐시 입력 포함") == "예" and cdx.get("입력") == "5000",
             f"{agy.get('캐시 입력 포함')} {cdx}")
        case("C05 모델 내부 호출 수: 아는 것만 합 + 부분 표시", "부분" in agy.get("모델 내부 호출 수", ""), str(agy))
        wo_table, raw = kpi_section(p, "작업 지시서별")
        wo = next((t for t in wo_table if t.get("WO") == "WO-X1"), {})
        case("C06 작업 지시서별: 검토 회차 1 · 계약 안 결함 0 · 예산 연장 1 · 예산 중단 1 · 실패 사례 4→0",
             wo.get("검토 회차") == "1" and wo.get("계약 안 결함") == "0" and wo.get("예산 연장") == "1"
             and wo.get("예산 중단") == "1" and wo.get("실패 사례 (첫→마지막)") == "4→0", str(wo) or raw)
        case("C07 kpi.py 는 설정 파일을 바꾸지 않는다", (p / "config" / "gates.toml").read_text(encoding="utf-8") == cfg_before)

        import kpi  # noqa: E402  (순수 함수만 쓴다)
        def obs(n: int, censored: tuple[int, ...] = (), dup: bool = False) -> list[dict]:
            o = [{"task": f"t{i}", "value": float(i + 1), "censored": i in censored} for i in range(n)]
            return o + ([{"task": "t0", "value": 999.0, "censored": False}] if dup else [])
        try:
            res = [kpi.p75_proposal(obs(4)), kpi.p75_proposal([{"task": f"t{i}", "value": v, "censored": False}
                                                               for i, v in enumerate([1.0, 2.0, 3.0, 4.0, 10.0])]),
                   kpi.p75_proposal(obs(5, censored=(3, 4))), kpi.p75_proposal(obs(8, censored=(7,))),
                   kpi.p75_proposal(obs(5, dup=True))]
        except Exception as ex:  # 함수가 없을 때
            res = [repr(ex)]
        good = (len(res) == 5 and res[0].get("status") == "표본 부족" and res[1] == {"status": "제안", "value": 4.0, "n": 5}
                and res[2].get("status") == "산정 불가" and res[2].get("value") is None
                and res[3] == {"status": "제안", "value": 6.0, "n": 8} and res[4] == {"status": "제안", "value": 4.0, "n": 5})
        case("C08 p75_proposal: 4개 부족 · [1,2,3,4,10]→4 · 상한 2/5 산정 불가 · 상한 1/8→6 · 작업 중복은 처음 것만", good, str(res))

        evs = []
        for i in range(5):  # 독립 작업 5개, 각 작업은 재시도 1회 더 (재시도는 관측에서 빠져야 한다)
            evs.append(usage(f"w{i}a", task=f"T{i}", round=1, seconds=60.0 * (i + 1),
                             started=f"2026-10-18T1{i}:00:00Z"))
            evs.append(usage(f"w{i}b", task=f"T{i}", round=2, seconds=5.0, started=f"2026-10-18T1{i}:30:00Z"))
        evs.append(usage("u1", wo="WO-U1", task="U1", tier=None, risk=None))
        # WO-H35 (HD-43 §4, 결정 B): 보정 제안은 작업 전(prospective) 분류가 있는 WO 만 쓴다
        evs.append({"kind": "wo_class", "wo": "WO-X1", "tier": "T2", "risk": "low", "reason": "견본",
                    "basis": "prospective", "confirmed_by": "user"})
        p2 = make_project(base, "c2")
        write_events(p2, evs)
        try:
            groups = kpi.time_observations(evs)
        except Exception as ex:
            groups = repr(ex)
        key_ok = isinstance(groups, dict) and len(groups) == 1
        vals = sorted(o["value"] for o in next(iter(groups.values()))) if key_ok else []
        case("C09 time_observations: 묶음 1개, 작업당 첫 요청만(재시도 제외), 티어 미상 제외",
             key_ok and vals == [60.0, 120.0, 180.0, 240.0, 300.0], str(groups)[:160])
        # WO-H34 판정 뒤 추가 (리드가 diff 에서 찾은 계약 안 결함): 모르는 시간은 0 이 아니다 · 작업 키는 (wo, task) ·
        # 시간 범위가 다른 기록을 섞지 않는다 (p75 는 time_scope == "model_activity" 만)
        def tasks_of(evlist: list[dict]) -> object:
            try:
                g = kpi.time_observations(evlist)
            except Exception as ex:
                return repr(ex)
            return sorted((str(o["task"]), o["value"]) for obs_ in g.values() for o in obs_)
        got = tasks_of([usage("n1", task="A", seconds=60.0), usage("n2", task="B", seconds=None)])
        case("C11 시간을 모르는 기록은 관측에서 빠진다 (0 으로 바꾸지 않는다)",
             isinstance(got, list) and len(got) == 1 and got[0][1] == 60.0, str(got))
        got = tasks_of([usage("k1", wo="WO-A", task="T1", seconds=60.0), usage("k2", wo="WO-B", task="T1", seconds=90.0)])
        case("C12 다른 WO 의 같은 이름 작업은 별개 관측 (p75 에서도 따로 센다)",
             isinstance(got, list) and len(got) == 2 and len({t for t, _ in got}) == 2, str(got))
        try:
            same_name = kpi.p75_proposal([o for obs_ in kpi.time_observations(
                [usage(f"q{i}", wo=f"WO-Q{i}", task="T1", seconds=float(i + 1)) for i in range(5)]).values() for o in obs_])
        except Exception as ex:
            same_name = repr(ex)
        case("C12b 이름이 같은 다섯 WO 의 작업 → n = 5", isinstance(same_name, dict) and same_name.get("n") == 5, str(same_name))
        got = tasks_of([usage("s1", task="A", seconds=60.0), usage("s2", task="B", seconds=500.0, time_scope="delegation_total")])
        case("C13 time_scope 가 model_activity 가 아닌 기록은 시간 관측에서 빠진다",
             isinstance(got, list) and len(got) == 1 and got[0][1] == 60.0, str(got))
        _, raw = kpi_section(p2, "기본값 보정 제안 (적용은 사용자 확인)")
        case("C10 제안 절: 묶음 · 제안 값 240 · 티어·위험도 미상 1건 제외 표시",
             "240" in raw and "미상" in raw and "1" in raw.split("미상", 1)[1][:20], raw[:170])

        # ── D 수집기 ──
        # 공개본(비공개 도구 · dev/ 없음)에서는 수집기 실행 사례를 건너뛴다. 비공개 저장소에서 도구가 없으면 실패한다.
        if not (ROOT / "adapters" / "tools" / "collect_usage.py").exists() and not (ROOT / "dev").exists():
            print("알림: 비공개 도구가 없는 공개본이라 수집기 사례(D01~D06 · D08)를 건너뜁니다.")
        else:
            p = make_project(base, "d")
            has_tool = (p / "adapters" / "tools" / "collect_usage.py").exists()
            conv = "7eb337f8-0000-0000-0000-000000000001"
            tr = agy_transcript(base, conv)
            r = run(p, "adapters/tools/collect_usage.py", "--agy-transcript", str(tr), "--wo", "WO-X1", "--role", "implementer",
                    "--task", "T1", "--round", "1", "--tier", "T2", "--risk", "low", "--exec-path", "orca",
                    "--model", "gemini-3.8-flash-high")
            e = [x for x in events(p) if x.get("kind") == "model_usage"]
            u = e[0] if e else {}
            case("D01 AGY transcript → run_id = 대화 폴더, 토큰 합(입력 3000 · 출력 400 · 캐시 1200, 캐시 별도), 모델 호출 2, 시간",
                 has_tool and r.returncode == 0 and len(e) == 1 and u.get("agent") == "agy" and u.get("run_id") == conv
                 and u.get("tokens") == {"input": 3000, "output": 400, "cache_read": 1200, "cache_in_input": False}
                 and u.get("model_calls") == 2 and u.get("started") == "2026-10-18T08:34:28Z"
                 and u.get("ended") == "2026-10-18T08:37:33Z" and u.get("seconds") == 185.0, (r.stdout + r.stderr)[-120:] + str(u)[:60])
            job = agent_kit_job(base, conv)
            r = run(p, "adapters/tools/collect_usage.py", "--delegation-job", str(job), "--wo", "WO-X1", "--role", "implementer")
            n = len([x for x in events(p) if x.get("kind") == "model_usage"])
            case("D02 같은 run 의 위임 도구 결과를 다시 넣음 → 기록 안 함, ‘중복’, 종료 0",
                 has_tool and r.returncode == 0 and n == 1 and "중복" in r.stdout, (r.stdout + r.stderr)[-140:])
            cs = codex_session(base, "01a14eb2-0000-0000-0000-000000000002", "danger-full-access")
            r = run(p, "adapters/tools/collect_usage.py", "--codex-session", str(cs), "--wo", "WO-X1", "--role", "reviewer")
            e = [x for x in events(p) if x.get("kind") == "model_usage" and x.get("agent") == "codex"]
            u = e[0] if e else {}
            case("D03 Codex 세션 → run_id = session_meta.id, 마지막 누적 토큰(캐시 입력 포함), 모델, 권한, 샌드박스 해제 경고",
                 has_tool and r.returncode == 0 and u.get("run_id") == "01a14eb2-0000-0000-0000-000000000002"
                 and u.get("tokens") == {"input": 5000, "output": 200, "cache_read": 4000, "cache_in_input": True}
                 and u.get("model") == "gpt-6.1-sol" and "danger-full-access" in str(u.get("condition", {}).get("permission"))
                 and "경고" in r.stdout, (r.stdout + r.stderr)[-120:] + str(u)[:60])
            cs2 = codex_session(base, "01a14eb2-0000-0000-0000-000000000003", "workspace-write")
            r = run(p, "adapters/tools/collect_usage.py", "--codex-session", str(cs2), "--wo", "WO-X1", "--role", "reviewer")
            case("D04 workspace-write Codex 세션 → 경고 없음", has_tool and r.returncode == 0 and "경고" not in r.stdout,
                 (r.stdout + r.stderr)[-120:])
            before = len(events(p))
            r1 = run(p, "adapters/tools/collect_usage.py", "--agy-transcript", str(base / "nope.jsonl"), "--wo", "WO-X1", "--role", "implementer")
            bad = base / "bad.jsonl"
            bad.write_text("not json\n", encoding="utf-8")
            r2 = run(p, "adapters/tools/collect_usage.py", "--codex-session", str(bad), "--wo", "WO-X1", "--role", "reviewer")
            r3 = run(p, "adapters/tools/collect_usage.py", "--agy-transcript", str(tr))
            case("D05 없는 파일 · 못 읽는 형식 · --wo/--role 없음 → 2, 기록 없음",
                 has_tool and (r1.returncode, r2.returncode, r3.returncode) == (2, 2, 2) and len(events(p)) == before,
                 f"{r1.returncode},{r2.returncode},{r3.returncode}")
            r = run(p, "adapters/tools/collect_usage.py", "--agy-transcript", str(tr), "--wo", "WO-X1", "--role", "implementer",
                    "--cap-kind", "time", "--cap-value", "600", "--cap-unit", "s", "--stop-reason", "cap", "--stop-by", "lead")
            case("D06 같은 transcript 다시 → 중복 (cap 인자가 달라도 run 은 한 번)", has_tool and "중복" in r.stdout
                 and len(events(p)) == before, (r.stdout + r.stderr)[-120:])
            scopes = {(x.get("agent"), x.get("time_scope")) for x in events(p) if x.get("kind") == "model_usage"}
            p3 = make_project(base, "d3")
            job3 = agent_kit_job(base / "d3job", "conv-only-job") if (base / "d3job").mkdir() is None else None
            r = run(p3, "adapters/tools/collect_usage.py", "--delegation-job", str(job3), "--wo", "WO-X1", "--role", "implementer")
            jscope = [x.get("time_scope") for x in events(p3) if x.get("kind") == "model_usage"]
            case("D08 time_scope: AGY · Codex = transcript_span, 위임 도구 결과 = delegation_total",
                 has_tool and ("agy", "transcript_span") in scopes and ("codex", "transcript_span") in scopes
                 and jscope == ["delegation_total"], f"{scopes} {jscope} {(r.stdout + r.stderr)[-80:]}")
        case("D07 수집기는 Core 밖 (harness/ 에 공급자 로그 형식 문자열이 없다)",
             not any(s in (ROOT / "harness" / f).read_text(encoding="utf-8")
                     for f in ("kpi.py", "events.py", "usage.py")
                     if (ROOT / "harness" / f).exists() for s in ("turn_context", "cache_read_tokens\"", "conversation_id"))
             and not (ROOT / "harness" / "collect_usage.py").exists())
    finally:
        shutil.rmtree(base, ignore_errors=True)

    all_good = True
    for name, good, detail in rows:
        all_good &= good
        print(f"{'OK ' if good else 'BAD'} {name}  — {detail}")
    print("결과: 모든 사례가 기대와 일치" if all_good else "결과: 기대와 다른 사례가 있다")
    return 0 if all_good else 1


if __name__ == "__main__":
    sys.exit(main())
