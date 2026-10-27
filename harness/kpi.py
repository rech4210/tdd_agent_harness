"""KPI 집계: reports/events.jsonl + reports/spec_issues.md → reports/kpi.md

원칙
- 핵심 지표는 스크립트가 자동으로 남긴 이벤트에서만 계산한다 (자기 보고 · 사람 기억 불신, P5).
- 사람이 남긴 mark.py 기록은 '보조'로 따로 표시한다. 없어도 표는 채워진다.
- 명세 변경 건수 하나로 판단하지 않는다. 여러 지표를 함께 본다.

사용: python harness/kpi.py
"""

from __future__ import annotations

import argparse
import math
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from auto_approval import is_valid_auto  # step.py 와 같은 함수 객체 (WO-H80)
from events import ROOT, read_events

ISSUES = ROOT / "reports" / "spec_issues.md"
OUT = ROOT / "reports" / "kpi.md"
OUT_TIMING = ROOT / "reports" / "tdd-timing.md"


def parse_seconds(sec: Any) -> float | None:
    if isinstance(sec, bool):
        return None
    if not isinstance(sec, (int, float)):
        return None
    if math.isnan(sec) or math.isinf(sec):
        return None
    if sec < 0:
        return None
    return float(sec)


def is_proven_activity(e: Any) -> bool:
    if not isinstance(e, dict):
        return False
    return (
        e.get("time_scope") == "model_activity"
        and e.get("time_source") == "measured_activity_intervals"
    )


def parse_ts_safe(val: Any) -> datetime | None:
    if not isinstance(val, str) or not val.strip():
        return None
    try:
        s = val.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except Exception:
        return None


def ts(e: dict) -> datetime:
    return datetime.strptime(e["ts"], "%Y-%m-%dT%H:%M:%SZ")


def minutes(a: datetime, b: datetime) -> float:
    return round((b - a).total_seconds() / 60, 1)


def full_run(e: dict) -> bool:
    return not e.get("only")


def is_verdict_run(e: dict) -> bool:
    if not isinstance(e, dict) or e.get("kind") != "gate_run":
        return False
    if "purpose" in e:
        return e.get("purpose") == "verdict"
    return not e.get("only")


def issued_at(wo: str) -> datetime | None:
    """지시서 발행 시각 = 기준 태그(예: WO-02 → wo02-base)의 태그 시각(UTC). 없으면 None."""
    import subprocess
    from datetime import timezone

    from adapter_loader import harness_settings

    # C8: 기준 태그 이름 규칙은 설정으로 (기본 "{compact}-base": WO-02 → wo02-base)
    pattern = harness_settings().get("base_tag", "{compact}-base")
    tag = pattern.format(compact=wo.lower().replace("-", ""), wo=wo)
    p = subprocess.run(
        ["git", "for-each-ref", f"refs/tags/{tag}", "--format=%(taggerdate:iso-strict)"],
        cwd=ROOT, capture_output=True, text=True,
    )
    text = p.stdout.strip()
    if not text:
        return None
    return datetime.fromisoformat(text).astimezone(timezone.utc).replace(tzinfo=None)


def assignments() -> dict[str, dict]:
    import tomllib

    from paths import ASSIGNMENTS as path
    return tomllib.loads(path.read_text(encoding="utf-8")).get("slice", {}) if path.exists() else {}


def wait_stats(evs: list[dict]) -> tuple[str, int, int]:
    wait_starts = [e for e in evs if e.get("kind") == "human_wait_start"]
    wait_ends = [e for e in evs if e.get("kind") == "human_wait_end"]
    if not wait_starts and not wait_ends:
        return "기록 없음", 0, 0

    open_wait: dict[str, list[datetime]] = defaultdict(list)
    total_wait = 0.0
    for e in evs:
        if e.get("kind") == "human_wait_start":
            stage = e.get("stage", "")
            open_wait[stage].append(ts(e))
        elif e.get("kind") == "human_wait_end":
            stage = e.get("stage", "")
            if open_wait[stage]:
                start_t = open_wait[stage].pop(0)
                total_wait += minutes(start_t, ts(e))

    open_count = sum(len(v) for v in open_wait.values())
    total_wait = round(total_wait, 1)
    if open_count > 0:
        wait_text = f"{total_wait:.1f} (진행 중 {open_count})"
    else:
        wait_text = f"{total_wait:.1f}"

    blocked_count = len(wait_starts)
    decisions_sum = sum(int(e.get("decisions") or 0) for e in wait_ends)
    return wait_text, blocked_count, decisions_sum


def human_minutes(evs: list[dict]) -> float | None:
    open_: dict[str, datetime] = {}
    total = 0.0
    pairs = 0
    for e in evs:
        if e.get("kind") == "human_start" and e.get("stage"):
            open_[e["stage"]] = ts(e)
        elif e.get("kind") == "human_end" and e.get("stage") and e["stage"] in open_:
            start_t = open_.pop(e["stage"])
            total += minutes(start_t, ts(e))
            pairs += 1
    if pairs == 0:
        return None
    return round(total, 1)


def delegation_feedback_count(wo: str, default_count: int) -> str:
    del_file = ROOT / "config" / "delegation.toml"
    if not del_file.is_file():
        return str(default_count)
    try:
        import tomllib

        del_doc = tomllib.loads(del_file.read_text(encoding="utf-8"))
        del_sec = del_doc.get("delegation", {})
        if not isinstance(del_sec, dict):
            return str(default_count)
        feedback_key = del_sec.get("feedback_key")
        record_tmpl = del_sec.get("record")
        if not (isinstance(feedback_key, str) and feedback_key and isinstance(record_tmpl, str) and "{job}" in record_tmpl):
            return str(default_count)
    except Exception:
        return str(default_count)

    wo_file = ROOT / "work_orders" / f"{wo}.md"
    if not wo_file.is_file():
        return str(default_count)
    try:
        text = wo_file.read_text(encoding="utf-8")
    except Exception:
        return str(default_count)
    m = re.search(r"```yaml\n(.*?)```", text, re.S)
    if not m:
        return str(default_count)
    job_val = None
    for line in m.group(1).splitlines():
        km = re.match(r"^([a-z_]+):\s*(\S+)", line)
        if km and km.group(1) == "job":
            job_val = km.group(2)
            break
    if not job_val:
        return str(default_count)

    if not re.fullmatch(r"[A-Za-z0-9._-]+", job_val) or job_val in (".", ".."):
        return "기록 없음"

    if (
        Path(record_tmpl).is_absolute()
        or record_tmpl.startswith("/")
        or record_tmpl.startswith("\\")
        or bool(re.match(r"^[A-Za-z]:", record_tmpl))
        or ".." in Path(record_tmpl).parts
        or ".." in record_tmpl.replace("\\", "/").split("/")
    ):
        return "기록 없음"

    rel_record = record_tmpl.replace("{job}", job_val)
    rec_path = ROOT / rel_record
    try:
        resolved_path = rec_path.resolve()
        root_resolved = ROOT.resolve()
        is_inside = (
            resolved_path.is_relative_to(root_resolved)
            and resolved_path != root_resolved
        )
    except Exception:
        is_inside = False

    if not is_inside or not resolved_path.is_file():
        return "기록 없음"

    try:
        import json

        rec_text = resolved_path.read_bytes().decode("utf-8-sig")
        rec_data = json.loads(rec_text)
        if not isinstance(rec_data, dict):
            return "기록 없음"
        if feedback_key not in rec_data:
            return "기록 없음"
        val = rec_data[feedback_key]
        if type(val) is int and val >= 0:
            return str(val)
        return "기록 없음"
    except Exception:
        return "기록 없음"


VALID_TIERS = {"T1", "T2", "T3"}
VALID_RISKS = {"low", "medium", "high"}
VALID_BASES = {"prospective", "retrospective"}


def wo_class_for(events: list[dict], wo: str) -> dict[str, str] | None:
    """작업 지시서의 유효한 분류 정보 {"tier", "risk", "basis"}를 반환한다."""
    last_prospective = None
    last_retrospective = None

    for e in events:
        if not isinstance(e, dict):
            continue
        if e.get("kind") != "wo_class" or e.get("wo") != wo:
            continue
        by = e.get("confirmed_by")
        if not isinstance(by, str) or not by.strip():
            continue
        tier = e.get("tier")
        risk = e.get("risk")
        basis = e.get("basis")
        if tier not in VALID_TIERS or risk not in VALID_RISKS or basis not in VALID_BASES:
            continue

        item = {"tier": str(tier), "risk": str(risk), "basis": str(basis)}
        if basis == "prospective":
            last_prospective = item
        elif basis == "retrospective":
            last_retrospective = item

    if last_prospective is not None:
        return last_prospective
    return last_retrospective


def per_wo(events: list[dict]) -> list[dict]:
    by_wo: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        if e.get("wo"):
            by_wo[e["wo"]].append(e)
    rows = []
    for wo, evs in sorted(by_wo.items()):
        cf = wo_class_for(events, wo)
        if cf:
            basis_str = "작업 전" if cf.get("basis") == "prospective" else "소급"
            class_str = f"{cf.get('tier')} · {cf.get('risk')} · {basis_str}"
        else:
            class_str = "—"
        gate_runs = [e for e in evs if e.get("kind") == "gate_run"]
        verdict_runs = [e for e in gate_runs if is_verdict_run(e)]
        self_check_runs = [e for e in gate_runs if not is_verdict_run(e)]

        impl = [e for e in verdict_runs if e.get("role") == "implementer"]
        fb_runs = [e for e in evs if e.get("kind") == "feedback_run"]
        fb_count = delegation_feedback_count(wo, len(fb_runs))
        green_idx = next((i for i, e in enumerate(impl) if e.get("passed") and full_run(e)), None)
        violations = [e.get("g0_violations") or 0 for e in impl if e.get("g0_violations") is not None]
        review = sorted({p for e in impl for p in e.get("review_required", [])})
        # 시작점은 지시서 발행(기준 태그) 시각. 태그가 없으면 첫 이벤트로 대신하고 표에 표시한다
        issued = issued_at(wo)
        first = issued or (ts(evs[0]) if (evs and evs[0].get("ts")) else datetime.min)
        green = ts(impl[green_idx]) if (green_idx is not None and impl[green_idx].get("ts")) else None
        wait_text, blocked_count, decisions_sum = wait_stats(evs)
        human = human_minutes(evs)
        slice_id = next((e.get("slice") for e in impl if e.get("slice")), "")
        a = assignments().get(slice_id, {})

        amends_by_run: dict[str, str] = {}
        for e in evs:
            if e.get("kind") == "cause_amend":
                rf = e.get("run_file")
                if rf and e.get("cause"):
                    amends_by_run[rf] = e["cause"]

        impl_cause = 0
        test_cause = 0
        harness_cause = 0
        for e in verdict_runs:
            rf = e.get("run_file")
            eff_cause = amends_by_run.get(rf) or e.get("cause")
            role = e.get("role")
            if role == "implementer" and (eff_cause == "implementer" or not eff_cause):
                impl_cause += 1
            elif eff_cause == "test" or (not eff_cause and role == "test_author"):
                test_cause += 1
            elif eff_cause == "harness":
                harness_cause += 1

        amended_runs = {e.get("run_file") for e in verdict_runs if e.get("run_file") and e.get("run_file") in amends_by_run}
        amended_count = len(amended_runs)

        spec_confirmed_human_stops = 0
        for e in evs:
            if e.get("kind") == "human_wait_start":
                stage = e.get("stage")
                if stage not in ("H1", "H2"):
                    spec_confirmed_human_stops += 1
                elif issued is not None:
                    try:
                        if ts(e) > issued:
                            spec_confirmed_human_stops += 1
                    except Exception:
                        pass

        review_runs = [e for e in evs if e.get("kind") == "review_run"]
        round_vals = [e.get("round") for e in review_runs if e.get("round") is not None]
        review_round = max(round_vals) if round_vals else (len(review_runs) if review_runs else 0)
        contract_defects = sum(int(e.get("contract_defects") or 0) for e in review_runs)
        budget_extends = sum(1 for e in evs if e.get("kind") == "budget_extend")
        budget_stops = sum(1 for e in evs if e.get("kind") == "budget_stop")
        fc_vals = [e.get("failed_cases") for e in impl if e.get("failed_cases") is not None]
        failed_cases_str = f"{fc_vals[0]}→{fc_vals[-1]}" if fc_vals else "—"
        rows.append({
            "WO": wo,
            "분류": class_str,
            # 배정이 다른 슬라이스끼리 비교할 때 섞이지 않도록 모델을 함께 적는다
            "배정 (테스트 / 구현)": f"{a.get('test_author', '?')} / {a.get('implementer', '?')}" if a else "—",
            "구현 실행 수": len(impl),
            "자체 점검 실행 수": str(len(self_check_runs)),
            "판정 전 피드백 실행 수": fb_count,
            "전체 통과까지 실행 수": (green_idx + 1) if green_idx is not None else "미통과",
            "첫 실행 통과": "예" if impl and impl[0].get("passed") and full_run(impl[0]) else "아니오",
            "실패가 가장 많은 게이트": most_common([e.get("first_failed") for e in impl if e.get("first_failed")]),
            "G0 위반(건)": sum(violations),
            "리드타임(분)": (f"{minutes(first, green)}" + ("" if issued else " (태그 없음, 첫 이벤트 기준)")) if green else "—",
            "기계 실행 시간(분)": round(sum(e.get("seconds", 0) for e in impl) / 60, 1),
            "사람 검토 필요 경로": ", ".join(review) or "없음",
            "구현자 원인 회차": str(impl_cause),
            "테스트 · 재잠금 원인 회차": str(test_cause),
            "하네스 원인 회차": str(harness_cause),
            "원인 정정": str(amended_count),
            "max_rounds 산정 회차": str(impl_cause),
            "검토 회차": str(review_round),
            "계약 안 결함": str(contract_defects),
            "예산 연장": str(budget_extends),
            "예산 중단": str(budget_stops),
            "실패 사례 (첫→마지막)": failed_cases_str,
            "사람 대기(분, 자동)": wait_text,
            "막힌 지점 수": blocked_count,
            "명세 확정 후 사람 멈춤": str(spec_confirmed_human_stops),
            "결정 수": decisions_sum,
            "사람 시간(분, 보조)": "기록 없음" if human is None else f"{human:.1f}",
        })
    return rows


def approvals(events: list[dict]) -> list[dict]:
    """테스트 승인 대기: 잠금마다 직전 잠금 이후의 H3 근거(사람 H3 끝 · 유효 자동 승인, step.py 와 같은 규칙)를
    짝짓는다. 마지막 근거의 WO 로 작성 실행 · 사람 대기를 찾고, 근거가 없으면 '기록 없음'(0 아님) (WO-H77)."""
    rows: list[dict] = []
    approvals_in_interval: list[dict] = []
    h3_pairs_in_interval: dict[str, list[tuple[dict, dict]]] = defaultdict(list)
    open_h3_starts: dict[str, list[dict]] = defaultdict(list)
    last_author_by_wo: dict[str, dict] = {}
    last_author_any: dict | None = None

    for e in events:
        if not isinstance(e, dict):
            continue
        kind = e.get("kind")
        if kind == "gate_run" and e.get("role") == "test_author":
            wo = e.get("wo")
            if wo:
                last_author_by_wo[wo] = e
            last_author_any = e
        elif kind == "human_wait_start" and e.get("stage") == "H3":
            wo = e.get("wo")
            if wo:
                open_h3_starts[wo].append(e)
        elif kind == "human_wait_end" and e.get("stage") == "H3":
            wo = e.get("wo")
            approvals_in_interval.append({"type": "사람", "wo": wo, "event": e})
            if wo and open_h3_starts[wo]:
                start_e = open_h3_starts[wo].pop(0)
                h3_pairs_in_interval[wo].append((start_e, e))
        elif kind == "auto_approval":
            wo = e.get("wo")
            risk = (wo_class_for(events, wo) or {}).get("risk") if wo else None
            if is_valid_auto(e, risk):
                approvals_in_interval.append({"type": "자동", "wo": wo, "event": e})
        elif kind == "test_lock":
            if approvals_in_interval:
                last_appr = approvals_in_interval[-1]
                appr_type = last_appr["type"]
                appr_wo = last_appr["wo"] or "기록 없음"
            else:
                appr_type = "기록 없음"
                appr_wo = "기록 없음"

            if appr_wo != "기록 없음":
                pairs = h3_pairs_in_interval.get(appr_wo, [])
                if pairs:
                    last_pair = pairs[-1]
                    wait_val = f"{minutes(ts(last_pair[0]), ts(last_pair[1])):.1f}"
                else:
                    wait_val = "기록 없음"
                author_e = last_author_by_wo.get(appr_wo)
            else:
                wait_val = "기록 없음"
                author_e = last_author_any

            elapsed = minutes(ts(author_e), ts(e)) if author_e else "작성자 실행 기록 없음"

            rows.append({
                "잠금 시각(UTC)": e["ts"],
                "잠금 커밋": e["head"],
                "테스트 파일 수": e.get("files", "?"),
                "작성 완료 → 잠금(분)": elapsed,
                "WO": appr_wo,
                "승인 근거": appr_type,
                "H3 사람 대기(분)": wait_val,
            })

            approvals_in_interval.clear()
            h3_pairs_in_interval.clear()
            # 기존 의미 유지: 한 작성 실행은 한 잠금에만 센다 (WO-H77 리드 보정)
            last_author_by_wo.clear()
            last_author_any = None

    return rows


def most_common(items: list[str]) -> str:
    if not items:
        return "—"
    counts: dict[str, int] = {}
    for x in items:
        counts[x] = counts.get(x, 0) + 1
    max_count = max(counts.values())
    top = [x for x, c in counts.items() if c == max_count]
    return " · ".join(top)


def issues() -> list[dict]:
    if not ISSUES.exists():
        return []
    counts: dict[str, dict[str, int]] = defaultdict(lambda: {"건수": 0, "미정": 0})
    for line in ISSUES.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 7 and re.fullmatch(r"\d+", cells[0]):
            kind = cells[2]
            counts[kind]["건수"] += 1
            counts[kind]["미정"] += "미정" in cells[5]
    return [{"유형": k, **v} for k, v in sorted(counts.items())]


def table(rows: list[dict]) -> str:
    if not rows:
        return "_데이터 없음_\n"
    head = list(rows[0])
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    lines += ["| " + " | ".join(str(r[h]) for h in head) + " |" for r in rows]
    return "\n".join(lines) + "\n"


def usage_by_role_agent(events: list[dict]) -> list[dict]:
    """역할 × 에이전트별 모델 사용량 집계."""
    seen_runs = set()
    deduped = []
    for e in events:
        if not isinstance(e, dict) or e.get("kind") != "model_usage":
            continue
        agent = e.get("agent")
        run_id = e.get("run_id")
        run_key = (agent, run_id) if run_id is not None else (agent, id(e))
        if run_key in seen_runs:
            continue
        seen_runs.add(run_key)
        deduped.append(e)

    by_group: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for e in deduped:
        role = e.get("role", "")
        agent = e.get("agent", "")
        by_group[(role, agent)].append(e)

    rows = []
    for (role, agent), runs in sorted(by_group.items()):
        n_runs = len(runs)

        def format_stat(vals: list[int | None]) -> str:
            known = [v for v in vals if v is not None]
            k_cnt = len(known)
            k_sum = sum(known)
            if k_cnt == n_runs:
                return str(k_sum)
            return f"{k_sum} 부분 (확인 {k_cnt}/{n_runs})"

        calls_stat = format_stat([e.get("model_calls") for e in runs])

        in_vals = []
        out_vals = []
        cache_vals = []
        cache_in_input_flags = []
        for e in runs:
            toks = e.get("tokens")
            if isinstance(toks, dict):
                in_vals.append(toks.get("input"))
                out_vals.append(toks.get("output"))
                cache_vals.append(toks.get("cache_read"))
                if "cache_in_input" in toks and toks["cache_in_input"] is not None:
                    cache_in_input_flags.append(toks["cache_in_input"])
            else:
                in_vals.append(None)
                out_vals.append(None)
                cache_vals.append(None)

        in_stat = format_stat(in_vals)
        out_stat = format_stat(out_vals)
        cache_stat = format_stat(cache_vals)

        if True in cache_in_input_flags and False not in cache_in_input_flags:
            cache_in_input_str = "예"
        elif False in cache_in_input_flags and True not in cache_in_input_flags:
            cache_in_input_str = "아니오"
        elif True in cache_in_input_flags and False in cache_in_input_flags:
            cache_in_input_str = "혼합"
        else:
            cache_in_input_str = "—"

        sec_vals = [
            parse_seconds(e.get("seconds"))
            for e in runs
            if is_proven_activity(e) and parse_seconds(e.get("seconds")) is not None
        ]
        if sec_vals:
            min_val = round(sum(sec_vals) / 60, 1)
            if len(sec_vals) == n_runs:
                time_str = f"{min_val}"
            else:
                time_str = f"{min_val} 부분 (확인 {len(sec_vals)}/{n_runs})"
        else:
            time_str = "—"

        cap_count = 0
        for e in runs:
            stop = e.get("stop")
            if (isinstance(stop, dict) and stop.get("reason") == "cap") or stop == "cap":
                cap_count += 1

        rows.append({
            "역할": role,
            "에이전트": agent,
            "위임 요청 수": str(n_runs),
            "모델 내부 호출 수": calls_stat,
            "입력": in_stat,
            "출력": out_stat,
            "캐시": cache_stat,
            "캐시 입력 포함": cache_in_input_str,
            "모델 활동 시간(분)": time_str,
            "상한 도달": str(cap_count),
        })

    return rows


def review_cost_by_risk(events: list[dict]) -> list[dict]:
    """위험도별 검토 비용 집계."""
    seen_runs = set()
    deduped = []
    for e in events:
        if not isinstance(e, dict) or e.get("kind") != "model_usage":
            continue
        agent = e.get("agent")
        run_id = e.get("run_id")
        run_key = (agent, run_id) if run_id is not None else (agent, id(e))
        if run_key in seen_runs:
            continue
        seen_runs.add(run_key)
        deduped.append(e)

    review_roles = {"adversary", "reviewer", "mutation_auditor", "doc_auditor"}
    review_usages = [e for e in deduped if e.get("role") in review_roles]
    review_runs = [e for e in events if isinstance(e, dict) and e.get("kind") == "review_run"]

    def risk_for_wo(wo: str) -> str:
        if not wo:
            return "미상"
        cf = wo_class_for(events, wo)
        if cf and cf.get("risk"):
            return cf["risk"]
        return "미상"

    rev_by_risk: dict[str, list[dict]] = defaultdict(list)
    for e in review_runs:
        r = risk_for_wo(e.get("wo", ""))
        rev_by_risk[r].append(e)

    usage_by_risk: dict[str, list[dict]] = defaultdict(list)
    for e in review_usages:
        r = risk_for_wo(e.get("wo", ""))
        usage_by_risk[r].append(e)

    rows = []
    for risk in ["low", "medium", "high", "미상"]:
        r_runs = rev_by_risk.get(risk, [])
        u_runs = usage_by_risk.get(risk, [])
        if not r_runs and not u_runs:
            continue

        wos = {e.get("wo") for e in r_runs if e.get("wo")} | {e.get("wo") for e in u_runs if e.get("wo")}
        wo_count = len(wos)

        review_round_count = len(r_runs)
        contract_defects = sum(int(e.get("contract_defects") or 0) for e in r_runs)
        new_defects = sum(int(e["new_defects"]) for e in r_runs if e.get("new_defects") is not None)
        unknown_new_defects = sum(1 for e in r_runs if e.get("new_defects") is None)

        sec_vals = [
            parse_seconds(e.get("seconds"))
            for e in u_runs
            if is_proven_activity(e) and parse_seconds(e.get("seconds")) is not None
        ]
        k_cnt = len(sec_vals)
        n_u = len(u_runs)
        if k_cnt == 0:
            sec_time_str = "unknown"
        elif k_cnt == n_u:
            sec_time_str = f"{round(sum(sec_vals), 1):.1f}"
        else:
            sec_time_str = f"{round(sum(sec_vals), 1):.1f} 부분 (확인 {k_cnt}/{n_u})"

        in_sum = 0
        out_sum = 0
        for e in u_runs:
            toks = e.get("tokens")
            if isinstance(toks, dict):
                inp = toks.get("input")
                if isinstance(inp, int):
                    in_sum += inp
                outp = toks.get("output")
                if isinstance(outp, int):
                    out_sum += outp

        rows.append({
            "위험도": risk,
            "WO 수": str(wo_count),
            "검토 회차": str(review_round_count),
            "계약 안 결함": str(contract_defects),
            "새 결함": str(new_defects),
            "새 결함 모름": str(unknown_new_defects),
            "검토 모델 시간(초)": sec_time_str,
            "검토 입력 토큰": str(in_sum),
            "검토 출력 토큰": str(out_sum),
        })

    return rows


def time_observations(events: list[dict], prospective_only: bool = False) -> dict[tuple, list[dict[str, Any]]]:
    """이벤트에서 시간 관측치를 묶음별로 추출한다."""
    seen_runs = set()
    deduped = []
    for e in events:
        if not isinstance(e, dict) or e.get("kind") != "model_usage":
            continue
        agent = e.get("agent")
        run_id = e.get("run_id")
        run_key = (agent, run_id) if run_id is not None else (agent, id(e))
        if run_key in seen_runs:
            continue
        seen_runs.add(run_key)
        deduped.append(e)

    valid = []
    for raw_e in deduped:
        wo = raw_e.get("wo")
        cf = wo_class_for(events, wo) if wo else None
        if prospective_only:
            if not (cf and cf.get("basis") == "prospective"):
                continue

        e = dict(raw_e)
        if cf:
            if e.get("tier") is None:
                e["tier"] = cf["tier"]
            if e.get("risk") is None:
                e["risk"] = cf["risk"]

        if (
            e.get("tier") is not None
            and e.get("risk") is not None
            and parse_seconds(e.get("seconds")) is not None
            and is_proven_activity(e)
        ):
            valid.append(e)

    earliest_by_wo_task: dict[tuple, dict] = {}
    for e in valid:
        key = (e.get("wo"), e.get("task"))
        if key not in earliest_by_wo_task:
            earliest_by_wo_task[key] = e
        else:
            prev = earliest_by_wo_task[key]
            prev_started = prev.get("started") or "9999"
            curr_started = e.get("started") or "9999"
            if curr_started < prev_started:
                earliest_by_wo_task[key] = e

    groups: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for e in earliest_by_wo_task.values():
        cond = e.get("condition") if isinstance(e.get("condition"), dict) else {}
        exec_path = cond.get("exec_path") if cond.get("exec_path") is not None else e.get("exec_path")
        permission = cond.get("permission") if cond.get("permission") is not None else e.get("permission")
        group_key = (
            e.get("tier"),
            e.get("risk"),
            e.get("role"),
            e.get("agent"),
            e.get("model"),
            exec_path,
            permission,
        )
        sec = e.get("seconds")
        val = float(parse_seconds(sec))

        stop = e.get("stop") if isinstance(e.get("stop"), dict) else {}
        cap = e.get("cap") if isinstance(e.get("cap"), dict) else {}
        stop_reason = stop.get("reason") if isinstance(stop, dict) else (e.get("stop") if isinstance(e.get("stop"), str) else None)
        is_time_cap = cap.get("kind") == "time" if isinstance(cap, dict) and cap else True
        censored = (stop_reason == "cap" and is_time_cap)

        task_val = e.get("task") if isinstance(e.get("task"), tuple) else (e.get("wo"), e.get("task"))

        groups[group_key].append({
            "task": task_val,
            "value": val,
            "censored": censored,
        })

    return dict(groups)


def p75_proposal(observations: list[dict]) -> dict[str, Any]:
    """작업당 첫 관측만 유지하고, 상한 도달(censored)을 반영한 p75 보정값을 산정한다."""
    seen_tasks = set()
    unique_obs = []
    for o in observations:
        if not isinstance(o, dict):
            continue
        task = o.get("task")
        if task not in seen_tasks:
            seen_tasks.add(task)
            unique_obs.append(o)
    n = len(unique_obs)
    if n < 5:
        return {"status": "표본 부족", "value": None, "n": n}

    sorted_obs = sorted(
        unique_obs,
        key=lambda x: (1 if x.get("censored") else 0, x.get("value") if x.get("value") is not None else float("inf")),
    )
    k = math.ceil(0.75 * n)
    target = sorted_obs[k - 1]
    if target.get("censored"):
        return {"status": "산정 불가", "value": None, "n": n}
    return {"status": "제안", "value": target.get("value"), "n": n}


def proposal_section(events: list[dict]) -> str:
    """기본값 보정 제안 절 내용 생성."""
    seen_runs = set()
    unique_usage = []
    for e in events:
        if isinstance(e, dict) and e.get("kind") == "model_usage":
            agent = e.get("agent")
            run_id = e.get("run_id")
            run_key = (agent, run_id) if run_id is not None else (agent, id(e))
            if run_key not in seen_runs:
                seen_runs.add(run_key)
                unique_usage.append(e)

    unknown_count = 0
    unassigned_count = 0
    for e in unique_usage:
        wo = e.get("wo")
        cf = wo_class_for(events, wo) if wo else None
        tier = e.get("tier") if e.get("tier") is not None else (cf.get("tier") if cf else None)
        risk = e.get("risk") if e.get("risk") is not None else (cf.get("risk") if cf else None)
        has_prospective = (cf is not None and cf.get("basis") == "prospective")

        if tier is None or risk is None:
            unknown_count += 1
        elif not has_prospective:
            unassigned_count += 1

    groups = time_observations(events, prospective_only=True)
    rows = []
    for group_key in sorted(groups.keys(), key=lambda k: tuple(str(x) for x in k)):
        obs_list = groups[group_key]
        prop = p75_proposal(obs_list)
        val_str = "—"
        if prop.get("value") is not None:
            val = prop["value"]
            val_str = f"{int(val)}" if isinstance(val, (int, float)) and val == int(val) else f"{val}"
        group_str = " · ".join(str(k) if k is not None else "—" for k in group_key)
        rows.append({
            "묶음": group_str,
            "표본 수(n)": str(prop["n"]),
            "상태": prop["status"],
            "제안 값(초)": val_str,
        })

    note = f"티어·위험도 미상: {unknown_count}건 제외\n작업 전 배정 없음: {unassigned_count}건 제외\n\n"
    return note + table(rows)


def timing_rows(events: list[dict]) -> list[dict]:
    wait_starts: dict[tuple[str, str], list[int]] = defaultdict(list)
    wait_start_paired_with: dict[int, int] = {}
    wait_end_paired_with: dict[int, int] = {}

    act_starts: dict[tuple[str, str], list[int]] = defaultdict(list)
    act_start_paired_with: dict[int, int] = {}
    act_end_paired_with: dict[int, int] = {}

    for idx, e in enumerate(events):
        if not isinstance(e, dict):
            continue
        k = e.get("kind")
        wo = str(e.get("wo") or "")
        stage = str(e.get("stage") or "")
        if k == "human_wait_start":
            wait_starts[(wo, stage)].append(idx)
        elif k == "human_wait_end":
            if wait_starts[(wo, stage)]:
                s_idx = wait_starts[(wo, stage)].pop(0)
                wait_start_paired_with[s_idx] = idx
                wait_end_paired_with[idx] = s_idx
        elif k == "human_start":
            act_starts[(wo, stage)].append(idx)
        elif k == "human_end":
            if act_starts[(wo, stage)]:
                s_idx = act_starts[(wo, stage)].pop(0)
                act_start_paired_with[s_idx] = idx
                act_end_paired_with[idx] = s_idx

    seen_model_runs: set[tuple[str, Any]] = set()
    rows = []

    for idx, e in enumerate(events):
        if not isinstance(e, dict):
            continue
        k = e.get("kind")
        wo = str(e.get("wo") or "")

        if k == "human_wait_start":
            stage = str(e.get("stage") or "unknown")
            start_raw = e.get("ts")
            start_dt = parse_ts_safe(start_raw)
            start_str = str(start_raw) if start_dt is not None else "unknown"

            if idx in wait_start_paired_with:
                end_e = events[wait_start_paired_with[idx]]
                end_raw = end_e.get("ts")
                end_dt = parse_ts_safe(end_raw)
                end_str = str(end_raw) if end_dt is not None else "unknown"

                if start_dt is not None and end_dt is not None:
                    delta = (end_dt - start_dt).total_seconds()
                    sec_str = f"{delta:.1f}" if delta >= 0 else "unknown"
                else:
                    sec_str = "unknown"
                rows.append({
                    "WO": wo,
                    "종류": "사람 대기",
                    "구간": stage,
                    "시작(UTC)": start_str,
                    "끝(UTC)": end_str,
                    "시간(초)": sec_str,
                    "출처": "기록",
                })
            else:
                rows.append({
                    "WO": wo,
                    "종류": "사람 대기",
                    "구간": stage,
                    "시작(UTC)": start_str,
                    "끝(UTC)": "진행 중",
                    "시간(초)": "진행 중",
                    "출처": "기록",
                })

        elif k == "human_wait_end":
            if idx in wait_end_paired_with:
                continue
            stage = str(e.get("stage") or "unknown")
            end_raw = e.get("ts")
            end_dt = parse_ts_safe(end_raw)
            end_str = str(end_raw) if end_dt is not None else "unknown"
            rows.append({
                "WO": wo,
                "종류": "사람 대기",
                "구간": stage,
                "시작(UTC)": "unknown",
                "끝(UTC)": end_str,
                "시간(초)": "unknown",
                "출처": "기록",
            })

        elif k == "human_start":
            stage = str(e.get("stage") or "unknown")
            start_raw = e.get("ts")
            start_dt = parse_ts_safe(start_raw)
            start_str = str(start_raw) if start_dt is not None else "unknown"

            if idx in act_start_paired_with:
                end_e = events[act_start_paired_with[idx]]
                end_raw = end_e.get("ts")
                end_dt = parse_ts_safe(end_raw)
                end_str = str(end_raw) if end_dt is not None else "unknown"

                if start_dt is not None and end_dt is not None:
                    delta = (end_dt - start_dt).total_seconds()
                    sec_str = f"{delta:.1f}" if delta >= 0 else "unknown"
                else:
                    sec_str = "unknown"
                rows.append({
                    "WO": wo,
                    "종류": "사람 활동",
                    "구간": stage,
                    "시작(UTC)": start_str,
                    "끝(UTC)": end_str,
                    "시간(초)": sec_str,
                    "출처": "기록",
                })
            else:
                rows.append({
                    "WO": wo,
                    "종류": "사람 활동",
                    "구간": stage,
                    "시작(UTC)": start_str,
                    "끝(UTC)": "진행 중",
                    "시간(초)": "진행 중",
                    "출처": "기록",
                })

        elif k == "human_end":
            if idx in act_end_paired_with:
                continue
            stage = str(e.get("stage") or "unknown")
            end_raw = e.get("ts")
            end_dt = parse_ts_safe(end_raw)
            end_str = str(end_raw) if end_dt is not None else "unknown"
            rows.append({
                "WO": wo,
                "종류": "사람 활동",
                "구간": stage,
                "시작(UTC)": "unknown",
                "끝(UTC)": end_str,
                "시간(초)": "unknown",
                "출처": "기록",
            })

        elif k == "model_usage":
            agent = e.get("agent")
            run_id = e.get("run_id")
            run_key = (agent, run_id) if run_id is not None else (agent, id(e))
            if run_key in seen_model_runs:
                continue
            seen_model_runs.add(run_key)

            role = e.get("role") or "unknown"
            agent_str = e.get("agent") or "unknown"
            time_scope = e.get("time_scope") or "unknown"
            if time_scope == "model_activity":
                scope_label = "model_activity" if is_proven_activity(e) else "unknown"
            else:
                scope_label = time_scope
            scope_str = f"{role}·{agent_str}·{scope_label}"

            started_raw = e.get("started")
            ended_raw = e.get("ended")
            started_dt = parse_ts_safe(started_raw)
            ended_dt = parse_ts_safe(ended_raw)
            ts_raw = e.get("ts")
            ts_dt = parse_ts_safe(ts_raw)
            sec_val = parse_seconds(e.get("seconds"))

            if started_raw is not None and ended_raw is not None:
                start_str = str(started_raw) if started_dt is not None else "unknown"
                end_str = str(ended_raw) if ended_dt is not None else "unknown"
                출처 = "기록"
                if sec_val is not None:
                    sec_str = f"{sec_val:.1f}"
                else:
                    sec_str = "unknown"
            else:
                출처 = "계산"
                ref_raw = ended_raw if ended_raw is not None else ts_raw
                ref_dt = ended_dt if ended_raw is not None else ts_dt
                end_str = str(ref_raw) if ref_dt is not None else "unknown"

                if sec_val is not None and ref_dt is not None:
                    sec_str = f"{sec_val:.1f}"
                    calc_start_dt = ref_dt - timedelta(seconds=sec_val)
                    start_str = calc_start_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                else:
                    sec_str = f"{sec_val:.1f}" if sec_val is not None else "unknown"
                    start_str = "unknown"

            rows.append({
                "WO": wo,
                "종류": "모델",
                "구간": scope_str,
                "시작(UTC)": start_str,
                "끝(UTC)": end_str,
                "시간(초)": sec_str,
                "출처": 출처,
            })

        elif k == "gate_run":
            purpose = e.get("purpose")
            if purpose:
                scope_str = str(purpose)
            else:
                scope_str = "self_check" if e.get("only") else "verdict"

            ts_raw = e.get("ts")
            ts_dt = parse_ts_safe(ts_raw)
            sec_val = parse_seconds(e.get("seconds"))

            end_str = str(ts_raw) if ts_dt is not None else "unknown"
            if sec_val is not None and ts_dt is not None:
                calc_start_dt = ts_dt - timedelta(seconds=sec_val)
                start_str = calc_start_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                sec_str = f"{sec_val:.1f}"
            else:
                start_str = "unknown"
                sec_str = f"{sec_val:.1f}" if sec_val is not None else "unknown"

            rows.append({
                "WO": wo,
                "종류": "게이트",
                "구간": scope_str,
                "시작(UTC)": start_str,
                "끝(UTC)": end_str,
                "시간(초)": sec_str,
                "출처": "계산",
            })

        elif k == "feedback_run":
            scope_str = "feedback"
            ts_raw = e.get("ts")
            ts_dt = parse_ts_safe(ts_raw)
            sec_val = parse_seconds(e.get("seconds"))

            end_str = str(ts_raw) if ts_dt is not None else "unknown"
            if sec_val is not None and ts_dt is not None:
                calc_start_dt = ts_dt - timedelta(seconds=sec_val)
                start_str = calc_start_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                sec_str = f"{sec_val:.1f}"
            else:
                start_str = "unknown"
                sec_str = f"{sec_val:.1f}" if sec_val is not None else "unknown"

            rows.append({
                "WO": wo,
                "종류": "피드백",
                "구간": scope_str,
                "시작(UTC)": start_str,
                "끝(UTC)": end_str,
                "시간(초)": sec_str,
                "출처": "계산",
            })

    return rows


def timing_summary(events: list[dict]) -> list[dict]:
    wos = sorted({str(e["wo"]) for e in events if isinstance(e, dict) and e.get("wo")})

    wait_starts: dict[tuple[str, str], list[dict]] = defaultdict(list)
    paired_waits: dict[str, list[float]] = defaultdict(list)
    open_wait_count: dict[str, int] = defaultdict(int)

    for e in events:
        if not isinstance(e, dict):
            continue
        k = e.get("kind")
        wo = str(e.get("wo") or "")
        stage = str(e.get("stage") or "")
        if k == "human_wait_start":
            wait_starts[(wo, stage)].append(e)
        elif k == "human_wait_end":
            if wait_starts[(wo, stage)]:
                s_ev = wait_starts[(wo, stage)].pop(0)
                s_dt = parse_ts_safe(s_ev.get("ts"))
                e_dt = parse_ts_safe(e.get("ts"))
                if s_dt is not None and e_dt is not None and e_dt >= s_dt:
                    paired_waits[wo].append((e_dt - s_dt).total_seconds())

    for (wo, stage), starts in wait_starts.items():
        open_wait_count[wo] += len(starts)

    seen_model_runs: set[tuple[str, Any]] = set()
    model_activity_by_wo: dict[str, list[dict]] = defaultdict(list)

    for e in events:
        if not isinstance(e, dict) or e.get("kind") != "model_usage":
            continue
        agent = e.get("agent")
        run_id = e.get("run_id")
        run_key = (agent, run_id) if run_id is not None else (agent, id(e))
        if run_key in seen_model_runs:
            continue
        seen_model_runs.add(run_key)

        wo = str(e.get("wo") or "")
        if e.get("time_scope") == "model_activity":
            model_activity_by_wo[wo].append(e)

    rows = []
    for wo in wos:
        waits = paired_waits.get(wo, [])
        if waits:
            wait_str = f"{sum(waits):.1f}"
        else:
            wait_str = "기록 없음"

        open_count = open_wait_count.get(wo, 0)

        m_runs = model_activity_by_wo.get(wo, [])
        if not m_runs:
            model_sum_str = "unknown"
        else:
            valid_secs = []
            for r in m_runs:
                if is_proven_activity(r):
                    s = parse_seconds(r.get("seconds"))
                    if s is not None:
                        valid_secs.append(s)
            a = len(valid_secs)
            b = len(m_runs)
            if a == 0:
                model_sum_str = "unknown"
            elif a == b:
                model_sum_str = f"{sum(valid_secs):.1f}"
            else:
                model_sum_str = f"{sum(valid_secs):.1f} 부분 (확인 {a}/{b})"

        intervals = []
        for r in m_runs:
            if is_proven_activity(r):
                s_dt = parse_ts_safe(r.get("started"))
                e_dt = parse_ts_safe(r.get("ended"))
                if s_dt is not None and e_dt is not None and e_dt > s_dt:
                    intervals.append([s_dt, e_dt])

        if not intervals:
            wall_str = "unknown"
        else:
            intervals.sort(key=lambda x: x[0])
            merged = []
            for start, end in intervals:
                if not merged or start > merged[-1][1]:
                    merged.append([start, end])
                else:
                    merged[-1][1] = max(merged[-1][1], end)
            total_sec = sum((end - start).total_seconds() for start, end in merged)
            wall_str = f"{total_sec:.1f}"

        rows.append({
            "WO": wo,
            "사람 대기(초)": wait_str,
            "진행 중 대기": str(open_count),
            "모델 활동 합(초)": model_sum_str,
            "모델 벽시계(초)": wall_str,
        })

    return rows


def timing_report(events: list[dict]) -> str:
    return (
        "# TDD 단계 시간 보고 (자동 생성: python harness/kpi.py --timing)\n\n"
        "## 단계별 시각\n\n" + table(timing_rows(events)) +
        "\n## WO별 요약\n\n" + table(timing_summary(events))
    )


def auto_approval_section(events: list[dict]) -> str:
    target_wos = sorted({
        str(e["wo"]) for e in events
        if isinstance(e, dict) and e.get("wo") and (
            (e.get("kind") in ("human_wait_start", "human_wait_end") and e.get("stage") == "H3")
            or e.get("kind") == "auto_approval"
        )
    })

    rows = []
    for wo in target_wos:
        cf = wo_class_for(events, wo)
        risk = cf.get("risk") if cf else None

        decision = "미확인"
        for e in events:
            if not isinstance(e, dict) or e.get("wo") != wo:
                continue
            if e.get("kind") == "human_wait_end" and e.get("stage") == "H3":
                decision = "사람"
            elif e.get("kind") == "auto_approval" and is_valid_auto(e, risk):
                decision = "자동"

        auto_records = [
            e for e in events
            if isinstance(e, dict) and e.get("wo") == wo and e.get("kind") == "auto_approval"
        ]
        has_auto = len(auto_records) > 0
        all_auto_invalid = has_auto and all(not is_valid_auto(e, risk) for e in auto_records)

        if decision == "자동":
            reason = "—"
        elif decision == "사람":
            if risk == "high" and has_auto:
                reason = "high 범위 밖"
            elif all_auto_invalid:
                reason = "승인 근거 불충분"
            else:
                reason = "사람 H3 처리·사유 미기록"
        else:
            reason = "H3 결정 미확인"

        rows.append({
            "WO": wo,
            "H3 결정": decision,
            "미적용 사유": reason,
        })

    n_auto = sum(1 for r in rows if r["H3 결정"] == "자동")
    n_human = sum(1 for r in rows if r["H3 결정"] == "사람")
    n_unknown = sum(1 for r in rows if r["H3 결정"] == "미확인")
    denom = n_auto + n_human
    if denom == 0:
        rate_str = "적용률: N/A"
    else:
        pct = (n_auto / denom) * 100
        rate_str = f"적용률: {n_auto}/{denom} ({pct:.1f}%)"

    lines = [
        rate_str,
        f"유효 자동 승인: {n_auto}",
        f"사람 승인: {n_human}",
        f"결정 미확인: {n_unknown}",
        "",
        table(rows),
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timing", action="store_true")
    args = ap.parse_args()

    # 손상된 events.jsonl 을 빈 목록으로 삼키면 근거 없는 빈 보고서가 기존 kpi.md 를 덮는다.
    # 파일이 없을 때는 read_events 가 이미 [] 를 준다 (WO-H61 리드 보정, T17)
    events = read_events()

    if args.timing:
        body = timing_report(events)
        OUT_TIMING.parent.mkdir(exist_ok=True)
        OUT_TIMING.write_text(body, encoding="utf-8", newline="\n")
        print(body, end="")
        return 0

    body = (
        "# KPI 집계 (자동 생성: python harness/kpi.py)\n\n"
        "핵심 지표는 하네스가 자동으로 남긴 이벤트에서 계산한다. "
        "'사람 대기(자동)'은 lead 가 mark.py wait-start/wait-end 로 남긴 기록에서, "
        "'사람 시간(보조)'은 사람이 찍은 start/end 에서 계산한다.\n\n"
        "## 작업 지시서별\n\n" + table(per_wo(events)) +
        "\n## 사용량 (역할 × 에이전트)\n\n" + table(usage_by_role_agent(events)) +
        "\n## 검토 비용 (위험도별)\n\n" + table(review_cost_by_risk(events)) +
        "\n## 기본값 보정 제안 (적용은 사용자 확인)\n\n" + proposal_section(events) +
        "\n## 자동 승인 적용 현황\n\n" + auto_approval_section(events) +
        "\n## 테스트 승인 대기 (H3, 자동)\n\n" + table(approvals(events)) +
        "\n## 문제 기록 (spec_issues.md)\n\n" + table(issues())
    )
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(body, encoding="utf-8", newline="\n")
    print(body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
