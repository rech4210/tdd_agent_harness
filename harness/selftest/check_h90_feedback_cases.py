"""하네스 자기 검사: 워커 판정 전 피드백 한 회차 (WO-H90).

계약은 dev/WO-H90.md §계약. 도구 사본(adapters · harness)을 임시 루트에서 가짜 Orca 로 돌린다.
- D  dispatch 상태 파일
- F  feedback: 검사 · 실행 · 전송 · 새 감시 · 회차
사용: python harness/selftest/check_h90_feedback_cases.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable
HANDLE = "term_fb000000-0001"

FAKE_ORCA = r'''
import json, os, sys, time
from pathlib import Path
st = Path(os.environ["FAKE_ORCA_STATE"])
a = sys.argv[1:]
with (st / "calls.log").open("a", encoding="utf-8") as f:
    f.write(json.dumps(a, ensure_ascii=False) + "\n")
if a[:2] == ["worktree", "create"]:
    ws = (st / "workspace.txt").read_text(encoding="utf-8").strip()
    print(json.dumps({"ok": True, "result": {"worktree": {"path": ws}, "launch": {"outcome": {"handle": "%s"}}}}))
    sys.exit(0)
if a[:2] == ["terminal", "read"]:
    sys.stdout.write("> working\n"); sys.exit(0)
if a[:2] == ["terminal", "wait"]:
    print(json.dumps({"ok": True})); sys.exit(0)
if a[:2] == ["terminal", "send"]:
    if (st / "sendfail").exists():
        print("send_failed", file=sys.stderr); sys.exit(1)
    if (st / "sendnotok").exists():
        print(json.dumps({"ok": False, "error": {"code": "x"}})); sys.exit(0)
    print(json.dumps({"ok": True, "result": {"accepted": True}})); sys.exit(0)
print("unknown", a, file=sys.stderr); sys.exit(1)
''' % HANDLE


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


def main() -> int:
    results: list[tuple[str, bool, str]] = []

    def case(name: str, good: bool, detail: str = "") -> None:
        results.append((name, good, detail))

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        base = Path(td)
        proj = base / "proj"
        for top in ("adapters", "harness"):
            shutil.copytree(ROOT / top, proj / top, ignore=shutil.ignore_patterns("__pycache__"))
        (proj / "reports").mkdir()
        tool = proj / "adapters" / "tools" / "orca_dispatch.py"
        fake = base / "fake_orca.py"
        fake.write_text(FAKE_ORCA, encoding="utf-8")

        origin = base / "origin"
        origin.mkdir()
        git(origin, "init", "-q", "-b", "main")
        git(origin, "config", "user.email", "t@example.invalid")
        git(origin, "config", "user.name", "t")
        (origin / "briefs").mkdir()
        (origin / "briefs" / "IMPL.md").write_text("# 구현\n", encoding="utf-8")
        git(origin, "add", "-A")
        git(origin, "commit", "-qm", "b")
        base_sha = git(origin, "rev-parse", "HEAD").strip()

        def make_ws(name: str) -> Path:
            ws = base / name
            ws.mkdir()
            git(ws, "init", "-q", "-b", "main")
            git(ws, "config", "user.email", "t@example.invalid")
            git(ws, "config", "user.name", "t")
            (ws / "briefs").mkdir()
            (ws / "briefs" / "IMPL.md").write_text("# 구현\n", encoding="utf-8")
            (ws / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
            (ws / "chk").mkdir()
            (ws / "chk" / "ok.py").write_text("print('fine')\n", encoding="utf-8")
            (ws / "chk" / "bad.py").write_text(
                "import sys\nprint('line-A')\nprint('BAD-TAIL-MARK')\nsys.exit(3)\n", encoding="utf-8")
            (ws / "chk" / "slow.py").write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
            (ws / "chk" / "long.py").write_text(
                "import sys\nfor i in range(5000):\n    print('x' * 40, i)\nprint('LONG-LAST-LINE')\nsys.exit(1)\n",
                encoding="utf-8")
            (ws / "chk" / "touch.py").write_text(
                "from pathlib import Path\nPath('made.txt').write_text('m')\nimport sys\nsys.exit(1)\n", encoding="utf-8")
            (ws / "chk" / "argv.py").write_text(
                "import sys\nprint('ARGS=' + '|'.join(sys.argv[1:]))\nsys.exit(0 if sys.argv[1:] == ['--x', 'a b'] else 4)\n",
                encoding="utf-8")
            (ws / "chk" / "notpy.txt").write_text("x\n", encoding="utf-8")
            git(ws, "add", "-A")
            git(ws, "commit", "-qm", "w")
            return ws

        def env_for(st: Path) -> dict:
            return dict(os.environ, FAKE_ORCA_STATE=str(st), PYTHONIOENCODING="utf-8")

        def run(st: Path, *args: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
            st.mkdir(parents=True, exist_ok=True)
            r = subprocess.run([PY, str(tool), *args], cwd=proj, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", env=env_for(st), timeout=timeout)
            if "invalid choice" in r.stderr:  # 서브명령이 없어 argparse 가 낸 2 는 계약상 거부가 아니다(헛통과 방지)
                r.returncode = 99
            return r

        def calls(st: Path) -> list[list[str]]:
            p = st / "calls.log"
            return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []

        def sends(st: Path) -> list[list[str]]:
            return [c for c in calls(st) if c[:2] == ["terminal", "send"]]

        def opt(c: list[str], n: str) -> str | None:
            return c[c.index(n) + 1] if n in c else None

        def jout(r: subprocess.CompletedProcess[str]) -> dict:
            for line in reversed(r.stdout.strip().splitlines()):
                try:
                    return json.loads(line)
                except Exception:
                    continue
            return {}

        def do_dispatch(st: Path, ws: Path, wo: str) -> dict:
            st.mkdir(parents=True, exist_ok=True)
            (st / "workspace.txt").write_text(str(ws), encoding="utf-8")
            r = run(st, "dispatch", "--origin", str(origin), "--repo", "id:r", "--name", "n", "--base", base_sha,
                    "--brief", "briefs/IMPL.md", "--agent", "agy", "--model", "m-1", "--wo", wo, "--role",
                    "implementer", "--cap-seconds", "30", "--interval", "1", "--orca", str(fake),
                    "--transcript-root", str(base / "empty-root"), "--no-window")
            return {"rc": r.returncode, "out": jout(r), "err": r.stderr}

        # ── D ─────────────────────────────────────────────────────────────
        ws1 = make_ws("ws1")
        d = do_dispatch(base / "s1", ws1, "WO-F1")
        sp = Path(d["out"].get("state", "") or "/nonexistent")
        state = json.loads(sp.read_text(encoding="utf-8")) if sp.is_file() else {}
        case("D01 dispatch 출력에 state 경로 · 상태 파일 = 로그 옆 <로그 이름>.dispatch.json",
             d["rc"] == 0 and sp.is_file() and sp.name.endswith(".dispatch.json")
             and sp.parent == Path(d["out"].get("log", "")).parent, f"rc={d['rc']} out={d['out']} err={d['err'][-200:]}")
        case("D02 상태 파일: 출력 필드 + agent · model · wo · role · cap_seconds · no_window · round 0",
             state.get("handle") == HANDLE and state.get("workspace") and isinstance(state.get("watch"), list)
             and state.get("agent") == "agy" and state.get("model") == "m-1" and state.get("wo") == "WO-F1"
             and state.get("role") == "implementer" and state.get("cap_seconds") == 30
             and state.get("no_window") is True and state.get("round") == 0, str(state)[:300])

        def fresh_state(name: str, ws: Path, wo: str, round_: int | None = None) -> tuple[Path, Path]:
            st = base / name
            dd = do_dispatch(st, ws, wo)
            p = Path(dd["out"].get("state", "") or (base / "missing.json"))
            if round_ is not None and p.is_file():
                s = json.loads(p.read_text(encoding="utf-8"))
                s["round"] = round_
                p.write_text(json.dumps(s, ensure_ascii=False), encoding="utf-8")
            return st, p

        def fb(st: Path, sp_: Path, *runs: str, extra: tuple[str, ...] = ()) -> subprocess.CompletedProcess[str]:
            # WO-H92: 실패 전송은 리드 분류(--send --triage) 뒤에만 — 기존 사례는 분류를 마친 리드 호출로 본다
            a = ["feedback", "--state", str(sp_), "--orca", str(fake), "--send", "--triage", "lead-triaged", *extra]
            for x in runs:
                a += ["--run", x]
            return run(st, *a)

        def state_of(p: Path) -> dict:
            return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}

        # ── F ─────────────────────────────────────────────────────────────
        st, p = fresh_state("f01", make_ws("w01"), "WO-F01")
        r = fb(st, p, "chk/ok.py")
        o = jout(r)
        # dispatch 가 띄운 감시가 계속 terminal read · wait 를 부르므로 그 밖의 호출만 센다(경쟁 회피)
        non_watch = [c for c in calls(st) if c[:2] not in (["terminal", "read"], ["terminal", "wait"])]
        case("F01 모두 통과 → 종료 0 · passed · 전송 · 새 감시 없음 · round 그대로",
             r.returncode == 0 and o.get("passed") is True and o.get("sent") is False and not sends(st)
             and state_of(p).get("round") == 0 and len(non_watch) == 1
             and not list((proj / "reports" / "watch").glob("WO-F01-*.r1.log")),
             f"rc={r.returncode} {o} {r.stderr[-200:]} non_watch={non_watch}")

        st, p = fresh_state("f02", make_ws("w02"), "WO-F02")
        r = fb(st, p, "chk/ok.py", "chk/bad.py")
        o = jout(r)
        s = sends(st)
        text = opt(s[0], "--text") if s else ""
        case("F02 실패 하나 → 전송 1회(--terminal 핸들 · --enter · --json), 종료 0, sent",
             r.returncode == 0 and o.get("sent") is True and o.get("passed") is False and len(s) == 1
             and opt(s[0], "--terminal") == HANDLE and "--enter" in s[0] and "--json" in s[0],
             f"rc={r.returncode} {o} sends={len(s)} {r.stderr[-200:]}")
        case("F03 메시지: 첫 줄 [피드백 1/2] · 명령마다 결과(통과 · 종료 3) · 실패 출력 끝 줄",
             bool(text) and text.splitlines()[0].startswith("[피드백 1/2]") and "chk/ok.py" in text
             and "통과" in text and "chk/bad.py" in text and "종료 3" in text and "BAD-TAIL-MARK" in text,
             (text or "")[:300])
        case("F04 메시지: 사례 · 테스트를 고치지 말고 허용 경로 안에서만, 명령 실행 말 것",
             bool(text) and "고치지" in text and "허용" in text and "실행하지" in text, "")
        stt = state_of(p)
        w1 = stt.get("watch_r1") or []
        fin = o.get("final", "")
        case("F05 새 감시: 상태 round 1 · watch_r1 에 --round 1 · --log …r1.log, final = …r1.final.md",
             stt.get("round") == 1 and opt(w1, "--round") == "1" and str(opt(w1, "--log") or "").endswith(".r1.log")
             and w1.count("--log") == 1 and w1.count("--round") == 1 and fin.endswith(".r1.final.md"),
             f"round={stt.get('round')} w1={w1[-12:]} final={fin}")
        deadline = time.time() + 40
        while fin and not Path(fin).exists() and time.time() < deadline:
            time.sleep(0.5)
        case("F06 새 감시가 실제로 돌아 r1 최종 응답 파일을 남김", bool(fin) and Path(fin).exists(), fin)
        fbtxt = Path(fin.replace(".final.md", ".feedback.txt")) if fin else Path("/nonexistent")
        case("F07 실행 출력 전체를 …r1.feedback.txt 에 남김", fbtxt.is_file()
             and "BAD-TAIL-MARK" in fbtxt.read_text(encoding="utf-8", errors="replace"), str(fbtxt))

        # 회차 상한
        ws8 = make_ws("w08")
        st, p = fresh_state("f08", ws8, "WO-F08", round_=2)
        marker = base / "f08-ran.txt"
        (ws8 / "chk" / "mark.py").write_text(f"open({str(marker)!r}, 'w').write('x')\nraise SystemExit(1)\n",
                                             encoding="utf-8")
        r = fb(st, p, "chk/mark.py")
        case("F08 회차 상한(round 2 → 3 > 2) → 종료 2, 실행 · 전송 없음",
             r.returncode == 2 and not marker.exists() and not sends(st), f"rc={r.returncode} {r.stderr[-200:]}")
        r = fb(st, p, "chk/mark.py", extra=("--max-rounds", "3"))
        case("F08b --max-rounds 3 이면 3회차 진행(전송)", r.returncode == 0 and len(sends(st)) == 1
             and jout(r).get("round") == 3, f"rc={r.returncode} {jout(r)}")
        for cid, mr in (("F09a", "0"), ("F09b", "4")):
            st9, p9 = fresh_state(f"f09{mr}", make_ws(f"w09{mr}"), f"WO-F9{mr}")
            r = fb(st9, p9, "chk/bad.py", extra=("--max-rounds", mr))
            case(f"{cid} --max-rounds {mr}(1 ~ 3 밖) → 종료 2, 전송 없음", r.returncode == 2 and not sends(st9),
                 f"rc={r.returncode}")

        # --run 검사
        ws10 = make_ws("w10")
        st, p = fresh_state("f10", ws10, "WO-F10")
        bads = [str(ws10 / "chk" / "bad.py"), "../w01/chk/bad.py", "chk/notpy.txt", "chk/none.py"]
        for i, b in enumerate(bads):
            r = fb(st, p, "chk/bad.py", b)
            case(f"F10{'abcd'[i]} --run '{Path(b).name if i == 0 else b}' (절대 · .. · .py 아님 · 없음) → 종료 2, 아무것도 안 보냄",
                 r.returncode == 2 and not sends(st), f"rc={r.returncode}")
        r = fb(st, p)
        case("F10e --run 없음 → 종료 2", r.returncode == 2 and not sends(st), f"rc={r.returncode}")
        r = fb(st, p, "chk/argv.py --x 'a b'")
        case("F11 --run 인자는 shlex 로 나눠 전달(따옴표 묶음 유지) → 통과", r.returncode == 0
             and jout(r).get("passed") is True, f"rc={r.returncode} {jout(r)}")

        # 시간 초과 · 긴 출력 · 작업 공간 변경
        st, p = fresh_state("f12", make_ws("w12"), "WO-F12")
        r = fb(st, p, "chk/slow.py", extra=("--timeout-seconds", "2"))
        s = sends(st)
        case("F12 시간 초과 → 실패로 전송, 메시지에 '시간 초과'", r.returncode == 0 and len(s) == 1
             and "시간 초과" in (opt(s[0], "--text") or ""), f"rc={r.returncode} {r.stderr[-200:]}")
        st, p = fresh_state("f13", make_ws("w13"), "WO-F13")
        r = fb(st, p, "chk/long.py")
        s = sends(st)
        t = opt(s[0], "--text") if s else ""
        case("F13 긴 출력: 메시지 8000자 이하 · 마지막 줄 유지", bool(t) and len(t) <= 8000 and "LONG-LAST-LINE" in t,
             f"len={len(t or '')}")
        ws14 = make_ws("w14")
        st, p = fresh_state("f14", ws14, "WO-F14")
        before = git(ws14, "status", "--porcelain")
        r = fb(st, p, "chk/bad.py")
        case("F14 도구는 작업 공간에 쓰지 않음(실행 전후 porcelain 같음) · workspace_changed false",
             git(ws14, "status", "--porcelain") == before and jout(r).get("workspace_changed") is False,
             f"{jout(r)}")
        st, p = fresh_state("f15", make_ws("w15"), "WO-F15")
        r = fb(st, p, "chk/touch.py")
        case("F15 실행한 검사가 파일을 만들면 workspace_changed true(실패로 보지 않음, 전송은 함)",
             r.returncode == 0 and jout(r).get("workspace_changed") is True and len(sends(st)) == 1, f"{jout(r)}")

        # 전송 실패
        for cid, flag in (("F16a", "sendfail"), ("F16b", "sendnotok")):
            st, p = fresh_state(cid.lower(), make_ws(f"w{cid}"), f"WO-{cid}")
            (st / flag).write_text("1", encoding="utf-8")
            r = fb(st, p, "chk/bad.py")
            stt = state_of(p)
            case(f"{cid} 전송 {flag} → 종료 1, round 그대로, 새 감시 없음",
                 r.returncode == 1 and stt.get("round") == 0 and "watch_r1" not in stt
                 and not list((proj / "reports" / "watch").glob(f"WO-{cid}-*.r1.log")), f"rc={r.returncode}")

        # 사전 검토(Codex) 보강
        ws18 = make_ws("w18")
        st, p = fresh_state("f18", ws18, "WO-F18")
        outside = base / "outside_target.py"
        mark18 = base / "f18-ran.txt"
        outside.write_text(f"open({str(mark18)!r}, 'w').write('x')\n", encoding="utf-8")
        try:
            os.symlink(outside, ws18 / "chk" / "link.py")
            os.symlink(base, ws18 / "chk" / "dirlink", target_is_directory=True)
            linked = True
        except OSError:
            linked = False
        if linked:
            for cid, rel in (("F18a", "chk/link.py"), ("F18b", "chk/dirlink/outside_target.py")):
                r = fb(st, p, rel)
                case(f"{cid} 작업 공간 밖을 가리키는 링크 '{rel}' → 종료 2, 실행 · 전송 없음",
                     r.returncode == 2 and not mark18.exists() and not sends(st), f"rc={r.returncode}")
        else:
            print("NOTE F18 심볼릭 링크를 만들 권한이 없어 건너뜀(셈에 넣지 않음)")
        ws19 = make_ws("w19")
        st, p = fresh_state("f19", ws19, "WO-F19")
        mark19 = base / "f19-ran.txt"
        (ws19 / "chk" / "first.py").write_text(f"open({str(mark19)!r}, 'w').write('x')\n", encoding="utf-8")
        r = fb(st, p, "chk/first.py", "chk/none.py")
        case("F19 앞 명령은 유효 · 뒤 명령이 잘못됨 → 종료 2, 앞 명령도 실행 안 됨",
             r.returncode == 2 and not mark19.exists() and not sends(st), f"rc={r.returncode} ran={mark19.exists()}")
        st, p = fresh_state("f20", make_ws("w20"), "WO-F20")
        r = fb(st, p, "chk/ok.py", "chk/bad.py", "chk/slow.py", extra=("--timeout-seconds", "2"))
        fin20 = jout(r).get("final", "")
        ft = Path(fin20.replace(".final.md", ".feedback.txt")) if fin20 else Path("/nonexistent")
        ftxt = ft.read_text(encoding="utf-8", errors="replace") if ft.is_file() else ""
        case("F20 feedback.txt 에 명령 셋 모두(통과 출력 · 실패 출력 전체 · 시간 초과)",
             all(s_ in ftxt for s_ in ("chk/ok.py", "fine", "chk/bad.py", "line-A", "BAD-TAIL-MARK", "chk/slow.py",
                                       "시간 초과")), ftxt[:300])
        st, p = fresh_state("f21", make_ws("w21"), "WO-F21")
        r = fb(st, p, "chk/ok.py")
        passf = list((proj / "reports" / "watch").glob("WO-F21-*.r1.pass.txt"))
        case("F21 통과 회차는 …r1.pass.txt 에 출력 전체", len(passf) == 1
             and "fine" in passf[0].read_text(encoding="utf-8", errors="replace"), str(passf))
        st, p = fresh_state("f22", make_ws("w22"), "WO-F22")
        s22 = state_of(p)
        if s22:
            s22["watch"] = s22["watch"] + ["--log", "OLD-LOG.log", "--round", "9"]
            p.write_text(json.dumps(s22, ensure_ascii=False), encoding="utf-8")
        r = fb(st, p, "chk/bad.py")
        w22 = state_of(p).get("watch_r1") or []
        case("F22 상태 watch 에 기존 --log · --round 가 있어도 새 값 하나씩만",
             w22.count("--log") == 1 and w22.count("--round") == 1 and "OLD-LOG.log" not in w22 and "9" not in w22
             and opt(w22, "--round") == "1", str(w22[-8:]))
        ws23 = make_ws("w23")
        (ws23 / "briefs" / "IMPL.md").unlink()  # 기준 커밋엔 있으나 작업 공간엔 없음 → dispatch 종료 1
        d23 = do_dispatch(base / "f23", ws23, "WO-F23")
        sp23 = Path(d23["out"].get("state", "") or "/nonexistent")
        case("F23 작업 공간에 지시서 없음(종료 1)에도 상태 파일을 씀",
             d23["rc"] == 1 and sp23.is_file() and state_of(sp23).get("brief_present") is False,
             f"rc={d23['rc']} state={sp23}")

        # 상태 파일
        st = base / "f17"
        r = fb(st, base / "nope.dispatch.json", "chk/ok.py")
        case("F17a 상태 파일 없음 → 종료 2", r.returncode == 2, f"rc={r.returncode}")
        badp = base / "bad.dispatch.json"
        badp.write_text("{not json", encoding="utf-8")
        r = fb(st, badp, "chk/ok.py")
        case("F17b 상태 파일 JSON 아님 → 종료 2", r.returncode == 2, f"rc={r.returncode}")
        half = base / "half.dispatch.json"
        half.write_text(json.dumps({"workspace": str(ws1), "round": 0}), encoding="utf-8")
        r = fb(st, half, "chk/ok.py")
        case("F17c 상태 파일에 handle · watch 없음 → 종료 2", r.returncode == 2 and not sends(st), f"rc={r.returncode}")

    bad = [x for x in results if not x[1]]
    for name, good, detail in results:
        print(f"{'OK ' if good else 'BAD'} {name}{('  — ' + detail) if detail and not good else ''}")
    print(f"\n{len(results) - len(bad)}/{len(results)} 통과")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
