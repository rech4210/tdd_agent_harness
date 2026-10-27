"""하네스 자기 검사: Orca 워커 감시 도구 (WO-H43).

계약 (adapters/tools/orca_watch.py, Core 밖 · 소비 프로젝트에 배포. 별도 PowerShell 창은 orca_watch.ps1 이 연다)
- 인자: --terminal <Orca 터미널 핸들> --workspace <작업 공간> --agent agy|codex --wo <WO> --role <역할>
  --cap-seconds <N≥1> [--interval <초, 기본 60, ≥1>] [--checkpoints <초,초,...>] [--orca <실행 파일, .py 면 현재 파이썬으로>]
  [--log <경로, 기본 reports/watch/<WO>-<핸들 앞 12자>.log>] [--transcript-root <경로>] [--model] [--tier] [--risk] [--round]
  [--print-plan]. 잘못된 인자(필수 누락, cap · interval < 1, agent 가 agy|codex 밖)는 종료 2.
- 매 간격: `orca terminal read --terminal H` 화면과 작업 공간 `git status --porcelain` 변경 파일 수를 콘솔에 한 줄로 보인다.
  화면 줄 = 비어 있지 않은 마지막 줄. 단, 구분선(─ 등 상자 문자와 공백만)과 하단 안내(“esc to cancel”, “for shortcuts”,
  “Accept-edits mode”, “accept-edits ·” 가 든 줄)는 제외, 120자까지.
- 끝남 감지(매 간격): `orca terminal wait --terminal H --for tui-idle --timeout-ms 1000 --json` 이 ok=true 면
  로그에 “종료(유휴)” 한 줄, 종료 0. `terminal read` 가 실패하면(터미널 없음) 로그에 “종료(터미널 없음)”, 종료 0.
  두 경우 모두 중단 신호를 보내지 않고 사용량을 기록하지 않는다(리드가 수집).
- 중간 확인: 기본 시점은 cap ≤ 600 이면 cap 의 30% · 60% · 100%(600 → 180 · 360 · 600), cap > 600 이면 300초 간격으로 cap 까지
  (1200 → 300 · 600 · 900 · 1200). --checkpoints 로 바꿀 수 있다. 시점마다 로그에 “중간 확인 i/n” 한 줄(변경 파일 수 + 화면 줄).
  --print-plan 은 시점 목록(쉼표)만 출력하고 종료 0.
- 상한: 경과 ≥ cap 이고 끝나지 않았으면 `orca terminal send --terminal H --interrupt` 를 **한 번** 보내고 로그에 “상한 도달” 한 줄,
  자기 저장소의 adapters/tools/collect_usage.py 로 model_usage 를 기록한다: cap {time, N, s}, stop {cap, watcher}.
  작업자 로그 찾기 — agy: <root>/*/.system_generated/logs/transcript.jsonl, codex: <root>/**/rollout-*.jsonl 중
  내용에 작업 공간 폴더 이름이 있고 첫 줄 시각(created_at 또는 timestamp)이 감시 시작 120초 전 이후인 것, 그중 첫 줄 시각이
  가장 이른 것(보조 에이전트 제외). 없으면 자기 저장소 harness/usage.py 로 run_id “orca:<핸들>”, 토큰 미상으로 기록. 종료 3.
- 작업 공간 파일을 바꾸지 않는다.

사용: python harness/selftest/check_orca_watch_cases.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable
TOOL = Path("adapters") / "tools" / "orca_watch.py"
H = "term_aaaabbbbcccc-dddd"

FAKE_ORCA = r'''
import json, os, sys, time
from pathlib import Path
st = Path(os.environ["FAKE_ORCA_STATE"])
a = sys.argv[1:]
def opt(n):
    return a[a.index(n) + 1] if n in a else None
if a[:2] == ["terminal", "read"]:
    if (st / "gone").exists():
        print("terminal_not_found", file=sys.stderr); sys.exit(1)
    sys.stdout.buffer.write((st / "screen.txt").read_bytes()); sys.exit(0)
if a[:2] == ["terminal", "wait"]:
    if (st / "idle").exists():
        print(json.dumps({"ok": True})); sys.exit(0)
    time.sleep(0.2); print(json.dumps({"ok": False, "error": {"code": "timeout"}})); sys.exit(0)
if a[:2] == ["terminal", "send"]:
    if (st / "sendfail").exists():
        print("send_failed", file=sys.stderr); sys.exit(1)
    with (st / "sent.log").open("a", encoding="utf-8") as f:
        f.write(" ".join(a) + "\n")
    print("Sent 1 bytes"); sys.exit(0)
print("unknown", a, file=sys.stderr); sys.exit(1)
'''

SCREEN = ("  ▄▀▀▄        Antigravity CLI\n"
          "────────────────────────────\n"
          "> 지시서를 읽고 고치세요\n"
          "● Explored 6 files (WO-H42-PROMPT.md, +5)\n"
          "────────────────────────────\n"
          "> Accept-edits mode: file edits auto-approved\n"
          "────────────────────────────\n"
          "esc to cancel                         accept-edits · Gemini\n")


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def make_project(base: Path) -> Path:
    proj = base / "proj"
    shutil.copytree(ROOT / "harness", proj / "harness", ignore=shutil.ignore_patterns("__pycache__", "selftest"))
    shutil.copytree(ROOT / "adapters", proj / "adapters", ignore=shutil.ignore_patterns("__pycache__"))
    (proj / "reports").mkdir()
    return proj


def make_workspace(base: Path, name: str, dirty: int) -> Path:
    ws = base / name
    ws.mkdir(parents=True)
    for cmd in (["git", "init", "-q", "-b", "main"], ["git", "config", "user.email", "t@example.invalid"],
                ["git", "config", "user.name", "t"]):
        subprocess.run(cmd, cwd=ws, capture_output=True, check=True)
    (ws / "base.txt").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=ws, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-qm", "b"], cwd=ws, capture_output=True, check=True)
    for i in range(dirty):
        (ws / f"new{i}.txt").write_text("y\n", encoding="utf-8")
    return ws


def agy_tr(root: Path, conv: str, first: datetime, mention: str, source: str = "USER_EXPLICIT") -> None:
    """AGY 대화 로그. mention = 워커가 일한 작업 공간 경로 — 실제 로그처럼 MODEL 행의 tool_calls 인자에 남긴다(WO-H82)."""
    d = root / conv / ".system_generated" / "logs"
    d.mkdir(parents=True)
    rows = [{"step_index": 0, "source": source, "created_at": iso(first), "status": "DONE",
             "content": "<USER_REQUEST>Read the brief in this workspace</USER_REQUEST>"},
            {"step_index": 1, "source": "MODEL", "created_at": iso(first + timedelta(seconds=5)), "status": "DONE",
             "input_tokens": 1000, "cache_read_tokens": 500, "output_tokens": 100,
             "tool_calls": [{"name": "view_file", "args": {"AbsolutePath": f"{mention}/briefs/brief.md"}}]}]
    (d / "transcript.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def codex_rollout(root: Path, sid: str, first: datetime, cwd: str) -> None:
    d = root / "2026" / "10" / "19"
    d.mkdir(parents=True, exist_ok=True)
    rows = [{"timestamp": iso(first), "type": "session_meta", "payload": {"id": sid, "cli_version": "0.160.0", "cwd": cwd}},
            {"timestamp": iso(first + timedelta(seconds=1)), "type": "turn_context",
             "payload": {"model": "gpt-6-luna", "effort": "medium", "sandbox_policy": {"type": "workspace-write"},
                         "approval_policy": "never"}},
            {"timestamp": iso(first + timedelta(seconds=9)), "type": "event_msg", "payload": {"type": "token_count", "info": {
                "total_token_usage": {"input_tokens": 1000, "cached_input_tokens": 600, "output_tokens": 50,
                                      "reasoning_output_tokens": 5, "total_tokens": 1050}}}}]
    (d / f"rollout-{sid}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def files(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*"))
            if p.is_file() and ".git" not in p.relative_to(root).parts}


def usage_events(proj: Path) -> list[dict]:
    ev = proj / "reports" / "events.jsonl"
    if not ev.exists():
        return []
    return [e for e in (json.loads(l) for l in ev.read_text(encoding="utf-8").splitlines() if l.strip())
            if e.get("kind") == "model_usage"]


def main() -> int:
    rows: list[tuple[str, bool, str]] = []

    def case(name: str, good: bool, detail: str = "") -> None:
        rows.append((name, bool(good), detail.replace("\n", " | ")[:260]))

    base = Path(tempfile.mkdtemp(prefix="orca-watch-"))
    try:
        proj = make_project(base)
        tool = proj / TOOL
        fake = base / "fake_orca.py"
        fake.write_text(FAKE_ORCA, encoding="utf-8")
        if not tool.exists():
            case("W00 도구 파일 adapters/tools/orca_watch.py · orca_watch.ps1", False, "없음")
            raise SystemExit

        def watch(state: Path, ws: Path, *extra: str, agent: str = "agy", root: Path | None = None,
                  log: Path | None = None, timeout: int = 60) -> subprocess.CompletedProcess[str]:
            state.mkdir(parents=True, exist_ok=True)
            if not (state / "screen.txt").exists():
                (state / "screen.txt").write_text(SCREEN, encoding="utf-8")
            args = [PY, str(tool), "--terminal", H, "--workspace", str(ws), "--agent", agent, "--wo", "WO-X9",
                    "--role", "implementer", "--orca", str(fake), "--log", str(log or state / "watch.log"),
                    "--transcript-root", str(root or base / "empty-root"), *extra]
            env = dict(**__import__("os").environ, FAKE_ORCA_STATE=str(state), PYTHONIOENCODING="utf-8")
            return subprocess.run(args, cwd=proj, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  env=env, timeout=timeout)

        def logtext(state: Path) -> str:
            p = state / "watch.log"
            return p.read_text(encoding="utf-8") if p.exists() else ""

        # W01 유휴 → 종료 0, 중단 신호 · 사용량 기록 없음
        st = base / "s1"
        ws = make_workspace(base, "ws1", 0)
        st.mkdir()
        (st / "idle").write_text("", encoding="utf-8")
        r = watch(st, ws, "--cap-seconds", "30", "--interval", "1")
        lg = logtext(st)
        case("W01 작업자 유휴 → “종료(유휴)” 로그, 종료 0, 중단 신호 · 사용량 기록 없음",
             r.returncode == 0 and "종료(유휴)" in lg and not (st / "sent.log").exists() and not usage_events(proj),
             r.stdout[-200:] + r.stderr[-200:] + " LOG " + lg)

        # W02 상한 → 중단 신호 한 번, 종료 3, 로그, model_usage(cap · stop, 주 대화 run_id)
        now = datetime.now(timezone.utc)
        root = base / "brain"
        agy_tr(root, "conv-main", now - timedelta(seconds=30), str(base / "wo-x9-impl"))
        agy_tr(root, "conv-sub", now - timedelta(seconds=20), str(base / "wo-x9-impl"), source="SYSTEM")
        agy_tr(root, "conv-other", now - timedelta(seconds=40), str(base / "other-ws"))
        agy_tr(root, "conv-old", now - timedelta(hours=3), str(base / "wo-x9-impl"))
        st = base / "s2"
        ws = make_workspace(base, "wo-x9-impl", 2)
        before = files(ws)
        r = watch(st, ws, "--cap-seconds", "3", "--interval", "1", "--checkpoints", "1,2,3",
                  "--model", "gemini-3.8-flash-high", "--tier", "T2", "--risk", "low", root=root)
        lg = logtext(st)
        sent = (st / "sent.log").read_text(encoding="utf-8") if (st / "sent.log").exists() else ""
        ev = usage_events(proj)
        e = ev[-1] if ev else {}
        case("W02 상한 도달 → 중단 신호(--interrupt, 핸들) 정확히 한 번, “상한 도달” 로그, 종료 3",
             r.returncode == 3 and sent.count("--interrupt") == 1 and H in sent and "상한 도달" in lg,
             f"rc={r.returncode} sent={sent!r} LOG {lg} ERR {r.stderr[-200:]}")
        case("W03 상한 기록: model_usage cap{time,3,s} · stop{cap,watcher}, run_id = 주 대화(보조 · 다른 작업 공간 · 오래된 것 제외)",
             len(ev) == 1 and e.get("run_id") == "conv-main" and e.get("agent") == "agy" and e.get("wo") == "WO-X9"
             and (e.get("cap") or {}).get("kind") == "time" and float((e.get("cap") or {}).get("value", -1)) == 3
             and (e.get("cap") or {}).get("unit") == "s"
             and (e.get("stop") or {}).get("reason") == "cap" and (e.get("stop") or {}).get("by") == "watcher",
             json.dumps(ev, ensure_ascii=False)[:240])
        cps = [l for l in lg.splitlines() if "중간 확인" in l]
        case("W04 중간 확인 시점마다 한 줄: 변경 파일 수 + 화면 줄(구분선 · 하단 안내 제외)",
             len(cps) >= 2 and all("2" in l for l in cps) and any("Explored 6 files" in l for l in cps)
             and not any("esc to cancel" in l or "Accept-edits" in l for l in cps), lg)
        case("W05 감시 도구는 작업 공간 파일을 바꾸지 않음", files(ws) == before, "")
        case("W06 매 간격 콘솔에 화면 줄 · 변경 파일 수 표시", "Explored 6 files" in r.stdout, r.stdout[-200:])

        # W07 작업자 로그 못 찾음 → run_id orca:<핸들>, 토큰 미상, cap · stop
        st = base / "s3"
        ws = make_workspace(base, "ws-none", 0)
        r = watch(st, ws, "--cap-seconds", "2", "--interval", "1", "--checkpoints", "2")
        ev = usage_events(proj)
        e = ev[-1] if ev else {}
        case("W07 작업자 로그 없음 → model_usage run_id “orca:<핸들>”, 토큰 미상, cap · stop 기록, 종료 3",
             r.returncode == 3 and len(ev) == 2 and e.get("run_id") == f"orca:{H}" and e.get("tokens") in (None, {})
             and (e.get("stop") or {}).get("reason") == "cap", json.dumps(e, ensure_ascii=False)[:240] + r.stderr[-150:])

        # W08 터미널 없음 → 종료(터미널 없음), 종료 0, 중단 신호 없음
        st = base / "s4"
        ws = make_workspace(base, "ws4", 0)
        st.mkdir()
        (st / "gone").write_text("", encoding="utf-8")
        n_ev = len(usage_events(proj))
        r = watch(st, ws, "--cap-seconds", "30", "--interval", "1")
        case("W08 터미널 없음 → “종료(터미널 없음)”, 종료 0, 중단 신호 · 기록 없음",
             r.returncode == 0 and "종료(터미널 없음)" in logtext(st) and not (st / "sent.log").exists()
             and len(usage_events(proj)) == n_ev, r.stdout[-150:] + r.stderr[-150:])

        # W09 codex: rollout 에서 작업 공간 cwd 가 든 세션
        croot = base / "codex-sessions"
        codex_rollout(croot, "sess-ok", now - timedelta(seconds=15), str(base / "wo-x9-codex"))
        codex_rollout(croot, "sess-other", now - timedelta(seconds=10), str(base / "elsewhere"))
        st = base / "s5"
        ws = make_workspace(base, "wo-x9-codex", 0)
        r = watch(st, ws, "--cap-seconds", "2", "--interval", "1", "--checkpoints", "2", agent="codex", root=croot)
        ev = usage_events(proj)
        e = ev[-1] if ev else {}
        case("W09 codex: 작업 공간 cwd 가 든 세션 로그로 기록(agent codex, run_id = 세션 id)",
             r.returncode == 3 and e.get("agent") == "codex" and e.get("run_id") == "sess-ok", json.dumps(e)[:200])

        # W10 기본 중간 확인 시점
        p1 = watch(base / "s6", ws, "--cap-seconds", "600", "--print-plan")
        p2 = watch(base / "s6", ws, "--cap-seconds", "1200", "--print-plan")
        case("W10 기본 시점: 600 → 180,360,600 · 1200 → 300,600,900,1200",
             p1.returncode == 0 and p1.stdout.strip() == "180,360,600"
             and p2.returncode == 0 and p2.stdout.strip() == "300,600,900,1200", p1.stdout + "/" + p2.stdout)

        # W11 잘못된 인자 → 종료 2
        bad = [watch(base / "s7", ws, "--cap-seconds", "0"),
               watch(base / "s7", ws, "--cap-seconds", "10", "--interval", "0"),
               watch(base / "s7", ws, "--cap-seconds", "10", agent="unsupported-agent")]
        case("W11 잘못된 인자(cap 0, interval 0, agent 밖) → 종료 2", all(b.returncode == 2 for b in bad),
             str([b.returncode for b in bad]))

        # 리드 판정에서 추가 (W13~W15)
        lg1 = logtext(base / "s1")
        case("W13 시작 직후(첫 간격 전)의 유휴는 종료로 보지 않음 — 종료(유휴)는 첫 간격 뒤",
             "[00:00] 종료(유휴)" not in lg1 and "종료(유휴)" in lg1, lg1)
        broot = base / "broken-root"
        d = broot / "conv-bad" / ".system_generated" / "logs"
        d.mkdir(parents=True)
        # 작업 공간 근거(도구 호출 경로, WO-H82)는 있지만 뒤 줄이 깨져 수집기가 실패하는 로그
        (d / "transcript.jsonl").write_text(
            json.dumps({"step_index": 0, "source": "USER_EXPLICIT", "created_at": iso(datetime.now(timezone.utc)),
                        "content": "Read the brief"}) + "\n"
            + json.dumps({"step_index": 1, "source": "MODEL", "created_at": iso(datetime.now(timezone.utc)),
                          "tool_calls": [{"name": "view_file", "args": {"AbsolutePath": str(base / "ws-broken" / "a.md")}}]})
            + "\n{깨진 줄\n", encoding="utf-8")
        st = base / "s8"
        ws = make_workspace(base, "ws-broken", 0)
        st.mkdir()
        (st / "sendfail").write_text("", encoding="utf-8")
        r = watch(st, ws, "--cap-seconds", "2", "--interval", "1", "--checkpoints", "2", root=broot)
        lg = logtext(st)
        case("W14 중단 신호 실패 → 로그에 “중단 신호 실패”, “작업자 중단” 이라 쓰지 않음",
             "중단 신호 실패" in lg and "작업자 중단" not in lg, lg)
        case("W15 사용량 기록 결과를 로그에 남김(수집기 실패 = “기록 실패”), 종료 3 유지",
             r.returncode == 3 and "기록 실패" in lg, f"rc={r.returncode} LOG {lg} ERR {r.stderr[-150:]}")
        lg2 = logtext(base / "s2")
        case("W16 기록 성공도 로그에 남김(“기록:” + run_id)", "기록:" in lg2 and "conv-main" in lg2, lg2)

        ps1 = proj / "adapters" / "tools" / "orca_watch.ps1"
        case("W12 PowerShell 창 진입점 orca_watch.ps1 이 orca_watch.py 로 인자를 넘김",
             ps1.exists() and "orca_watch.py" in ps1.read_text(encoding="utf-8", errors="replace"), "")
    except SystemExit:
        pass
    except subprocess.TimeoutExpired as ex:
        case("W99 시간 초과 — 감시 도구가 상한 뒤에도 끝나지 않음", False, str(ex)[:200])
    finally:
        shutil.rmtree(base, ignore_errors=True)

    all_good = bool(rows)
    for name, good, detail in rows:
        all_good &= good
        print(f"{'OK ' if good else 'BAD'} {name}  — {detail}")
    print("결과: 모든 사례가 기대와 일치" if all_good else "결과: 기대와 다른 사례가 있다")
    return 0 if all_good else 1


if __name__ == "__main__":
    sys.exit(main())
