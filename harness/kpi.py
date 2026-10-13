"""KPI 집계: reports/events.jsonl + reports/spec_issues.md → reports/kpi.md

원칙
- 핵심 지표는 스크립트가 자동으로 남긴 이벤트에서만 계산한다 (자기 보고 · 사람 기억 불신, P5).
- 사람이 남긴 mark.py 기록은 '보조'로 따로 표시한다. 없어도 표는 채워진다.
- 명세 변경 건수 하나로 판단하지 않는다. 여러 지표를 함께 본다.

사용: python harness/kpi.py
"""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

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


def per_wo(events: list[dict]) -> list[dict]:
    by_wo: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        if e.get("wo"):
            by_wo[e["wo"]].append(e)
    rows = []
    for wo, evs in sorted(by_wo.items()):
        impl = [e for e in evs if e["kind"] == "gate_run" and e["role"] == "implementer"]
        fb_runs = [e for e in evs if e.get("kind") == "feedback_run"]
        fb_count = delegation_feedback_count(wo, len(fb_runs))
        green_idx = next((i for i, e in enumerate(impl) if e["passed"] and full_run(e)), None)
        violations = [e["g0_violations"] or 0 for e in impl if e.get("g0_violations") is not None]
        review = sorted({p for e in impl for p in e.get("review_required", [])})
        # 시작점은 지시서 발행(기준 태그) 시각. 태그가 없으면 첫 이벤트로 대신하고 표에 표시한다
        issued = issued_at(wo)
        first = issued or ts(evs[0])
        green = ts(impl[green_idx]) if green_idx is not None else None
        wait_text, blocked_count, decisions_sum = wait_stats(evs)
        human = human_minutes(evs)
        slice_id = next((e.get("slice") for e in impl if e.get("slice")), "")
        a = assignments().get(slice_id, {})
        gate_runs = [e for e in evs if e.get("kind") == "gate_run"]
        impl_cause = sum(1 for e in gate_runs if e.get("role") == "implementer" and (e.get("cause") == "implementer" or not e.get("cause")))
        test_cause = sum(1 for e in gate_runs if e.get("cause") == "test" or (not e.get("cause") and e.get("role") == "test_author"))
        harness_cause = sum(1 for e in gate_runs if e.get("cause") == "harness")
        rows.append({
            "WO": wo,
            # 배정이 다른 슬라이스끼리 비교할 때 섞이지 않도록 모델을 함께 적는다
            "배정 (테스트 / 구현)": f"{a.get('test_author', '?')} / {a.get('implementer', '?')}" if a else "—",
            "구현 실행 수": len(impl),
            "판정 전 피드백 실행 수": fb_count,
            "전체 통과까지 실행 수": (green_idx + 1) if green_idx is not None else "미통과",
            "첫 실행 통과": "예" if impl and impl[0]["passed"] and full_run(impl[0]) else "아니오",
            "실패가 가장 많은 게이트": most_common([e["first_failed"] for e in impl if e["first_failed"]]),
            "G0 위반(건)": sum(violations),
            "리드타임(분)": (f"{minutes(first, green)}" + ("" if issued else " (태그 없음, 첫 이벤트 기준)")) if green else "—",
            "기계 실행 시간(분)": round(sum(e["seconds"] for e in impl) / 60, 1),
            "사람 검토 필요 경로": ", ".join(review) or "없음",
            "구현자 원인 회차": str(impl_cause),
            "테스트 · 재잠금 원인 회차": str(test_cause),
            "하네스 원인 회차": str(harness_cause),
            "max_rounds 산정 회차": str(impl_cause),
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


def main() -> int:
    events = read_events()
    body = (
        "# KPI 집계 (자동 생성: python harness/kpi.py)\n\n"
        "핵심 지표는 하네스가 자동으로 남긴 이벤트에서 계산한다. "
        "'사람 대기(자동)'은 lead 가 mark.py wait-start/wait-end 로 남긴 기록에서, "
        "'사람 시간(보조)'은 사람이 찍은 start/end 에서 계산한다.\n\n"
        "## 작업 지시서별\n\n" + table(per_wo(events)) +
        "\n## 테스트 승인 대기 (H3, 자동)\n\n" + table(approvals(events)) +
        "\n## 문제 기록 (spec_issues.md)\n\n" + table(issues())
    )
    OUT.write_text(body, encoding="utf-8", newline="\n")
    print(body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
