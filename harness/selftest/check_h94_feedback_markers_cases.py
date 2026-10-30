"""하네스 자기 검사: 피드백 메시지가 실패 표시 줄을 앞에 모은다 (WO-H94).

계약은 dev/WO-H94.md. H93 에서 끝 60줄만 보내 앞쪽의 `BAD E12` 줄이 잘렸고 워커가 다른 사례를 추측해 고쳤다.
- M01 실패 표시 줄(BAD · FAIL · ERROR · Traceback 등)이 출력 앞쪽에 있어도 메시지에 들어간다
- M02 그 줄은 끝 60줄보다 앞(메시지 위쪽 '실패 표시 줄' 묶음)에 온다
- M03 표시 줄이 매우 많아도 메시지 8000자 이하 · 첫 표시 줄은 남는다
- M04 통과한 명령의 출력은 메시지에 넣지 않는다(지금처럼)
- M05 표시 줄이 없으면 묶음 없이 끝 60줄만(지금처럼)
사용: python harness/selftest/check_h94_feedback_markers_cases.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable

FAKE_ORCA = r'''
import json, sys
a = sys.argv[1:]
if a[:2] == ["terminal", "read"]:
    print("> working"); sys.exit(0)
print(json.dumps({"ok": True})); sys.exit(0)
'''


def main() -> int:
    results: list[tuple[str, bool, str]] = []

    def case(name: str, good: bool, detail: str = "") -> None:
        results.append((name, good, detail))

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        base = Path(td)
        proj = base / "proj"
        for top in ("adapters", "harness"):
            shutil.copytree(ROOT / top, proj / top, ignore=shutil.ignore_patterns("__pycache__"))
        (proj / "reports" / "watch").mkdir(parents=True)
        fake = base / "fake.py"
        fake.write_text(FAKE_ORCA, encoding="utf-8")

        def make(name: str, scripts: dict[str, str]) -> Path:
            ws = base / f"ws-{name}"
            (ws / "chk").mkdir(parents=True)
            subprocess.run(["git", "init", "-q", str(ws)], check=True)
            for fn, body in scripts.items():
                (ws / "chk" / fn).write_text(body, encoding="utf-8")
            watch = [PY, str(proj / "adapters" / "tools" / "orca_watch.py"), "--terminal", "term_h94-0001",
                     "--workspace", str(ws), "--agent", "agy", "--wo", f"WO-M{name}", "--role", "implementer",
                     "--cap-seconds", "5", "--interval", "1", "--orca", str(fake),
                     "--log", str(proj / "reports" / "watch" / f"WO-M{name}-term_h94-0.log")]
            state = {"handle": "term_h94-0001", "workspace": str(ws), "watch": watch, "wo": f"WO-M{name}",
                     "agent": "agy", "role": "implementer", "cap_seconds": 5, "no_window": True, "round": 0,
                     "orca": str(fake)}
            sp = proj / "reports" / "watch" / f"WO-M{name}-term_h94-0.dispatch.json"
            sp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            return sp

        def message(sp: Path, *runs: str) -> str:
            a = [PY, str(proj / "adapters" / "tools" / "orca_dispatch.py"), "feedback", "--state", str(sp),
                 "--orca", str(fake)]
            for r in runs:
                a += ["--run", r]
            p = subprocess.run(a, cwd=proj, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               env=dict(os.environ, PYTHONIOENCODING="utf-8"), timeout=120)
            try:
                j = json.loads(p.stdout.strip().splitlines()[-1])
                return Path(j["message"]).read_text(encoding="utf-8")
            except Exception as e:
                return f"<<메시지 없음 rc={p.returncode} {e} {p.stderr[-300:]}>>"

        early_bad = ("print('BAD E12 CRLF 패키지 — EARLY-MARK')\n"
                     "for i in range(200):\n    print(f'OK  case {i:03d}')\n"
                     "print('74/75 통과')\nraise SystemExit(1)\n")
        early_fail = ("print('FAIL: test_x (__main__.C.test_x) — FAIL-MARK')\nprint('AssertionError: 1 != 2')\n"
                      "for i in range(150):\n    print(f'ok {i}')\nraise SystemExit(1)\n")
        tb_early = ("import sys\nprint('Traceback (most recent call last):')\nprint('  File \"x.py\", line 1')\n"
                    "print('ValueError: TB-MARK')\nfor i in range(150):\n    print(f'line {i}')\nraise SystemExit(1)\n")
        many_bad = "for i in range(3000):\n    print(f'BAD case {i:04d} ' + 'x' * 40)\nraise SystemExit(1)\n"
        good = "for i in range(80):\n    print(f'GOOD-OUTPUT {i}')\n"
        plain = "for i in range(100):\n    print(f'plain {i:03d}')\nraise SystemExit(4)\n"

        sp = make("01", {"a.py": early_bad, "b.py": early_fail, "c.py": tb_early})
        m = message(sp, "chk/a.py", "chk/b.py", "chk/c.py")
        case("M01 앞쪽 실패 표시 줄이 메시지에 들어감(BAD · FAIL · Traceback 뒤 오류 줄)",
             "EARLY-MARK" in m and "FAIL-MARK" in m and "TB-MARK" in m, m[:600])
        i_mark = m.find("EARLY-MARK")
        i_tail = m.find("OK  case 199")
        case("M02 표시 줄은 끝 60줄보다 앞에 · '실패 표시 줄' 머리 있음",
             0 <= i_mark < i_tail and "실패 표시 줄" in m[:i_mark], f"mark={i_mark} tail={i_tail}")
        case("M02b 끝 60줄은 지금처럼 남음(마지막 줄)", "74/75 통과" in m, m[-300:])

        sp = make("03", {"many.py": many_bad})
        m = message(sp, "chk/many.py")
        case("M03 표시 줄이 3000개여도 메시지 8000자 이하 · 첫 표시 줄 남음",
             len(m) <= 8000 and "BAD case 0000" in m, f"len={len(m)}")

        sp = make("04", {"good.py": good, "a.py": early_bad})
        m = message(sp, "chk/good.py", "chk/a.py")
        case("M04 통과한 명령의 출력은 메시지에 없음 · 상태 줄만", "GOOD-OUTPUT" not in m and "chk/good.py" in m
             and "통과" in m, m[:400])

        sp = make("05", {"plain.py": plain})
        one_mark = "print('BAD ' + 'y' * 296)\nraise SystemExit(1)\n"
        sp30 = make("06", {f"m{i:02d}.py": one_mark for i in range(30)})
        m30 = message(sp30, *[f"chk/m{i:02d}.py" for i in range(30)])
        every = all(f"chk/m{i:02d}.py → 종료 1" in m30 for i in range(30))
        marks_kept = m30.count("\nBAD yyy") == 30
        case("M06 실패 명령 30개 × 300자 표시 줄: 8000자 이하 · 명령마다 상태 줄과 첫 표시 줄(앞부분) 남음(검토 A05)",
             len(m30) <= 8000 and every and marks_kept and not m30.startswith("<<"),
             f"len={len(m30)} every={every} marks={m30.count(chr(10) + 'BAD yyy')}")
        m = message(sp, "chk/plain.py")
        case("M05 표시 줄이 없으면 묶음 없이 끝 60줄(plain 040 ~ 099)만",
             "실패 표시 줄" not in m and "plain 099" in m and "plain 040" in m and "plain 039" not in m, m[:400])

    bad = [x for x in results if not x[1]]
    for name, ok, detail in results:
        print(f"{'OK ' if ok else 'BAD'} {name}{('  — ' + detail) if detail and not ok else ''}")
    print(f"\n{len(results) - len(bad)}/{len(results)} 통과")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
