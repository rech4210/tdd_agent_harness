"""WO-H77 B: '테스트 승인 대기 (H3, 자동)' 표가 실제 H3 근거(사람 대기 · 유효 자동 승인)를 쓰는지 검사한다.

기존 approvals() 는 마지막 test_author gate_run → test_lock 간격만 계산하고 H3 이벤트를 보지 않았다.
승인 근거 판정은 step.py 와 같은 규칙(사람 H3 끝 또는 유효 auto_approval, high 위험도 auto 무효)을 쓴다.
짝짓기: 각 승인 근거는 그 뒤 첫 test_lock 하나와만 짝짓는다. 근거가 없는 잠금은 '기록 없음'(0 아님).
'자동 승인 적용 현황' 절의 마지막 결정 규칙(check_h61 A07)은 바꾸지 않는다.
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
TITLE = "테스트 승인 대기 (H3, 자동)"


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


def put(proj: Path, evs: list[dict]) -> None:
    lines = [json.dumps({"head": "abc1234", "branch": "main", **e}, ensure_ascii=False) for e in evs]
    (proj / "reports" / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def approval_rows(proj: Path) -> tuple[int, list[dict[str, str]]]:
    r = subprocess.run([PY, "harness/kpi.py"], cwd=proj, capture_output=True, text=True)
    f = proj / "reports" / "kpi.md"
    if r.returncode != 0 or not f.exists():
        return r.returncode, []
    text = f.read_text(encoding="utf-8")
    if f"## {TITLE}" not in text:
        return r.returncode, []
    sec = text.split(f"## {TITLE}", 1)[1].split("\n## ", 1)[0]
    lines = [l for l in sec.splitlines() if l.startswith("|")]
    if len(lines) < 2:
        return r.returncode, []
    head = [c.strip() for c in lines[0].strip().strip("|").split("|")]
    return r.returncode, [dict(zip(head, [c.strip() for c in l.strip().strip("|").split("|")])) for l in lines[2:]]


def author(t: str, wo: str) -> dict:
    return {"ts": t, "kind": "gate_run", "role": "test_author", "wo": wo, "slice": wo, "passed": True,
            "only": [], "purpose": "self_check", "g0_violations": 0, "review_required": []}


def ws(t: str, wo: str) -> dict:
    return {"ts": t, "kind": "human_wait_start", "wo": wo, "stage": "H3"}


def we(t: str, wo: str) -> dict:
    return {"ts": t, "kind": "human_wait_end", "wo": wo, "stage": "H3", "decisions": 1}


def auto(t: str, wo: str) -> dict:
    return {"ts": t, "kind": "auto_approval", "wo": wo, "policy": "D-28", "gt": "pass", "lazy_caught": 4,
            "lazy_total": 4, "residual_defects": 0, "base_types": ["unbounded", "reject-preserve"], "by": "lead"}


def cls(wo: str, risk: str) -> dict:
    return {"ts": "2026-10-26T08:00:00Z", "kind": "wo_class", "wo": wo, "tier": "T2", "risk": risk, "reason": "r",
            "basis": "prospective", "confirmed_by": "user"}


def lock(t: str, files: int = 3) -> dict:
    return {"ts": t, "kind": "test_lock", "files": files}


def at(rows: list[dict[str, str]], lock_ts: str) -> dict[str, str]:
    return next((r for r in rows if r.get("잠금 시각(UTC)") == lock_ts), {})


def main() -> int:
    results: list[tuple[str, bool, str]] = []

    def case(name: str, good: bool, detail: str = "") -> None:
        results.append((name, good, detail))

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)

        p = make_project(base, "human")
        put(p, [cls("WO-A", "low"), author("2026-10-26T10:00:00Z", "WO-A"), ws("2026-10-26T10:05:00Z", "WO-A"),
                we("2026-10-26T10:35:00Z", "WO-A"), lock("2026-10-26T10:40:00Z")])
        rc, rs = approval_rows(p)
        r = at(rs, "2026-10-26T10:40:00Z")
        case("K77-01 사람 H3: WO · 승인 근거 '사람' · H3 사람 대기 30.0 · 작성 완료 → 잠금 40.0",
             rc == 0 and r.get("WO") == "WO-A" and r.get("승인 근거") == "사람"
             and r.get("H3 사람 대기(분)") == "30.0" and r.get("작성 완료 → 잠금(분)") == "40.0", f"rc={rc} {rs}")

        p = make_project(base, "auto")
        put(p, [cls("WO-B", "low"), author("2026-10-26T11:00:00Z", "WO-B"), auto("2026-10-26T11:10:00Z", "WO-B"),
                lock("2026-10-26T11:12:00Z")])
        rc, rs = approval_rows(p)
        r = at(rs, "2026-10-26T11:12:00Z")
        case("K77-02 유효 자동 승인: 승인 근거 '자동' · H3 사람 대기 '기록 없음'(0 아님)",
             rc == 0 and r.get("WO") == "WO-B" and r.get("승인 근거") == "자동"
             and r.get("H3 사람 대기(분)") == "기록 없음", f"rc={rc} {rs}")

        p = make_project(base, "none")
        put(p, [author("2026-10-26T12:00:00Z", "WO-C"), lock("2026-10-26T12:05:00Z")])
        rc, rs = approval_rows(p)
        r = at(rs, "2026-10-26T12:05:00Z")
        case("K77-03 승인 기록 없는 잠금: WO · 승인 근거 · H3 사람 대기 모두 '기록 없음'",
             rc == 0 and r.get("WO") == "기록 없음" and r.get("승인 근거") == "기록 없음"
             and r.get("H3 사람 대기(분)") == "기록 없음", f"rc={rc} {rs}")

        p = make_project(base, "cross")
        put(p, [cls("WO-D", "low"), author("2026-10-26T13:00:00Z", "WO-D"), author("2026-10-26T13:20:00Z", "WO-E"),
                ws("2026-10-26T13:25:00Z", "WO-D"), we("2026-10-26T13:30:00Z", "WO-D"), lock("2026-10-26T13:31:00Z")])
        rc, rs = approval_rows(p)
        r = at(rs, "2026-10-26T13:31:00Z")
        case("K77-04 다른 WO 작성 실행이 끼어도 승인 WO(WO-D)의 작성 실행과 짝: 작성 완료 → 잠금 31.0 (11.0 아님)",
             rc == 0 and r.get("WO") == "WO-D" and r.get("작성 완료 → 잠금(분)") == "31.0", f"rc={rc} {rs}")

        p = make_project(base, "high")
        put(p, [cls("WO-F", "high"), author("2026-10-26T14:00:00Z", "WO-F"), auto("2026-10-26T14:05:00Z", "WO-F"),
                lock("2026-10-26T14:06:00Z")])
        rc, rs = approval_rows(p)
        r = at(rs, "2026-10-26T14:06:00Z")
        case("K77-05 high 위험도 auto 만 있음 → '자동' 아님 · 승인 근거 '기록 없음'",
             rc == 0 and r.get("승인 근거") == "기록 없음", f"rc={rc} {rs}")

        p = make_project(base, "relock")
        put(p, [cls("WO-G", "low"), author("2026-10-26T15:00:00Z", "WO-G"), ws("2026-10-26T15:01:00Z", "WO-G"),
                we("2026-10-26T15:11:00Z", "WO-G"), lock("2026-10-26T15:12:00Z"),
                author("2026-10-26T15:30:00Z", "WO-G"), lock("2026-10-26T15:40:00Z")])
        rc, rs = approval_rows(p)
        r1, r2 = at(rs, "2026-10-26T15:12:00Z"), at(rs, "2026-10-26T15:40:00Z")
        case("K77-06 한 승인 근거는 첫 잠금과만 짝: 재잠금은 승인 근거 '기록 없음'",
             rc == 0 and r1.get("승인 근거") == "사람" and r2.get("승인 근거") == "기록 없음", f"rc={rc} {rs}")

        p = make_project(base, "both-order")
        put(p, [cls("WO-H", "low"), author("2026-10-26T16:00:00Z", "WO-H"), auto("2026-10-26T16:01:00Z", "WO-H"),
                ws("2026-10-26T16:02:00Z", "WO-H"), we("2026-10-26T16:12:00Z", "WO-H"), lock("2026-10-26T16:13:00Z")])
        rc, rs = approval_rows(p)
        r = at(rs, "2026-10-26T16:13:00Z")
        case("K77-07a 자동 → 사람 H3: 마지막 근거 '사람' · 사람 대기 10.0 · 잠금은 한 행 (검토 필수-1)",
             rc == 0 and len(rs) == 1 and r.get("승인 근거") == "사람" and r.get("H3 사람 대기(분)") == "10.0"
             and r.get("WO") == "WO-H", f"rc={rc} {rs}")

        p = make_project(base, "both-order-rev")
        put(p, [cls("WO-H", "low"), author("2026-10-26T16:00:00Z", "WO-H"), ws("2026-10-26T16:02:00Z", "WO-H"),
                we("2026-10-26T16:12:00Z", "WO-H"), auto("2026-10-26T16:12:30Z", "WO-H"), lock("2026-10-26T16:13:00Z")])
        rc, rs = approval_rows(p)
        r = at(rs, "2026-10-26T16:13:00Z")
        case("K77-07b 사람 H3 → 자동: 마지막 근거 '자동' · 사람 대기 10.0 은 그대로 표시 (검토 필수-1)",
             rc == 0 and len(rs) == 1 and r.get("승인 근거") == "자동" and r.get("H3 사람 대기(분)") == "10.0",
             f"rc={rc} {rs}")

        p = make_project(base, "relock-new")
        put(p, [cls("WO-I", "low"), author("2026-10-26T17:00:00Z", "WO-I"), ws("2026-10-26T17:01:00Z", "WO-I"),
                we("2026-10-26T17:11:00Z", "WO-I"), lock("2026-10-26T17:12:00Z"),
                author("2026-10-26T17:20:00Z", "WO-I"), auto("2026-10-26T17:25:00Z", "WO-I"), lock("2026-10-26T17:30:00Z")])
        rc, rs = approval_rows(p)
        r1, r2 = at(rs, "2026-10-26T17:12:00Z"), at(rs, "2026-10-26T17:30:00Z")
        case("K77-08 잠금 사이 새 근거는 두 번째 잠금과만: 1차 '사람' · 2차 '자동' · 2차 작성 완료 → 잠금 10.0 (검토 필수-2)",
             rc == 0 and r1.get("승인 근거") == "사람" and r2.get("승인 근거") == "자동"
             and r2.get("작성 완료 → 잠금(분)") == "10.0" and r2.get("H3 사람 대기(분)") == "기록 없음", f"rc={rc} {rs}")

        p = make_project(base, "multi-wo")
        put(p, [cls("WO-J", "low"), cls("WO-K", "low"), author("2026-10-26T18:00:00Z", "WO-J"),
                author("2026-10-26T18:10:00Z", "WO-K"), ws("2026-10-26T18:11:00Z", "WO-J"), we("2026-10-26T18:15:00Z", "WO-J"),
                auto("2026-10-26T18:20:00Z", "WO-K"), lock("2026-10-26T18:30:00Z")])
        rc, rs = approval_rows(p)
        r = at(rs, "2026-10-26T18:30:00Z")
        case("K77-09 한 잠금 구간에 여러 WO 근거 → 마지막 근거의 WO(WO-K) · '자동' · WO-K 작성 실행 기준 20.0 (검토 필수-3)",
             rc == 0 and r.get("WO") == "WO-K" and r.get("승인 근거") == "자동"
             and r.get("작성 완료 → 잠금(분)") == "20.0", f"rc={rc} {rs}")

        p = make_project(base, "wait-pair")
        put(p, [cls("WO-L", "low"), author("2026-10-26T19:00:00Z", "WO-L"), ws("2026-10-26T19:00:00Z", "WO-L"),
                ws("2026-10-26T19:05:00Z", "WO-M"), we("2026-10-26T19:20:00Z", "WO-L"), lock("2026-10-26T19:21:00Z")])
        rc, rs = approval_rows(p)
        r = at(rs, "2026-10-26T19:21:00Z")
        case("K77-10 H3 사람 대기는 같은 WO 의 시작 · 끝만 짝: 20.0 (다른 WO 시작 무시, 검토 선택-1)",
             rc == 0 and r.get("WO") == "WO-L" and r.get("H3 사람 대기(분)") == "20.0", f"rc={rc} {rs}")

        p = make_project(base, "invalid-auto-human")
        put(p, [cls("WO-N", "low"), author("2026-10-26T20:00:00Z", "WO-N"), ws("2026-10-26T20:01:00Z", "WO-N"),
                we("2026-10-26T20:06:00Z", "WO-N"), auto("2026-10-26T20:07:00Z", "WO-N", ), lock("2026-10-26T20:08:00Z")])
        evs = [json.loads(l) for l in (p / "reports" / "events.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
        evs[4]["gt"] = "fail"
        put(p, evs)
        rc, rs = approval_rows(p)
        r = at(rs, "2026-10-26T20:08:00Z")
        case("K77-11 사람 H3 뒤 무효 auto(gt fail) 는 근거가 아님 → '사람'",
             rc == 0 and r.get("승인 근거") == "사람", f"rc={rc} {rs}")

        p = make_project(base, "relock-no-author")
        put(p, [cls("WO-P", "low"), author("2026-10-26T21:00:00Z", "WO-P"), ws("2026-10-26T21:01:00Z", "WO-P"),
                we("2026-10-26T21:02:00Z", "WO-P"), lock("2026-10-26T21:03:00Z"), lock("2026-10-26T21:30:00Z")])
        rc, rs = approval_rows(p)
        r2 = at(rs, "2026-10-26T21:30:00Z")
        case("K77-12 (리드 보정) 새 작성 실행 없는 재잠금 → '작성자 실행 기록 없음' (한 작성 실행은 한 잠금에만)",
             rc == 0 and r2.get("작성 완료 → 잠금(분)") == "작성자 실행 기록 없음", f"rc={rc} {rs}")

    all_good = True
    for name, good, detail in results:
        all_good &= good
        print(f"{'OK ' if good else 'BAD'} {name}{('  — ' + detail) if (detail and not good) else ''}")
    print("결과: 모든 사례가 기대와 일치" if all_good else "결과: 기대와 다른 사례가 있다")
    return 0 if all_good else 1


if __name__ == "__main__":
    sys.exit(main())
