"""사람 이벤트 보조 기록. KPI 의 핵심 지표는 이 기록 없이도 자동 이벤트로 계산된다.

사람이 시각을 직접 적지 않는다. 명령을 친 순간의 시각을 스크립트가 찍는다.
기억에 의존한 사후 기록을 막기 위해, 지난 시각은 받지 않는다.

사용:
  python harness/mark.py start H3 --wo WO-02           # 사람 단계 시작
  python harness/mark.py end H3 --wo WO-02             # 사람 단계 끝
  python harness/mark.py wait-start DEC --wo WO-AK01   # 자동 대기 시작 (lead)
  python harness/mark.py wait-end DEC --wo WO-AK01 --decisions 4 # 자동 대기 끝 (lead)
  python harness/mark.py note "..." --wo WO-02         # 메모
  python harness/mark.py review codex --round 1 --defects 2 --wo WO-02  # 검토 기록
  python harness/mark.py budget-extend "이유" --wo WO-02   # 예산 연장
  python harness/mark.py budget-stop "이유" --wo WO-02     # 예산 중단
  python harness/mark.py classify "근거" --wo WO-02 --tier T2 --risk low --basis prospective --by user  # 작업 지시서 분류
"""

from __future__ import annotations

import argparse
import re
import sys

from events import log_event, read_events

STAGES = {
    "H1", "H2", "H3", "H4", "H5", "H6", "H7", "H8", "H9", "H10", "H11", "QA",
    "DEC", "MERGE",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["start", "end", "note", "wait-start", "wait-end", "review", "budget-extend", "budget-stop", "classify"])
    ap.add_argument("value", help="단계(H1~H11, QA, DEC, MERGE), 검토자, 메모 또는 이유")
    ap.add_argument("--wo", default="")
    ap.add_argument("--decisions", type=int, default=0, help="결정 수 (0 이상)")
    ap.add_argument("--round", type=int, default=None, help="검토 회차 (0 이상)")
    ap.add_argument("--defects", type=int, default=0, help="계약 안 결함 수 (0 이상)")
    ap.add_argument("--tier", default=None, help="티어 (T1|T2|T3)")
    ap.add_argument("--risk", default=None, help="위험도 (low|medium|high)")
    ap.add_argument("--basis", default=None, help="근거 (prospective|retrospective)")
    ap.add_argument("--by", default=None, help="확인자")
    args = ap.parse_args()

    if args.action != "classify" and any(x is not None for x in (args.tier, args.risk, args.basis, args.by)):
        print("--tier, --risk, --basis, --by는 classify에서만 쓸 수 있다")
        return 2

    if args.action in ("start", "end", "wait-start", "wait-end") and args.value not in STAGES:
        print(f"알 수 없는 단계: {args.value}. 가능한 값: {sorted(STAGES)}")
        return 2
    if args.wo and not re.fullmatch(r"([A-Za-z][A-Za-z0-9]*-[A-Za-z]*\d+[a-z]?|(?:S\d+|\d{2,})-[a-z0-9-]+)", args.wo):
        print(f"작업 ID 또는 슬라이스 ID 형식이 아니다: {args.wo} (예: WO-02, WO-01b, JS-01, WO-AK01, WO-H11, S01-app-state)")
        return 2
    if args.decisions < 0:
        print(f"--decisions는 0 이상의 정수여야 한다: {args.decisions}")
        return 2
    if args.action != "wait-end" and args.decisions != 0:
        print("--decisions는 wait-end에서만 쓸 수 있다")
        return 2

    if args.action == "classify":
        if not args.value or not args.value.strip():
            print("근거가 필요하다")
            return 2
        if not args.wo or not args.wo.strip():
            print("--wo가 필요하다")
            return 2
        if args.tier not in ("T1", "T2", "T3"):
            print(f"--tier는 T1, T2, T3 중 하나여야 한다: {args.tier}")
            return 2
        if args.risk not in ("low", "medium", "high"):
            print(f"--risk는 low, medium, high 중 하나여야 한다: {args.risk}")
            return 2
        if args.basis not in ("prospective", "retrospective"):
            print(f"--basis는 prospective, retrospective 중 하나여야 한다: {args.basis}")
            return 2
        if not args.by or not args.by.strip():
            print("--by(확인자)가 필요하다")
            return 2
        e = log_event(
            "wo_class",
            wo=args.wo,
            tier=args.tier,
            risk=args.risk,
            reason=args.value.strip(),
            basis=args.basis,
            confirmed_by=args.by.strip(),
        )
        print(f"기록: {e['ts']} {e['kind']} {args.wo} tier={args.tier} risk={args.risk} basis={args.basis} by={args.by}")
        return 0

    if args.action == "review":
        if not args.value or not args.value.strip():
            print("검토자 이름이 필요하다")
            return 2
        if args.round is None or args.round < 0:
            print(f"--round는 0 이상의 정수여야 한다: {args.round}")
            return 2
        if args.defects is None or args.defects < 0:
            print(f"--defects는 0 이상의 정수여야 한다: {args.defects}")
            return 2
        e = log_event(
            "review_run",
            reviewer=args.value.strip(),
            round=args.round,
            contract_defects=args.defects,
            wo=args.wo,
        )
        print(f"기록: {e['ts']} {e['kind']} {args.value} round={args.round} defects={args.defects} {args.wo}")
        return 0

    if args.action in ("budget-extend", "budget-stop"):
        if not args.value or not args.value.strip():
            print("이유가 필요하다")
            return 2
        kind = "budget_extend" if args.action == "budget-extend" else "budget_stop"
        e = log_event(kind, reason=args.value.strip(), wo=args.wo)
        print(f"기록: {e['ts']} {e['kind']} {args.value} {args.wo}")
        return 0

    if args.action in ("wait-start", "wait-end"):
        open_count = 0
        for e in read_events():
            if e.get("stage") == args.value and e.get("wo", "") == args.wo:
                if e.get("kind") == "human_wait_start":
                    open_count += 1
                elif e.get("kind") == "human_wait_end":
                    open_count = max(0, open_count - 1)
        if args.action == "wait-end" and open_count <= 0:
            print(f"열린 wait-start가 없다: 구간={args.value}, wo={args.wo}")
            return 2
        if args.action == "wait-start" and open_count > 0:
            print(f"이미 열린 wait-start가 있다: 구간={args.value}, wo={args.wo}")
            return 2

    fields: dict[str, object] = {
        "stage": args.value if args.action != "note" else None,
        "note": args.value if args.action == "note" else None,
        "wo": args.wo,
    }
    if args.action == "wait-end":
        fields["decisions"] = args.decisions

    e = log_event(f"human_{args.action.replace('-', '_')}", **fields)
    print(f"기록: {e['ts']} {e['kind']} {args.value} {args.wo}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
