"""하네스 자기 검사: 피드백 회차 감시의 작업자 로그 · 최종 응답, 피드백 전송 전 리드 분류 (WO-H92).

계약은 dev/WO-H92.md.
- W: 회차 감시(`--round` ≥ 1)는 같은 대화를 이어 가므로 첫 줄(대화 시작)이 오래된 로그도 찾고,
     최종 응답은 감시 시작 뒤의 답만 쓴다(없으면 비움 — 이전 회차 답을 새 답으로 보이지 않게).
- T: `feedback` 은 실패가 있어도 `--send --triage "<근거>"` 없이는 보내지 않는다(리드가 사례 결함을 먼저 거름, H89).
사용: python harness/selftest/check_h92_round_watch_cases.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "adapters" / "tools"
PY = sys.executable
NOW = datetime.now(timezone.utc)
OLD = NOW - timedelta(hours=2)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def load_watch():
    spec = importlib.util.spec_from_file_location("h92_orca_watch", TOOLS / "orca_watch.py")
    mod = importlib.util.module_from_spec(spec)
    if str(TOOLS) not in sys.path:
        sys.path.insert(0, str(TOOLS))
    spec.loader.exec_module(mod)
    return mod


def write_agy(root: Path, sid: str, ws: Path, rows_after: list[dict], first_at: datetime) -> Path:
    d = root / sid / ".system_generated" / "logs"
    d.mkdir(parents=True)
    wsj = str(ws).replace("\\", "/")
    rows = [
        {"source": "USER_EXPLICIT", "type": "USER_INPUT", "created_at": iso(first_at), "content": "<USER_REQUEST> brief"},
        {"source": "MODEL", "type": "PLANNER_RESPONSE", "created_at": iso(first_at + timedelta(seconds=5)),
         "content": "", "tool_calls": [{"name": "view_file", "args": {"AbsolutePath": wsj + "/src/a.py"}}]},
        {"source": "MODEL", "type": "PLANNER_RESPONSE", "created_at": iso(first_at + timedelta(seconds=60)),
         "content": "ROUND0-ANSWER"},
    ] + rows_after
    p = d / "transcript.jsonl"
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    return p


FAKE_ORCA = r'''
import json, sys
a = sys.argv[1:]
if a[:2] == ["terminal", "read"]:
    print("> "); sys.exit(0)
print(json.dumps({"ok": True})); sys.exit(0)
'''


def main() -> int:
    results: list[tuple[str, bool, str]] = []

    def case(name: str, good: bool, detail: str = "") -> None:
        results.append((name, good, detail))

    w = load_watch()
    import inspect
    sig = inspect.signature(w.find_worker_log)
    fr_sig = inspect.signature(w.final_response)
    has_cont = "continuing" in sig.parameters
    has_since = "since" in fr_sig.parameters
    case("W00 find_worker_log(continuing=) · final_response(since=) 인자", has_cont and has_since,
         f"{sig} / {fr_sig}")

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        base = Path(td)
        ws = base / "ws-h92"
        ws.mkdir()
        other = base / "ws-other"
        other.mkdir()
        start = NOW - timedelta(seconds=30)
        after = [
            {"source": "USER_EXPLICIT", "type": "USER_INPUT", "created_at": iso(start + timedelta(seconds=1)),
             "content": "<USER_REQUEST> [피드백 1/2]"},
            {"source": "MODEL", "type": "PLANNER_RESPONSE", "created_at": iso(start + timedelta(seconds=20)),
             "content": "ROUND1-ANSWER"},
        ]
        r_agy = base / "agy"
        log = write_agy(r_agy, "s1", ws, after, OLD)

        def find(agent, root, wsp, cont):
            if not has_cont:
                return "NO-ARG"
            return w.find_worker_log(agent, root, wsp, start, continuing=cont)

        case("W01 회차 감시(continuing): 첫 줄이 오래된 같은 대화 AGY 로그를 찾음", find("agy", r_agy, ws, True) == log,
             str(find("agy", r_agy, ws, True)))
        case("W02 첫 감시(continuing 아님)는 그대로 — 오래된 대화는 후보 아님(H82 유지)",
             find("agy", r_agy, ws, False) is None, str(find("agy", r_agy, ws, False)))
        case("W03 회차 감시라도 다른 작업 공간 로그는 아님", find("agy", r_agy, other, True) is None,
             str(find("agy", r_agy, other, True)))
        stale_root = base / "agy-stale"
        stale = write_agy(stale_root, "s2", ws, [], OLD)
        old_ts = (NOW - timedelta(hours=1)).timestamp()
        os.utime(stale, (old_ts, old_ts))
        case("W04 회차 감시라도 감시 시작 전에 마지막으로 바뀐 로그는 아님(mtime)", find("agy", stale_root, ws, True) is None,
             str(find("agy", stale_root, ws, True)))
        two_root = base / "agy-two"
        older = write_agy(two_root, "s-old", ws, [], OLD)
        newer = write_agy(two_root, "s-new", ws, [], OLD)
        t_old = (start + timedelta(seconds=1)).timestamp()
        t_new = (start + timedelta(seconds=300)).timestamp()
        os.utime(older, (t_old, t_old))
        os.utime(newer, (t_new, t_new))
        case("W11 회차 감시: 같은 작업 공간 후보 둘이면 가장 최근에 바뀐 로그(마일스톤 검토 A04)",
             find("agy", two_root, ws, True) == newer, str(find("agy", two_root, ws, True)))

        # Codex: 같은 세션 파일을 이어 씀
        r_cx = base / "codex"
        (r_cx / "2026").mkdir(parents=True)
        cx = r_cx / "2026" / "rollout-x.jsonl"
        cx_rows = [
            {"type": "session_meta", "timestamp": iso(OLD), "payload": {"cwd": str(ws)}},
            {"type": "event_msg", "timestamp": iso(OLD + timedelta(seconds=60)),
             "payload": {"type": "task_complete", "last_agent_message": "CX-ROUND0"}},
            {"type": "event_msg", "timestamp": iso(start + timedelta(seconds=20)),
             "payload": {"type": "task_complete", "last_agent_message": "CX-ROUND1"}},
        ]
        cx.write_text("\n".join(json.dumps(r) for r in cx_rows) + "\n", encoding="utf-8")
        case("W05 Codex 회차 감시: 같은 세션 파일을 찾음 · 첫 감시는 아님",
             find("codex", r_cx, ws, True) == cx and find("codex", r_cx, ws, False) is None,
             f"{find('codex', r_cx, ws, True)} / {find('codex', r_cx, ws, False)}")

        def fresp(agent, p, since):
            if not has_since:
                return ("NO-ARG", False)
            return w.final_response(agent, p, since=since)

        case("W06 최종 응답: since 뒤의 답(ROUND1)만", fresp("agy", log, start)[0] == "ROUND1-ANSWER",
             str(fresp("agy", log, start)))
        only_old_root = base / "agy-noanswer"
        noans = write_agy(only_old_root, "s3", ws, after[:1], OLD)
        case("W07 이번 회차 답이 아직 없으면 비움(이전 회차 답을 새 답으로 쓰지 않음)",
             fresp("agy", noans, start)[0] is None, str(fresp("agy", noans, start)))
        case("W08 since 없으면 지금처럼 마지막 답", w.final_response("agy", log)[0] == "ROUND1-ANSWER", "")
        case("W09 Codex since: CX-ROUND1 · 이번 회차 답 없으면 비움",
             fresp("codex", cx, start)[0] == "CX-ROUND1"
             and fresp("codex", cx, start + timedelta(seconds=60))[0] is None,
             f"{fresp('codex', cx, start)}")

        # 끝에서 끝: --round 1 감시가 최종 응답 파일에 ROUND1 을 남김
        fake = base / "fake_orca.py"
        fake.write_text(FAKE_ORCA, encoding="utf-8")
        subprocess.run(["git", "init", "-q", str(ws)], check=True)
        e2e_root = base / "agy-e2e"
        e2e_start = datetime.now(timezone.utc)
        after_e2e = [
            {"source": "USER_EXPLICIT", "type": "USER_INPUT", "created_at": iso(e2e_start + timedelta(seconds=30)),
             "content": "<USER_REQUEST> [피드백 1/2]"},
            {"source": "MODEL", "type": "PLANNER_RESPONSE", "created_at": iso(e2e_start + timedelta(seconds=31)),
             "content": "E2E-ROUND1"},
        ]
        write_agy(e2e_root, "s4", ws, after_e2e, OLD)
        logf = base / "WO-H92-term_e2e.r1.log"
        cmd = [PY, str(TOOLS / "orca_watch.py"), "--terminal", "term_e2e", "--workspace", str(ws), "--agent", "agy",
               "--wo", "WO-H92", "--role", "implementer", "--cap-seconds", "30", "--interval", "1",
               "--orca", str(fake), "--transcript-root", str(e2e_root), "--save-final", "--log", str(logf),
               "--round", "1"]
        p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=90)
        fin = logf.with_name(logf.stem + ".final.md")
        body = fin.read_text(encoding="utf-8") if fin.exists() else ""
        case("W10 끝에서 끝: --round 1 감시의 최종 응답 파일에 이번 회차 답 · 작업자 로그 경로",
             "E2E-ROUND1" in body and "ROUND0-ANSWER" not in body and "작업자 로그: 없음" not in body,
             f"rc={p.returncode} {body[:300]} {p.stderr[-300:]}")

    # ---- T: feedback 전송 전 리드 분류 ----
    disp = (TOOLS / "orca_dispatch.py").read_text(encoding="utf-8")
    case("T00 feedback 에 --send · --triage 인자", "--send" in disp and "--triage" in disp, "")
    t_res = run_feedback_cases()
    results.extend(t_res)

    bad = [x for x in results if not x[1]]
    for name, good, detail in results:
        print(f"{'OK ' if good else 'BAD'} {name}{('  — ' + detail) if detail and not good else ''}")
    print(f"\n{len(results) - len(bad)}/{len(results)} 통과")
    return 1 if bad else 0


FAKE_ORCA_FB = r'''
import json, os, sys
from pathlib import Path
st = Path(os.environ["FAKE_ORCA_STATE"])
st.mkdir(parents=True, exist_ok=True)
a = sys.argv[1:]
with (st / "calls.log").open("a", encoding="utf-8") as f:
    f.write(json.dumps(a, ensure_ascii=False) + "\n")
if a[:2] == ["terminal", "read"]:
    print("> working"); sys.exit(0)
if a[:2] == ["terminal", "wait"]:
    print(json.dumps({"ok": False})); sys.exit(0)
print(json.dumps({"ok": True})); sys.exit(0)
'''


def run_feedback_cases() -> list[tuple[str, bool, str]]:
    out: list[tuple[str, bool, str]] = []

    def case(name: str, good: bool, detail: str = "") -> None:
        out.append((name, good, detail))

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        base = Path(td)
        proj = base / "proj"
        import shutil
        for top in ("adapters", "harness"):
            shutil.copytree(ROOT / top, proj / top, ignore=shutil.ignore_patterns("__pycache__"))
        (proj / "reports" / "watch").mkdir(parents=True)
        fake = base / "fake.py"
        fake.write_text(FAKE_ORCA_FB, encoding="utf-8")

        def make(name: str) -> tuple[Path, Path, Path]:
            ws = base / f"ws-{name}"
            (ws / "chk").mkdir(parents=True)
            subprocess.run(["git", "init", "-q", str(ws)], check=True)
            (ws / "chk" / "bad.py").write_text("print('BAD-TAIL-MARK')\nraise SystemExit(3)\n", encoding="utf-8")
            (ws / "chk" / "ok.py").write_text("print('fine')\n", encoding="utf-8")
            st = base / f"st-{name}"
            st.mkdir()
            watch = [PY, str(proj / "adapters" / "tools" / "orca_watch.py"), "--terminal", "term_h92fb-0001",
                     "--workspace", str(ws), "--agent", "agy", "--wo", f"WO-T{name}", "--role", "implementer",
                     "--cap-seconds", "5", "--interval", "1", "--orca", str(fake),
                     "--log", str(proj / "reports" / "watch" / f"WO-T{name}-term_h92fb-0.log")]
            state = {"handle": "term_h92fb-0001", "workspace": str(ws), "watch": watch, "wo": f"WO-T{name}",
                     "agent": "agy", "role": "implementer", "cap_seconds": 5, "no_window": True, "round": 0,
                     "orca": str(fake)}
            sp = proj / "reports" / "watch" / f"WO-T{name}-term_h92fb-0.dispatch.json"
            sp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            return ws, st, sp

        def fb(st: Path, sp: Path, *args: str):
            r = subprocess.run([PY, str(proj / "adapters" / "tools" / "orca_dispatch.py"), "feedback",
                                "--state", str(sp), "--orca", str(fake), *args], cwd=proj, capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=120,
                               env=dict(os.environ, FAKE_ORCA_STATE=str(st), PYTHONIOENCODING="utf-8"))
            if "invalid choice" in r.stderr or "unrecognized arguments" in r.stderr:
                r.returncode = 99
            try:
                j = json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else {}
            except Exception:
                j = {}
            return r, j

        def sends(st: Path) -> list[list[str]]:
            p = st / "calls.log"
            calls = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []
            return [c for c in calls if c[:2] == ["terminal", "send"]]

        def state(sp: Path) -> dict:
            return json.loads(sp.read_text(encoding="utf-8"))

        ws, st, sp = make("01")
        r, j = fb(st, sp, "--run", "chk/bad.py")
        msgs = sorted((proj / "reports" / "watch").glob("WO-T01-*.r1.message.txt"))
        case("T01 실패해도 --send 없으면 전송 안 함 · 종료 0 · sent false · round 그대로 · 보낼 메시지 파일 남김",
             r.returncode == 0 and j.get("passed") is False and j.get("sent") is False and not sends(st)
             and state(sp).get("round") == 0 and len(msgs) == 1 and "BAD-TAIL-MARK" in msgs[0].read_text(encoding="utf-8")
             and j.get("message") == str(msgs[0]),
             f"rc={r.returncode} {j} msgs={msgs} {r.stderr[-200:]}")

        ws, st, sp = make("02")
        marker = base / "t02-ran.txt"
        (ws / "chk" / "mark.py").write_text(f"open({str(marker)!r}, 'w').write('x')\nraise SystemExit(1)\n",
                                            encoding="utf-8")
        r, j = fb(st, sp, "--run", "chk/mark.py", "--send")
        case("T02 --send 인데 --triage 없음 → 종료 2, 실행 · 전송 없음",
             r.returncode == 2 and not marker.exists() and not sends(st), f"rc={r.returncode} {r.stderr[-200:]}")
        r, j = fb(st, sp, "--run", "chk/mark.py", "--send", "--triage", "   ")
        case("T03 --triage 가 공백뿐 → 종료 2", r.returncode == 2 and not sends(st), f"rc={r.returncode}")

        ws, st, sp = make("04")
        note = "F05 · R11 은 리드 사례 결함(autocrlf) — 제외, C04 만 구현자 원인 SECRET-TRIAGE"
        r, j = fb(st, sp, "--run", "chk/bad.py", "--send", "--triage", note)
        s = sends(st)
        text = ""
        if s and "--text" in s[0]:
            text = s[0][s[0].index("--text") + 1]
        stt = state(sp)
        case("T04 --send --triage → 전송 1회 · round 1 · 상태에 triage_r1 기록",
             r.returncode == 0 and j.get("sent") is True and len(s) == 1 and stt.get("round") == 1
             and stt.get("triage_r1") == note, f"rc={r.returncode} {j} {stt.get('triage_r1')} {r.stderr[-200:]}")
        case("T05 분류 근거는 워커에게 보내지 않음", bool(text) and "SECRET-TRIAGE" not in text, text[:200])
        case("T06 메시지: 사례 이름 · 호출 스택 · 실행 환경을 보고 동작을 바꾸지 말 것 · 사례가 틀려 보이면 근거 보고",
             "호출 스택" in text and "사례 이름" in text and "근거" in text, text[:400])

        ws, st, sp = make("07")
        r, j = fb(st, sp, "--run", "chk/ok.py", "--send", "--triage", "x")
        case("T07 모두 통과면 --send 여도 전송 없음 · round 그대로",
             r.returncode == 0 and j.get("passed") is True and not sends(st) and state(sp).get("round") == 0,
             f"rc={r.returncode} {j}")
    return out


if __name__ == "__main__":
    sys.exit(main())
