"""KPI 집계: reports/events.jsonl + reports/spec_issues.md → reports/kpi.md

원칙
- 핵심 지표는 스크립트가 자동으로 남긴 이벤트에서만 계산한다 (자기 보고 · 사람 기억 불신, P5).
- 사람이 남긴 mark.py 기록은 '보조'로 따로 표시한다. 없어도 표는 채워진다.
- 명세 변경 건수 하나로 판단하지 않는다. 여러 지표를 함께 본다.

사용: python harness/kpi.py
"""

from __future__ import annotations

import math
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from events import ROOT, read_events

ISSUES = ROOT / "reports" / "spec_issues.md"
OUT = ROOT / "reports" / "kpi.md"


def ts(e: dict) -> datetime:
    return datetime.strptime(e["ts"], "%Y-%m-%dT%H:%M:%SZ")


def minutes(a: datetime, b: datetime) -> float:
    return round((b - a).total_seconds() / 60, 1)


def full_run(e: dict) -> bool:
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
        impl = [e for e in evs if e.get("kind") == "gate_run" and e.get("role") == "implementer"]
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
        gate_runs = [e for e in evs if e.get("kind") == "gate_run"]
        impl_cause = sum(1 for e in gate_runs if e.get("role") == "implementer" and (e.get("cause") == "implementer" or not e.get("cause")))
        test_cause = sum(1 for e in gate_runs if e.get("cause") == "test" or (not e.get("cause") and e.get("role") == "test_author"))
        harness_cause = sum(1 for e in gate_runs if e.get("cause") == "harness")
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
            "max_rounds 산정 회차": str(impl_cause),
            "검토 회차": str(review_round),
            "계약 안 결함": str(contract_defects),
            "예산 연장": str(budget_extends),
            "예산 중단": str(budget_stops),
            "실패 사례 (첫→마지막)": failed_cases_str,
            "사람 대기(분, 자동)": wait_text,
            "막힌 지점 수": blocked_count,
            "결정 수": decisions_sum,
            "사람 시간(분, 보조)": "기록 없음" if human is None else f"{human:.1f}",
        })
    return rows


def approvals(events: list[dict]) -> list[dict]:
    """테스트 승인 대기: 테스트 작성자의 마지막 게이트 실행 → 잠금. 사람의 기록 없이 계산된다."""
    rows, last_author = [], None
    for e in events:
        if e["kind"] == "gate_run" and e["role"] == "test_author":
            last_author = e
        elif e["kind"] == "test_lock":
            rows.append({
                "잠금 시각(UTC)": e["ts"],
                "잠금 커밋": e["head"],
                "테스트 파일 수": e.get("files", "?"),
                "작성 완료 → 잠금(분)": minutes(ts(last_author), ts(e)) if last_author else "작성자 실행 기록 없음",
            })
            last_author = None
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

        sec_vals = [e.get("seconds") for e in runs if e.get("seconds") is not None and isinstance(e.get("seconds"), (int, float))]
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
            and e.get("seconds") is not None
            and e.get("time_scope") == "model_activity"
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
        val = float(sec)

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


def main() -> int:
    events = read_events()
    body = (
        "# KPI 집계 (자동 생성: python harness/kpi.py)\n\n"
        "핵심 지표는 하네스가 자동으로 남긴 이벤트에서 계산한다. "
        "'사람 대기(자동)'은 lead 가 mark.py wait-start/wait-end 로 남긴 기록에서, "
        "'사람 시간(보조)'은 사람이 찍은 start/end 에서 계산한다.\n\n"
        "## 작업 지시서별\n\n" + table(per_wo(events)) +
        "\n## 사용량 (역할 × 에이전트)\n\n" + table(usage_by_role_agent(events)) +
        "\n## 기본값 보정 제안 (적용은 사용자 확인)\n\n" + proposal_section(events) +
        "\n## 테스트 승인 대기 (H3, 자동)\n\n" + table(approvals(events)) +
        "\n## 문제 기록 (spec_issues.md)\n\n" + table(issues())
    )
    OUT.write_text(body, encoding="utf-8", newline="\n")
    print(body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
