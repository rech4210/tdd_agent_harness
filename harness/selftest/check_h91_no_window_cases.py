"""하네스 자기 검사: 창 없이 도는 감시 · 위임 도구의 자식 프로세스가 콘솔 창을 띄우지 않음 (WO-H91).

계약은 dev/WO-H91.md. Windows 전용 — 다른 OS 에서는 SKIP.
- N01 분리(DETACHED) 감시 아래 가짜 Orca 자식의 GetConsoleWindow 가 모두 0 (창 없음)
- N02 orca_watch.py · orca_dispatch.py 의 subprocess.run / Popen 호출마다 creationflags 가 있다
- N03 창 모드(새 콘솔)는 그대로 — CREATE_NEW_CONSOLE 분기 유지
사용: python harness/selftest/check_h91_no_window_cases.py
"""

from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "adapters" / "tools"

FAKE_ORCA = r'''
import ctypes, json, sys
a = sys.argv[1:]
with open(OUT_PATH, "a", encoding="utf-8") as f:
    f.write("%s=%d\n" % (a[0] + " " + (a[1] if len(a) > 1 else ""), ctypes.windll.kernel32.GetConsoleWindow() or 0))
if a[:2] == ["terminal", "read"]:
    print("> working"); sys.exit(0)
if a[:2] == ["terminal", "wait"]:
    print(json.dumps({"ok": True})); sys.exit(0)
print(json.dumps({"ok": True})); sys.exit(0)
'''


def subprocess_calls(path: Path) -> list[tuple[int, str, set[str]]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    res = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess" \
                and node.func.attr in ("run", "Popen", "check_output", "call", "check_call"):
            res.append((node.lineno, node.func.attr, {k.arg for k in node.keywords if k.arg}))
    return res


def main() -> int:
    if os.name != "nt":
        print("SKIP  Windows 전용 (콘솔 창)")
        return 0
    results: list[tuple[str, bool, str]] = []

    def case(name: str, good: bool, detail: str = "") -> None:
        results.append((name, good, detail))

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        base = Path(td)
        proj = base / "proj"
        for top in ("adapters", "harness"):
            shutil.copytree(ROOT / top, proj / top, ignore=shutil.ignore_patterns("__pycache__"))
        ws = base / "ws"
        ws.mkdir()
        subprocess.run(["git", "init", "-q", str(ws)], check=True, creationflags=subprocess.CREATE_NO_WINDOW)
        out = base / "hwnd.txt"
        fake = base / "fake_orca.py"
        fake.write_text(f"OUT_PATH = {str(out)!r}\n" + FAKE_ORCA, encoding="utf-8")
        log = base / "w.log"
        watch = [sys.executable, str(proj / "adapters" / "tools" / "orca_watch.py"), "--terminal", "term_nw00-0001",
                 "--workspace", str(ws), "--agent", "agy", "--wo", "WO-H91", "--role", "implementer",
                 "--cap-seconds", "30", "--interval", "1", "--orca", str(fake), "--log", str(log)]
        # dispatch --no-window 와 같은 방식: 분리 프로세스(콘솔 없음)
        p = subprocess.Popen(watch, cwd=proj, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL,
                             creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS)
        try:
            p.wait(timeout=60)
        except subprocess.TimeoutExpired:
            p.kill()
        rows = [x.split("=") for x in out.read_text(encoding="utf-8").splitlines()] if out.exists() else []
        case("N00 분리 감시가 가짜 Orca 를 불렀다(측정 준비)", len(rows) >= 2, str(rows))
        case("N01 분리 감시의 Orca 자식 호출 모두 콘솔 창 없음(GetConsoleWindow = 0)",
             bool(rows) and all(r[-1] == "0" for r in rows), str(rows))

    for name in ("orca_watch.py", "orca_dispatch.py"):
        calls = subprocess_calls(TOOLS / name)
        missing = [f"{name}:{ln} {attr}" for ln, attr, kws in calls if "creationflags" not in kws]
        case(f"N02 {name}: subprocess 호출 {len(calls)}개 모두 creationflags 있음", bool(calls) and not missing,
             "; ".join(missing))
    disp = (TOOLS / "orca_dispatch.py").read_text(encoding="utf-8")
    case("N03 창 모드(CREATE_NEW_CONSOLE) 분기는 그대로", "CREATE_NEW_CONSOLE" in disp, "")

    bad = [x for x in results if not x[1]]
    for name, good, detail in results:
        print(f"{'OK ' if good else 'BAD'} {name}{('  — ' + detail) if detail and not good else ''}")
    print(f"\n{len(results) - len(bad)}/{len(results)} 통과")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
