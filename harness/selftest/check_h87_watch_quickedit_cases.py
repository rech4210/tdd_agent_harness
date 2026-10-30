"""하네스 자기 검사: 감시 창의 QuickEdit 끄기 (WO-H87).

계약은 dev/WO-H87.md §계약. 숨긴 새 콘솔에서 orca_watch.ps1 을 실제로 돌리고, 가짜 Orca 가 호출될 때
그 콘솔의 입력 모드(CONIN$)를 기록한다. Windows 전용 — 다른 OS 에서는 SKIP.
사용: python harness/selftest/check_h87_watch_quickedit_cases.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
H = "term_aaaabbbbcccc-dddd"
QUICK_EDIT = 0x40
EXTENDED = 0x80

# 콘솔 입력 모드 읽기 · QuickEdit 켜기. 콘솔이 없으면 "none".
QE = r'''
import ctypes, sys
from ctypes import wintypes as w
k = ctypes.windll.kernel32
k.CreateFileW.restype = w.HANDLE
h = k.CreateFileW("CONIN$", 0xC0000000, 3, None, 3, 0, None)
m = w.DWORD()
ok = h not in (None, w.HANDLE(-1).value) and k.GetConsoleMode(h, ctypes.byref(m))
if sys.argv[1] in ("on", "on-noecho"):
    if ok:
        v = m.value | 0x40 | 0x80
        if sys.argv[1] == "on-noecho":
            v &= ~0x4
        k.SetConsoleMode(h, v)
    sys.exit(0)
with open(sys.argv[2], "a", encoding="utf-8") as f:
    f.write("%s=%s\n" % (sys.argv[3], m.value if ok else "none"))
'''

# 감시는 자식(가짜 Orca)을 창 없는 자기 콘솔로 띄운다(WO-H91). 그래서 부모(감시 python)의 콘솔에 붙은 뒤
# 그 콘솔의 입력 모드를 직접 읽는다 — 감시 콘솔 = ps1 이 모드를 바꾼 콘솔.
FAKE_ORCA = r'''
import ctypes, json, os, sys
from ctypes import wintypes as w
a = sys.argv[1:]
k = ctypes.windll.kernel32
k.CreateFileW.restype = w.HANDLE
k.FreeConsole()
val = "none"
if k.AttachConsole(os.getppid()):
    h = k.CreateFileW("CONIN$", 0xC0000000, 3, None, 3, 0, None)
    m = w.DWORD()
    if h not in (None, w.HANDLE(-1).value) and k.GetConsoleMode(h, ctypes.byref(m)):
        val = str(m.value)
with open(OUT_PATH, "a", encoding="utf-8") as f:
    f.write("during=%s\n" % val)
if a[:2] == ["terminal", "read"]:
    print("done"); sys.exit(0)
if a[:2] == ["terminal", "wait"]:
    print(json.dumps({"ok": True})); sys.exit(0)
print("unexpected", file=sys.stderr); sys.exit(1)
'''


def ps_quote(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def read_modes(out: Path) -> dict[str, list[str]]:
    res: dict[str, list[str]] = {}
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                res.setdefault(k, []).append(v)
    return res


def hidden_console_run(cmd: list[str], timeout: int = 180) -> int:
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    p = subprocess.run(cmd, creationflags=subprocess.CREATE_NEW_CONSOLE, startupinfo=si, timeout=timeout)
    return p.returncode


def main() -> int:
    if os.name != "nt":
        print("SKIP  Windows 전용 (콘솔 QuickEdit)")
        return 0

    results: list[tuple[str, bool, str]] = []

    def case(name: str, good: bool, detail: str = "") -> None:
        results.append((name, good, detail))
        print(("PASS  " if good else "FAIL  ") + name + (f" — {detail}" if detail and not good else ""))

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        base = Path(td)
        toolroot = base / "tr"
        for top in ("adapters", "harness"):
            shutil.copytree(ROOT / top, toolroot / top, ignore=shutil.ignore_patterns("__pycache__"))
        ps1 = toolroot / "adapters" / "tools" / "orca_watch.ps1"
        ws = base / "ws"
        ws.mkdir()
        subprocess.run(["git", "init", "-q", str(ws)], check=True)
        qe = base / "qe.py"
        qe.write_text(QE, encoding="utf-8")
        fake = base / "fake_orca.py"

        def watch_args(log: Path, bad: bool = False) -> list[str]:
            a = ["--terminal", H, "--workspace", str(ws), "--agent", "agy", "--wo", "WO-H87",
                 "--role", "implementer", "--cap-seconds", "600", "--interval", "1",
                 "--orca", str(fake), "--log", str(log)]
            if bad:  # --role 없음 → 종료 2
                i = a.index("--role")
                del a[i:i + 2]
            return a

        def run_probe(tag: str, setmode: str, stdin_nul: bool = False) -> dict[str, list[str]]:
            """숨긴 새 콘솔에서: 시작 모드 설정 → 정상 감시 → 잘못된 인자 감시, 각 뒤 모드 기록."""
            out = base / f"modes-{tag}.txt"
            fake.write_text(f"QE_PATH = {str(qe)!r}\nOUT_PATH = {str(out)!r}\n" + FAKE_ORCA, encoding="utf-8")
            q = ps_quote
            if stdin_nul:
                # 입력이 NUL 로 돌려진 자식 PowerShell(같은 콘솔). 오류 출력은 파일로.
                def call(args: list[str]) -> str:
                    cmdline = ("powershell -NoProfile -ExecutionPolicy Bypass -File \"" + str(ps1) + "\" "
                               + " ".join('"' + x + '"' for x in args)
                               + f" < NUL > \"{base / f'err-{tag}.txt'}\" 2>&1")
                    return f"& cmd /c {q(cmdline)}"
            else:
                def call(args: list[str]) -> str:
                    return f"& {q(str(ps1))} " + " ".join(q(x) for x in args)
            rec = lambda label: f"& python {q(str(qe))} rec {q(str(out))} {label}"
            lines = [f"& python {q(str(qe))} {setmode}"]
            if stdin_nul:
                # 대조 실행: 자식 PowerShell 호스트 자체가 콘솔 모드(삽입 0x10 등)를 바꾸므로, 같은 방식으로
                # 빈 명령을 한 번 돌린 뒤의 모드를 시작 모드로 삼는다.
                ctl = "powershell -NoProfile -Command exit < NUL > NUL 2>&1"
                lines.append(f"& cmd /c {q(ctl)}")
            lines += [
                rec("before"),
                call(watch_args(base / f"w-{tag}.log")),
                f"Add-Content -LiteralPath {q(str(out))} -Value \"exit=$LASTEXITCODE\"",
                rec("after"),
                call(watch_args(base / f"wb-{tag}.log", bad=True)),
                f"Add-Content -LiteralPath {q(str(out))} -Value \"exit=$LASTEXITCODE\"",
                rec("after"),
            ]
            probe = base / f"probe-{tag}.ps1"
            probe.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
            try:
                hidden_console_run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(probe)])
            except subprocess.TimeoutExpired:
                return {"timeout": ["1"]}
            return read_modes(out)

        # 시작 모드가 다른 두 경우(R4: 고정값 복구 헛통과 방지) — B 는 에코(0x4)를 끈 모드
        for tag, setmode, label in (("a", "on", "A"), ("b", "on-noecho", "B")):
            m = run_probe(tag, setmode)
            before = m.get("before", ["none"])[0]
            during = m.get("during", [])
            after = m.get("after", [])
            exits = m.get("exit", [])
            ready = "timeout" not in m and before.isdigit() and bool(int(before) & QUICK_EDIT)
            want = (int(before) & ~QUICK_EDIT) | EXTENDED if ready else None
            switched = ready and bool(during) and all(d == str(want) for d in during)
            case(f"Q00{label} 숨긴 콘솔 시험 준비(시작 전 QuickEdit 켜짐 · 시간 안에 끝남)", ready,
                 f"before={before} m={m}")
            case(f"Q01{label} 감시 중 QuickEdit 꺼짐 · 0x80 켜짐 · 다른 비트는 시작 모드 그대로(R3)", switched,
                 f"before={before} want={want} during={during}")
            # M2: 복구 사례는 실제로 바꾼 뒤에만 의미가 있다
            case(f"Q03{label} 정상 종료 뒤 시작 모드로 되돌림(전환이 있었을 때)",
                 switched and len(after) >= 1 and after[0] == before, f"before={before} after={after}")
            case(f"Q04{label} 종료 코드 그대로(정상 0)", len(exits) >= 1 and exits[0] == "0", f"exits={exits}")
            case(f"Q05{label} 잘못된 인자 종료 코드 그대로(2)", len(exits) >= 2 and exits[1] == "2", f"exits={exits}")
            case(f"Q06{label} 오류 종료 뒤에도 시작 모드로 되돌림(전환이 있었을 때)",
                 switched and len(after) >= 2 and after[1] == before, f"before={before} after={after}")

        # R5: 표준 입력이 NUL 로 돌려진 실행 — 모드 읽기가 실패할 수 있는 경로. 오류 없이 그대로 동작하고
        # 감시 중 모드는 바꾸지 않았거나 정확히 계약대로, 끝난 뒤 시작 모드.
        m = run_probe("n", "on", stdin_nul=True)
        before = m.get("before", ["none"])[0]
        during = m.get("during", [])
        after = m.get("after", [])
        exits = m.get("exit", [])
        errf = base / "err-n.txt"
        errtxt = errf.read_text(encoding="utf-8", errors="replace") if errf.exists() else ""
        ok_during = before.isdigit() and bool(during) and all(
            d in (before, str((int(before) & ~QUICK_EDIT) | EXTENDED)) for d in during)
        case("Q10 입력 NUL: 감시 실행됨 · 감시 중 모드는 그대로 또는 계약대로", ok_during,
             f"before={before} during={during}")
        case("Q11 입력 NUL: 종료 코드 그대로(0 · 2) · PowerShell 오류 출력 없음",
             exits[:2] == ["0", "2"] and not any(s in errtxt for s in ("Exception", "CategoryInfo",
                                                                       "FullyQualifiedErrorId")),
             f"exits={exits} err={errtxt[-300:]}")
        case("Q12 입력 NUL: 끝난 뒤 시작 모드", len(after) >= 2 and after[0] == before and after[1] == before,
             f"before={before} after={after}")

        # 표준 입출력을 돌려 받은 실행(입력 = 빈 장치, 출력 = 파이프)에서도 오류 없이 그대로 동작
        out2 = base / "modes2.txt"
        log2 = base / "w2.log"
        fake.write_text(f"QE_PATH = {str(qe)!r}\nOUT_PATH = {str(out2)!r}\n" + FAKE_ORCA, encoding="utf-8")
        args2 = watch_args(log2)
        p = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1), *args2],
                           creationflags=subprocess.CREATE_NO_WINDOW, stdin=subprocess.DEVNULL,
                           capture_output=True, timeout=180)
        err = p.stderr.decode("utf-8", "replace") + p.stdout.decode("utf-8", "replace")
        case("Q07 입출력 돌린 실행: 종료 0", p.returncode == 0, f"rc={p.returncode} {err[-300:]}")
        case("Q08 입출력 돌린 실행: PowerShell 오류 출력 없음",
             not any(s in err for s in ("Exception", "CategoryInfo", "FullyQualifiedErrorId")), err[-300:])
        case("Q09 ps1 이 여전히 orca_watch.py 로 인자를 넘김(감시 기록 · 가짜 Orca 호출 생김)",
             log2.exists() and read_modes(out2).get("during") is not None,
             f"log={log2.exists()} modes={read_modes(out2)}")

    bad = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(bad)}/{len(results)} 통과")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
