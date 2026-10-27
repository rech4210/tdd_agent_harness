"""하네스 자기 검사: 워커 실행 한 단계 · 최종 응답 저장 · 지시서 표현 검사 (WO-H78, HD-80).

계약은 dev/WO-H78.md §계약 S · D.
- S: adapters/tools/orca_watch.py --save-final — 모든 종료 경로에서 <감시 로그 stem>.final.md 에
  종료 · 작업자 로그 · 작업 사본 변경(--ignored 포함, git 실패 = 확인 실패) · 최종 응답(AGY transcript_full 우선,
  Codex task_complete.last_agent_message) 을 쓴다. 옵션이 없으면 기존 동작.
- D: adapters/tools/orca_dispatch.py brief-check · dispatch · await-final.

사용: python harness/selftest/check_h78_run_step_cases.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable
WATCH = Path("adapters") / "tools" / "orca_watch.py"
DISPATCH = Path("adapters") / "tools" / "orca_dispatch.py"
H = "term_aaaabbbbcccc-dddd"

# 감시 도구용 가짜 Orca (check_orca_watch_cases 와 같은 규약) + worktree create
FAKE_ORCA = r'''
import json, os, sys, time
from pathlib import Path
st = Path(os.environ["FAKE_ORCA_STATE"])
a = sys.argv[1:]
with (st / "calls.log").open("a", encoding="utf-8") as f:
    f.write(json.dumps(a, ensure_ascii=False) + "\n")
if a[:2] == ["worktree", "create"]:
    if (st / "createfail").exists():
        print(json.dumps({"ok": False, "error": {"code": "boom"}})); sys.exit(1)
    if (st / "createnotok").exists():
        print(json.dumps({"ok": False, "error": {"code": "boom"}})); sys.exit(0)
    ws = (st / "workspace.txt").read_text(encoding="utf-8").strip()
    wt = {} if (st / "nopath").exists() else {"path": ws}
    oc = {} if (st / "nohandle").exists() else {"handle": "term_dispatch-0001"}
    print(json.dumps({"ok": True, "result": {"worktree": wt, "launch": {"outcome": oc}}}))
    sys.exit(0)
if a[:2] == ["terminal", "read"]:
    if (st / "gone").exists():
        print("terminal_not_found", file=sys.stderr); sys.exit(1)
    sys.stdout.write("> working\n"); sys.exit(0)
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

FULL_ANSWER = "# 보고서\n\n" + "본문 줄\n" * 400 + "끝 문장 ZZ-END"


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def make_project(base: Path) -> Path:
    proj = base / "proj"
    shutil.copytree(ROOT / "harness", proj / "harness", ignore=shutil.ignore_patterns("__pycache__", "selftest"))
    shutil.copytree(ROOT / "adapters", proj / "adapters", ignore=shutil.ignore_patterns("__pycache__"))
    (proj / "reports").mkdir()
    return proj


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=True)


def make_workspace(base: Path, name: str, untracked: int = 0, ignored: int = 0) -> Path:
    ws = base / name
    ws.mkdir(parents=True)
    git(ws, "init", "-q", "-b", "main")
    git(ws, "config", "user.email", "t@example.invalid")
    git(ws, "config", "user.name", "t")
    (ws / "base.txt").write_text("x\n", encoding="utf-8")
    (ws / ".gitignore").write_text("*.log\n", encoding="utf-8")
    git(ws, "add", "-A")
    git(ws, "commit", "-qm", "b")
    for i in range(untracked):
        (ws / f"new{i}.txt").write_text("y\n", encoding="utf-8")
    for i in range(ignored):
        (ws / f"cache{i}.log").write_text("z\n", encoding="utf-8")
    return ws


def agy_logs(root: Path, conv: str, first: datetime, mention: str, rows: list[dict], *, full_rows: list[dict] | None,
             truncated: bool = False) -> Path:
    """AGY 주 대화 로그. rows 는 첫 USER 행 뒤에 붙는 MODEL 행들. full_rows 가 있으면 transcript_full.jsonl 도 만든다."""
    d = root / conv / ".system_generated" / "logs"
    d.mkdir(parents=True)
    head = {"step_index": 0, "source": "USER_EXPLICIT", "created_at": iso(first), "status": "DONE",
            "content": "<USER_REQUEST>Read the brief in this workspace</USER_REQUEST>"}
    # 실제 로그처럼 워커가 작업 공간 안 파일을 도구로 읽은 행 (작업 공간 = 이 사례들의 make_workspace(base, mention)) — WO-H82
    tool = {"step_index": 0, "source": "MODEL", "type": "PLANNER_RESPONSE", "created_at": iso(first), "status": "DONE",
            "tool_calls": [{"name": "view_file", "args": {"AbsolutePath": str(root.parent / mention / "briefs" / "brief.md")}}]}

    def dump(rs: list[dict], cut: bool) -> str:
        out = [json.dumps(head, ensure_ascii=False), json.dumps(tool, ensure_ascii=False)]
        for i, r in enumerate(rs, start=1):
            r = dict(r, step_index=i, created_at=iso(first + timedelta(seconds=i)), status="DONE")
            if cut and isinstance(r.get("content"), str) and len(r["content"]) > 200:
                r["content"] = r["content"][:100] + "\n<truncated 9999 bytes>\n" + r["content"][-50:]
                r["truncated_fields"] = "['content']"
            out.append(json.dumps(r, ensure_ascii=False))
        return "\n".join(out) + "\n"

    (d / "transcript.jsonl").write_text(dump(rows, truncated), encoding="utf-8")
    if full_rows is not None:
        (d / "transcript_full.jsonl").write_text(dump(full_rows, False), encoding="utf-8")
    return d / "transcript.jsonl"


def planner(content: str | None, tool_calls: object = None) -> dict:
    r: dict = {"source": "MODEL", "type": "PLANNER_RESPONSE", "input_tokens": 10, "output_tokens": 5}
    if content is not None:
        r["content"] = content
    if tool_calls is not None:
        r["tool_calls"] = tool_calls
    return r


def codex_rollout(root: Path, sid: str, first: datetime, cwd: str, messages: list[object], *,
                  junk: bool = False) -> Path:
    d = root / "2026" / "10" / "26"
    d.mkdir(parents=True, exist_ok=True)
    rows: list[object] = [
        {"timestamp": iso(first), "type": "session_meta", "payload": {"id": sid, "cli_version": "0.160.0", "cwd": cwd}},
        {"timestamp": iso(first + timedelta(seconds=1)), "type": "turn_context",
         "payload": {"model": "gpt-6-luna", "sandbox_policy": {"type": "workspace-write"}, "approval_policy": "never"}},
        {"timestamp": iso(first + timedelta(seconds=2)), "type": "event_msg", "payload": {"type": "token_count", "info": {
            "total_token_usage": {"input_tokens": 100, "cached_input_tokens": 60, "output_tokens": 5}}}}]
    for i, m in enumerate(messages):
        rows.append({"timestamp": iso(first + timedelta(seconds=3 + i)), "type": "event_msg",
                     "payload": {"type": "task_complete", "last_agent_message": m}})
    text = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n"
    if junk:
        text += "{not json\n"
    p = d / f"rollout-{sid}.jsonl"
    p.write_text(text, encoding="utf-8")
    return p


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
        rows.append((name, bool(good), detail.replace("\n", " | ")[:300]))

    base = Path(tempfile.mkdtemp(prefix="h78-"))
    try:
        proj = make_project(base)
        fake = base / "fake_orca.py"
        fake.write_text(FAKE_ORCA, encoding="utf-8")
        watch_tool = proj / WATCH
        disp_tool = proj / DISPATCH

        def env_for(state: Path) -> dict:
            return dict(os.environ, FAKE_ORCA_STATE=str(state), PYTHONIOENCODING="utf-8")

        def watch(state: Path, ws: Path, *extra: str, agent: str = "agy", root: Path | None = None,
                  log: Path | None = None) -> subprocess.CompletedProcess[str]:
            state.mkdir(parents=True, exist_ok=True)
            args = [PY, str(watch_tool), "--terminal", H, "--workspace", str(ws), "--agent", agent, "--wo", "WO-X9",
                    "--role", "reader", "--orca", str(fake), "--log", str(log or state / "watch.log"),
                    "--transcript-root", str(root or base / "empty-root"), "--interval", "1", *extra]
            return subprocess.run(args, cwd=proj, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  env=env_for(state), timeout=90)

        def final_of(state: Path, name: str = "watch") -> str:
            p = state / f"{name}.final.md"
            return p.read_text(encoding="utf-8") if p.exists() else ""

        def log_of(state: Path) -> str:
            p = state / "watch.log"
            return p.read_text(encoding="utf-8") if p.exists() else ""

        now = datetime.now(timezone.utc)

        # ── S: 감시 도구 --save-final ─────────────────────────────────────────────
        # S01 유휴 + AGY transcript_full 있음 → 전문, 종료 idle_only, 변경 0, 작업자 로그 경로
        root = base / "brain1"
        ws = make_workspace(base, "h78-s01")
        tr = agy_logs(root, "conv-s01", now - timedelta(seconds=20), "h78-s01",
                      [planner("중간 설명", tool_calls=[{"name": "read_file"}]), planner(FULL_ANSWER)],
                      full_rows=[planner("중간 설명", tool_calls=[{"name": "read_file"}]), planner(FULL_ANSWER)],
                      truncated=True)
        before = files(ws)
        st = base / "s01"
        st.mkdir()
        (st / "idle").write_text("", encoding="utf-8")
        r = watch(st, ws, "--cap-seconds", "30", "--save-final", root=root)
        fin = final_of(st)
        head, _, body = fin.partition("\n---\n")
        case("S01 유휴 종료 + --save-final → watch.final.md 에 transcript_full 의 최종 응답 전문 (잘림 없음), 종료 0",
             r.returncode == 0 and "ZZ-END" in body and body.count("본문 줄") == 400 and "<truncated" not in body
             and "(잘림" not in body, f"rc={r.returncode} head={head[:200]} ERR {r.stderr[-200:]}")
        case("S02 머리말: 첫 줄 '# 최종 응답 · WO-X9 · reader · agy', '- 종료: idle_only', 작업자 로그 경로, 변경 0",
             fin.startswith("# 최종 응답 · WO-X9 · reader · agy") and "- 종료: idle_only" in head
             and str(tr.parent) in head and "- 작업 사본 변경(--ignored 포함): 0" in head, head[:300])
        case("S03 로그에 '최종 응답 저장:' 한 줄 (경로 · 변경 수)",
             "최종 응답 저장:" in log_of(st) and "watch.final.md" in log_of(st), log_of(st)[-200:])
        case("S04 작업 공간 파일을 바꾸지 않음", files(ws) == before, "")

        # S05 transcript_full 없음 + 잘린 행 → 잘린 본문 + 표시
        root = base / "brain2"
        ws = make_workspace(base, "h78-s05")
        agy_logs(root, "conv-s05", now - timedelta(seconds=20), "h78-s05", [planner(FULL_ANSWER)], full_rows=None,
                 truncated=True)
        st = base / "s05"
        st.mkdir()
        (st / "idle").write_text("", encoding="utf-8")
        r = watch(st, ws, "--cap-seconds", "30", "--save-final", root=root)
        body = final_of(st).partition("\n---\n")[2]
        case("S05 transcript_full 없음 · 잘린 행 → 잘린 본문 뒤 '(잘림: transcript_full.jsonl 없음)'",
             r.returncode == 0 and "ZZ-END" in body and "(잘림: transcript_full.jsonl 없음)" in body, body[-200:])

        # S06 마지막 MODEL 행이 도구 호출이면 그 앞의 도구 없는 PLANNER_RESPONSE, 빈 content · 빈 tool_calls 처리
        root = base / "brain3"
        ws = make_workspace(base, "h78-s06")
        rows6 = [planner("첫 답 AA-1"), planner("", tool_calls=None), planner("둘째 답 BB-2", tool_calls=[]),
                 {"source": "MODEL", "type": "GENERIC", "content": "도구 결과"}, planner("세째", tool_calls=[{"n": 1}])]
        agy_logs(root, "conv-s06", now - timedelta(seconds=20), "h78-s06", rows6, full_rows=rows6)
        st = base / "s06"
        st.mkdir()
        (st / "idle").write_text("", encoding="utf-8")
        watch(st, ws, "--cap-seconds", "30", "--save-final", root=root)
        body = final_of(st).partition("\n---\n")[2]
        case("S06 AGY: 도구 호출 · 빈 content · GENERIC 행 제외, 빈 tool_calls 는 도구 없음 → 'BB-2'",
             "BB-2" in body and "AA-1" not in body and "세째" not in body and "도구 결과" not in body, body[:200])

        # S07 Codex: 마지막 task_complete, 깨진 줄 무시
        root = base / "sessions1"
        ws = make_workspace(base, "h78-s07")
        codex_rollout(root, "sess-s07", now - timedelta(seconds=20), str(ws), ["첫 완료 C-1", "마지막 완료 C-2"], junk=True)
        st = base / "s07"
        st.mkdir()
        (st / "idle").write_text("", encoding="utf-8")
        r = watch(st, ws, "--cap-seconds", "30", "--save-final", agent="codex", root=root)
        fin = final_of(st)
        body = fin.partition("\n---\n")[2]
        case("S07 Codex: 마지막 task_complete.last_agent_message, 깨진 JSON 줄 무시",
             r.returncode == 0 and "C-2" in body and "C-1" not in body and "· codex" in fin.splitlines()[0],
             fin[:300])

        # S08 Codex: task_complete 없음 · 문자열 아님 → (최종 응답 없음)
        root = base / "sessions2"
        ws = make_workspace(base, "h78-s08")
        codex_rollout(root, "sess-s08", now - timedelta(seconds=20), str(ws), [None, {"text": "x"}])
        st = base / "s08"
        st.mkdir()
        (st / "idle").write_text("", encoding="utf-8")
        watch(st, ws, "--cap-seconds", "30", "--save-final", agent="codex", root=root)
        body = final_of(st).partition("\n---\n")[2]
        case("S08 Codex: last_agent_message 가 문자열이 아니면 '(최종 응답 없음)'", "(최종 응답 없음)" in body, body[:200])

        # S09 작업자 로그 없음 → 작업자 로그: 없음, (최종 응답 없음)
        ws = make_workspace(base, "h78-s09")
        st = base / "s09"
        st.mkdir()
        (st / "idle").write_text("", encoding="utf-8")
        watch(st, ws, "--cap-seconds", "30", "--save-final")
        fin = final_of(st)
        case("S09 작업자 로그 없음 → '- 작업자 로그: 없음' + '(최종 응답 없음)'",
             "- 작업자 로그: 없음" in fin and "(최종 응답 없음)" in fin, fin[:300])

        # S10 변경 수는 --ignored 포함, 줄 목록 표시
        ws = make_workspace(base, "h78-s10", untracked=1, ignored=1)
        st = base / "s10"
        st.mkdir()
        (st / "idle").write_text("", encoding="utf-8")
        watch(st, ws, "--cap-seconds", "30", "--save-final")
        head = final_of(st).partition("\n---\n")[0]
        case("S10 작업 사본 변경 = --ignored 포함 줄 수(추적 안 됨 1 + 무시 1 = 2), 줄 목록 표시",
             "- 작업 사본 변경(--ignored 포함): 2" in head and "new0.txt" in head and "cache0.log" in head, head)

        # S17 많은 변경 → 수는 전체(25), 줄 목록은 20줄까지
        ws = make_workspace(base, "h78-s17", untracked=25)
        st = base / "s17"
        st.mkdir()
        (st / "idle").write_text("", encoding="utf-8")
        watch(st, ws, "--cap-seconds", "30", "--save-final")
        head = final_of(st).partition("\n---\n")[0]
        listed = [l for l in head.splitlines() if l.startswith("  - ")]
        case("S17 변경 25개 → '- 작업 사본 변경(--ignored 포함): 25', 변경 줄('  - ' 시작)은 정확히 20줄",
             "- 작업 사본 변경(--ignored 포함): 25" in head and len(listed) == 20, f"listed={len(listed)} {head[:200]}")

        # S11 git 실패(저장소 아님) → 확인 실패 (0 아님)
        ws = base / "h78-s11-notgit"
        ws.mkdir()
        st = base / "s11"
        st.mkdir()
        (st / "idle").write_text("", encoding="utf-8")
        watch(st, ws, "--cap-seconds", "30", "--save-final")
        head = final_of(st).partition("\n---\n")[0]
        case("S11 작업 공간이 git 저장소가 아니면 '확인 실패' (0 으로 쓰지 않음)",
             "- 작업 사본 변경(--ignored 포함): 확인 실패" in head, head)

        # S12 상한 경로 → 저장, 종료 interrupt_requested, 종료 코드 3
        root = base / "brain4"
        ws = make_workspace(base, "h78-s12")
        agy_logs(root, "conv-s12", now - timedelta(seconds=20), "h78-s12", [planner("부분 답 P-12")],
                 full_rows=[planner("부분 답 P-12")])
        st = base / "s12"
        st.mkdir()
        r = watch(st, ws, "--cap-seconds", "2", "--save-final", root=root)
        fin = final_of(st)
        case("S12 상한 종료 → final 저장('- 종료: interrupt_requested', 부분 답), 종료 3 유지",
             r.returncode == 3 and "- 종료: interrupt_requested" in fin and "P-12" in fin, f"rc={r.returncode} {fin[:200]}")

        # S18 상한 + 중단 신호 실패 → stop_unverified, 종료 3
        ws = make_workspace(base, "h78-s18")
        st = base / "s18"
        st.mkdir()
        (st / "sendfail").write_text("", encoding="utf-8")
        r = watch(st, ws, "--cap-seconds", "2", "--save-final")
        case("S18 상한 · 중단 신호 실패 → '- 종료: stop_unverified', 종료 3",
             r.returncode == 3 and "- 종료: stop_unverified" in final_of(st), f"rc={r.returncode} {final_of(st)[:200]}")

        # S13 터미널 없음 → 저장, terminal_unavailable, 종료 0
        ws = make_workspace(base, "h78-s13")
        st = base / "s13"
        st.mkdir()
        (st / "gone").write_text("", encoding="utf-8")
        r = watch(st, ws, "--cap-seconds", "30", "--save-final")
        case("S13 터미널 없음 종료 → '- 종료: terminal_unavailable', 종료 0",
             r.returncode == 0 and "- 종료: terminal_unavailable" in final_of(st), final_of(st)[:200])

        # S14 옵션 없으면 final 파일 없음 (기존 동작)
        ws = make_workspace(base, "h78-s14")
        st = base / "s14"
        st.mkdir()
        (st / "idle").write_text("", encoding="utf-8")
        r = watch(st, ws, "--cap-seconds", "30")
        case("S14 --save-final 없으면 final 파일을 만들지 않음, 종료 0",
             r.returncode == 0 and not list(st.glob("*.final.md")), str(list(st.iterdir())))

        # S15 --collect-on-exit 와 함께 → 사용량 1건 + final
        root = base / "brain5"
        ws = make_workspace(base, "h78-s15")
        agy_logs(root, "conv-s15", now - timedelta(seconds=20), "h78-s15", [planner("답 Q-15")],
                 full_rows=[planner("답 Q-15")])
        n0 = len(usage_events(proj))
        st = base / "s15"
        st.mkdir()
        (st / "idle").write_text("", encoding="utf-8")
        r = watch(st, ws, "--cap-seconds", "30", "--save-final", "--collect-on-exit", root=root)
        ev = usage_events(proj)[n0:]
        case("S15 --collect-on-exit 와 함께 → model_usage 1건(run_id = 주 대화) + final 저장",
             r.returncode == 0 and len(ev) == 1 and ev[0].get("run_id") == "conv-s15" and "Q-15" in final_of(st),
             json.dumps(ev, ensure_ascii=False)[:200])

        # S16 --log 경로 이름 규칙: x/w.log → x/w.final.md
        ws = make_workspace(base, "h78-s16")
        st = base / "s16"
        st.mkdir()
        (st / "idle").write_text("", encoding="utf-8")
        lg = base / "s16-logs" / "custom.log"
        watch(st, ws, "--cap-seconds", "30", "--save-final", log=lg)
        case("S16 final 경로 = 감시 로그 stem + '.final.md' (--log 지정 시 같은 폴더)",
             (lg.parent / "custom.final.md").exists(), str(list(lg.parent.iterdir()) if lg.parent.exists() else []))

        # ── B: brief-check ────────────────────────────────────────────────────────
        def bc(*paths: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run([PY, str(disp_tool), "brief-check", *paths], cwd=proj, capture_output=True,
                                  text=True, encoding="utf-8", errors="replace", timeout=60)

        bdir = base / "briefs"
        bdir.mkdir()
        clean = bdir / "clean.md"
        clean.write_text("# 경계 입력 검증 사례 설계\n\n작업 사본을 바꾸지 말 것.\n", encoding="utf-8")
        r = bc(str(clean))
        case("B01 금지 표현 없음 → 'brief-check 통과: 1개 파일', 종료 0",
             r.returncode == 0 and "brief-check 통과: 1개 파일" in r.stdout, r.stdout + r.stderr)

        terms = ["레드팀", "Red Team", "red-team", "REDTEAM", "뚫림", "위조", "Attack", "bypass"]
        bad = []
        for i, t in enumerate(terms):
            p = bdir / f"bad{i}.md"
            p.write_text(f"# 제목\n\n둘째 줄\n셋째 줄에 {t} 있음\n", encoding="utf-8")
            before_b = p.read_bytes()
            r = bc(str(p))
            ok = r.returncode == 1 and f"bad{i}.md:4" in r.stdout and p.read_bytes() == before_b
            if not ok:
                bad.append(f"{t}: rc={r.returncode} out={r.stdout[:120]}")
        case("B02 표현마다(대소문자 무시) 파일:줄 출력 · 종료 1 · 파일 바뀌지 않음", not bad, "; ".join(bad))

        r = bc(str(clean), str(bdir / "bad0.md"))
        case("B03 여러 파일 중 하나만 걸려도 종료 1, 걸린 파일만 출력",
             r.returncode == 1 and "bad0.md:4" in r.stdout and "clean.md:" not in r.stdout, r.stdout)

        r = bc(str(bdir / "missing.md"))
        nb = bdir / "latin1.md"
        nb.write_bytes("caf\xe9 attack\n".encode("latin-1"))
        r2 = bc(str(nb))
        case("B04 없는 파일 · UTF-8 아닌 파일 → 종료 2", disp_tool.exists() and r.returncode == 2 and r2.returncode == 2,
             f"rc={r.returncode},{r2.returncode} {r.stderr[:80]} {r2.stderr[:80]}")

        two = bdir / "two.md"
        two.write_text("레드팀 1\n괜찮음\n공격 시나리오 아님 bypass\n", encoding="utf-8")
        r = bc(str(two))
        case("B05 한 파일 여러 줄 → 줄마다 출력 (1, 3)",
             r.returncode == 1 and "two.md:1" in r.stdout and "two.md:3" in r.stdout and "two.md:2" not in r.stdout,
             r.stdout)

        # ── D: dispatch ───────────────────────────────────────────────────────────
        origin = base / "origin"
        origin.mkdir()
        git(origin, "init", "-q", "-b", "main")
        git(origin, "config", "user.email", "t@example.invalid")
        git(origin, "config", "user.name", "t")
        (origin / "briefs").mkdir()
        (origin / "briefs" / "READ.md").write_text("# 읽기 조사\n", encoding="utf-8")
        (origin / "briefs" / "CASES.md").write_text("# 사례 설계 (레드팀)\n", encoding="utf-8")
        git(origin, "add", "-A")
        git(origin, "commit", "-qm", "briefs")
        base_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=origin, capture_output=True, text=True).stdout.strip()

        def dispatch(state: Path, brief: str, agent: str, *extra: str) -> subprocess.CompletedProcess[str]:
            state.mkdir(parents=True, exist_ok=True)
            args = [PY, str(disp_tool), "dispatch", "--origin", str(origin), "--repo", "id:fixture-repo",
                    "--name", "h78-d", "--base", base_sha, "--brief", brief, "--agent", agent, "--model", "m-1",
                    "--wo", "WO-X9", "--role", "reader", "--cap-seconds", "30", "--interval", "1",
                    "--orca", str(fake), "--transcript-root", str(base / "empty-root"), "--no-window", *extra]
            return subprocess.run(args, cwd=proj, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  env=env_for(state), timeout=60)

        def calls(state: Path) -> list[list[str]]:
            p = state / "calls.log"
            return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []

        # D01 codex + 금지 표현 → Orca 호출 없이 1
        st = base / "d01"
        r = dispatch(st, "briefs/CASES.md", "codex")
        case("D01 codex 지시서에 금지 표현 → Orca 호출 없이 종료 1, 줄 출력",
             r.returncode == 1 and not calls(st) and "CASES.md:1" in (r.stdout + r.stderr), r.stdout + r.stderr)

        # D02 기준 커밋에 지시서 없음 → Orca 호출 없이 2
        st = base / "d02"
        r = dispatch(st, "briefs/NOPE.md", "agy")
        case("D02 기준 커밋에 지시서 없음 → Orca 호출 없이 종료 2",
             disp_tool.exists() and r.returncode == 2 and not calls(st),
             r.stdout + r.stderr)

        # D03 agy 정상 → worktree create 인자, 감시 시작, JSON, final 생성
        st = base / "d03"
        st.mkdir()
        ws = make_workspace(base, "h78-d03")
        (ws / "briefs").mkdir()
        (ws / "briefs" / "CASES.md").write_text("x\n", encoding="utf-8")
        (st / "workspace.txt").write_text(str(ws), encoding="utf-8")
        (st / "idle").write_text("", encoding="utf-8")
        r = dispatch(st, "briefs/CASES.md", "agy", "--tier", "T2", "--risk", "low")
        try:
            out = json.loads(r.stdout.strip().splitlines()[-1])
        except Exception:
            out = {}
        cs = calls(st)
        create = next((c for c in cs if c[:2] == ["worktree", "create"]), [])

        def opt(c: list[str], n: str) -> str | None:
            return c[c.index(n) + 1] if n in c and c.index(n) + 1 < len(c) else None

        case("D03 agy: 금지 표현이 있어도 codex 가 아니면 검사하지 않음 · worktree create 인자 "
             "(--repo · --name · --no-parent · --base-branch · --setup skip · --agent antigravity · --model · --prompt · --json)",
             r.returncode == 0 and opt(create, "--repo") == "id:fixture-repo" and opt(create, "--name") == "h78-d"
             and "--no-parent" in create and opt(create, "--base-branch") == base_sha
             and opt(create, "--setup") == "skip" and opt(create, "--agent") == "antigravity"
             and opt(create, "--model") == "m-1" and "--json" in create
             and "briefs/CASES.md" in (opt(create, "--prompt") or "") and "--effort" not in create,
             f"rc={r.returncode} create={create} ERR {r.stderr[-200:]}")
        case("D04 출력 JSON: handle · workspace · log · final · brief_present=true, final = log stem + .final.md",
             out.get("handle") == "term_dispatch-0001" and Path(out.get("workspace", "")) == ws
             and str(out.get("log", "")).endswith(".log") and out.get("brief_present") is True
             and Path(out.get("final", "x")).name == Path(out.get("log", "y")).stem + ".final.md",
             json.dumps(out, ensure_ascii=False))
        case("D11 --prompt 는 계약 문장 그대로",
             opt(create, "--prompt") == "Read briefs/CASES.md in this workspace and do exactly what it says, "
                                         "without asking questions. Answer in Korean.", str(opt(create, "--prompt")))
        wcmd = out.get("watch") or []
        want = {"--terminal": "term_dispatch-0001", "--workspace": str(ws), "--agent": "agy", "--wo": "WO-X9",
                "--role": "reader", "--cap-seconds": "30", "--interval": "1", "--orca": str(fake),
                "--transcript-root": str(base / "empty-root"), "--model": "m-1", "--tier": "T2", "--risk": "low"}
        wrong = {k: opt(wcmd, k) for k, v in want.items() if opt(wcmd, k) != v}
        case("D10 출력 JSON 'watch'(감시 실행 인자 목록): 핸들 · 작업 공간 · agy · WO · 역할 · 상한 · 간격 · orca · "
             "transcript-root · 모델 · tier · risk 전달 + --collect-on-exit · --save-final, orca_watch 실행",
             isinstance(wcmd, list) and not wrong and "--collect-on-exit" in wcmd and "--save-final" in wcmd
             and any("orca_watch" in str(x) for x in wcmd), f"wrong={wrong} watch={wcmd}")
        fpath = Path(out.get("final", base / "nope"))
        deadline = time.monotonic() + 40
        while not fpath.exists() and time.monotonic() < deadline:
            time.sleep(0.5)
        fin = fpath.read_text(encoding="utf-8") if fpath.exists() else ""
        case("D05 감시를 --save-final 로 띄움 → final 파일 생성(종료 idle_only, 핸들 terminal read 호출)",
             "- 종료: idle_only" in fin and any(c[:2] == ["terminal", "read"] and "term_dispatch-0001" in c
                                               for c in calls(st)), fin[:200])

        # D06 codex 정상 + --effort 전달
        st = base / "d06"
        st.mkdir()
        (st / "workspace.txt").write_text(str(ws), encoding="utf-8")
        (st / "idle").write_text("", encoding="utf-8")
        r = dispatch(st, "briefs/READ.md", "codex", "--effort", "medium")
        create = next((c for c in calls(st) if c[:2] == ["worktree", "create"]), [])
        try:
            out6 = json.loads(r.stdout.strip().splitlines()[-1])
        except Exception:
            out6 = {}
        case("D06 codex: --agent codex · --effort medium 전달, 깨끗한 지시서는 통과, 감시 --agent codex",
             opt(create, "--agent") == "codex" and opt(create, "--effort") == "medium"
             and opt(out6.get("watch") or [], "--agent") == "codex", f"rc={r.returncode} {create}")
        f6 = Path(out6.get("final", base / "nope"))
        deadline = time.monotonic() + 40
        while not f6.exists() and time.monotonic() < deadline:  # 감시가 끝난 뒤 정리 (D12)
            time.sleep(0.5)

        # D09 ok 이지만 핸들 · 경로 없음 → 감시 없이 1
        for flag in ("nohandle", "nopath"):
            st = base / f"d09-{flag}"
            st.mkdir()
            (st / flag).write_text("", encoding="utf-8")
            (st / "workspace.txt").write_text(str(ws), encoding="utf-8")
            r = dispatch(st, "briefs/READ.md", "agy")
            time.sleep(2)
            case(f"D09 create 응답이 ok 지만 {flag} → 종료 1, 감시를 띄우지 않음",
                 r.returncode == 1 and not any(c[:2] == ["terminal", "read"] for c in calls(st)), r.stdout + r.stderr)

        # D07 create 실패(종료 코드 · ok false) → 감시 없이 1
        for label, flag in (("exit", "createfail"), ("notok", "createnotok")):
            st = base / f"d07-{label}"
            st.mkdir()
            (st / flag).write_text("", encoding="utf-8")
            (st / "workspace.txt").write_text(str(ws), encoding="utf-8")
            r = dispatch(st, "briefs/READ.md", "agy")
            time.sleep(2)
            case(f"D07 worktree create 실패({label}) → 종료 1, 감시를 띄우지 않음(terminal read 호출 없음)",
                 r.returncode == 1 and not any(c[:2] == ["terminal", "read"] for c in calls(st)), r.stdout + r.stderr)

        # D08 작업 공간에 지시서 없음 → 감시는 띄우고 brief_present=false, 종료 1
        st = base / "d08"
        st.mkdir()
        ws8 = make_workspace(base, "h78-d08")
        (st / "workspace.txt").write_text(str(ws8), encoding="utf-8")
        (st / "idle").write_text("", encoding="utf-8")
        r = dispatch(st, "briefs/READ.md", "agy")
        try:
            out = json.loads(r.stdout.strip().splitlines()[-1])
        except Exception:
            out = {}
        fpath = Path(out.get("final", base / "nope"))
        deadline = time.monotonic() + 40
        while not fpath.exists() and time.monotonic() < deadline:
            time.sleep(0.5)
        case("D08 작업 공간에 지시서 없음 → brief_present=false, 종료 1, 감시는 계속(final 생성)",
             r.returncode == 1 and out.get("brief_present") is False and fpath.exists(),
             f"rc={r.returncode} {r.stdout[-200:]}")

        # ── A: await-final ────────────────────────────────────────────────────────
        def await_final(path: Path, timeout: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run([PY, str(disp_tool), "await-final", "--final", str(path), "--timeout-seconds",
                                   timeout, "--interval", "1"], cwd=proj, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=60)

        fx = base / "a01.final.md"
        content = "# 최종 응답 · X\n---\n본문 A-01\n둘째 줄 끝"
        fx.write_bytes(content.encode("utf-8"))
        r = await_final(fx, "5")
        case("A01 final 파일이 있으면 종료 0", r.returncode == 0 and "본문 A-01" in r.stdout, r.stdout + r.stderr)
        case("A03 출력은 파일 내용과 정확히 같음(덧붙임 · 잘림 없음)", r.stdout == content, repr(r.stdout[-80:]))
        t0 = time.monotonic()
        r = await_final(base / "never.final.md", "2")
        case("A02 시간 안에 없으면 종료 3 (상한 근처에서 끝남)",
             r.returncode == 3 and time.monotonic() - t0 < 20, f"rc={r.returncode} {r.stderr[:100]}")
    except subprocess.TimeoutExpired as ex:
        case("X99 시간 초과 — 도구가 끝나지 않음", False, str(ex)[:200])
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
