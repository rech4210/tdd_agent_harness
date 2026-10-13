"""사람 이벤트 보조 기록. KPI 의 핵심 지표는 이 기록 없이도 자동 이벤트로 계산된다.

사람이 시각을 직접 적지 않는다. 명령을 친 순간의 시각을 스크립트가 찍는다.
기억에 의존한 사후 기록을 막기 위해, 지난 시각은 받지 않는다.

사용:
  python harness/mark.py start H3 --wo WO-02           # 사람 단계 시작
  python harness/mark.py end H3 --wo WO-02             # 사람 단계 끝
  python harness/mark.py wait-start DEC --wo WO-AK01   # 자동 대기 시작 (lead)
  python harness/mark.py wait-end DEC --wo WO-AK01 --decisions 4 # 자동 대기 끝 (lead)
  python harness/mark.py note "..." --wo WO-02         # 메모
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
    ap.add_argument("action", choices=["start", "end", "note", "wait-start", "wait-end"])
    ap.add_argument("value", help="단계(H1~H11, QA, DEC, MERGE) 또는 메모")
    ap.add_argument("--wo", default="")
    ap.add_argument("--decisions", type=int, default=0, help="결정 수 (0 이상)")
    args = ap.parse_args()

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
