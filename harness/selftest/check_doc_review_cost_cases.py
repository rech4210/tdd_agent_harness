"""WO-H57 F1: doc_auditor 비용 포함, 편집 비용 제외, 기존 집계 보존.

계약: doc_auditor를 기존 검토 역할에 추가. 작업 전 위험도 우선·미상·
run 중복 제거를 보존하고 review_run이 없는 비용에서 회차/결함을 만들지 않는다.
이벤트는 읽기만 한다. 실제 kpi CLI를 임시 저장소에서 실행한다.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from check_kpi_cases import make_project, write_events
from check_review_cost_cases import cls, rev, section, use


def main() -> int:
    results = []

    def check(name, actual, expected):
        good = actual == expected
        results.append(good)
        print(f"{'OK' if good else 'BAD'} {name}" + (f" actual={actual!r} expected={expected!r}" if not good else ""))

    def costs(row):
        return [row.get(k) for k in ("검토 모델 시간(초)", "검토 입력 토큰", "검토 출력 토큰")]

    with tempfile.TemporaryDirectory(prefix="h57-") as tmp:
        p = make_project(Path(tmp), "fixture")
        duplicate = use("WO-81", "audit-low", "doc_auditor", 61.5, 1200, 33)
        write_events(p, [
            cls("WO-81", "low"),
            {**cls("WO-81", "high"), "basis": "retrospective", "ts": "2026-10-20T01:00:00Z"},
            cls("WO-82", "high"),
            rev("WO-81", 1, 2, 2),
            duplicate, dict(duplicate),
            use("WO-81", "old-review", "reviewer", 10, 100, 2),
            use("WO-81", "editor", "doc_editor", 999, 9999, 999),
            use("WO-81", "impl", "implementer", 999, 9999, 999),
            use("WO-82", "audit-high", "doc_auditor", 7, 70, 7),
            use("WO-83", "audit-unknown", "doc_auditor", 5, 50, 5),
            use("WO-84", "old-adversary", "adversary", 3, 30, 3),
            use("WO-84", "old-mutation", "mutation_auditor", 4, 40, 4),
        ])
        before = (p / "reports/events.jsonl").read_bytes()
        rows = section(p)
        low, high, unknown = (rows.get(k, {}) for k in ("low", "high", "미상"))
        check("D01 auditor + reviewer, duplicate once, editor/impl excluded", costs(low), ["71.5", "1300", "35"])
        check("D02 prospective low overrides newer retrospective high", low.get("WO 수"), "1")
        check("D03 auditor-only high row and costs", costs(high), ["7.0", "70", "7"])
        check("D04 auditor-only creates no review rounds or defects", [high.get(k) for k in
              ("WO 수", "검토 회차", "계약 안 결함", "새 결함", "새 결함 모름")], ["1", "0", "0", "0", "0"])
        check("D05 unknown auditor and existing adversary/mutation", costs(unknown), ["12.0", "120", "12"])
        check("D06 unknown WO union", unknown.get("WO 수"), "2")
        check("D07 existing review defects unchanged", [low.get(k) for k in
              ("검토 회차", "계약 안 결함", "새 결함", "새 결함 모름")], ["1", "2", "2", "0"])
        check("D08 risk row order", list(rows), ["low", "high", "미상"])
        check("D09 events unchanged", (p / "reports/events.jsonl").read_bytes(), before)
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
