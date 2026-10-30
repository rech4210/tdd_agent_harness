"""하네스 selftest 전체를 파일마다 독립 프로세스로 병렬 실행한다 (WO-H95 · WO-H96).

계약은 dev/WO-H95.md §3. 순차 러너(scripts/run_suite_progress.sh)와 같은 결과를 더 짧게 내는 것이 목적.
- 파일마다 자식 프로세스 [python, 파일] (같은 인터프리터 안 import 금지 — 환경 변수를 바꾸는 사례가 있다)
- 직전 실행 시간(<log-dir>/selftest-times.json) → 씨앗(harness/selftest_weights.json) 순 가중치로 무거운 것부터(LPT),
  모르는 새 파일은 가장 먼저
- 기본은 끝까지 돌려 모든 실패를 보고(종료 1), --fail-fast 면 첫 실패 뒤 새 파일을 시작하지 않는다
- 타임아웃 · Ctrl+C · fail-fast 정리는 프로세스 트리째(Windows Job Object, 그 밖 프로세스 그룹)
사용: python harness/selftest_runner.py [--workers N] [--sequential] [--fail-fast] [--timeout S]
      [--log-dir DIR] [--root DIR] [--python EXE] [--only NAME ...]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SEED = HERE / "selftest_weights.json"
TIMES = "selftest-times.json"
IS_WIN = os.name == "nt"


class _Tree:
    """자식 하나와 그 자손 전체. kill() 은 트리째 끝낸다."""

    def __init__(self, cmd: list[str], cwd: Path, env: dict, out) -> None:
        self.job = None
        if IS_WIN:
            import ctypes
            from ctypes import wintypes

            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.CreateJobObjectW.restype = wintypes.HANDLE
            k32.OpenProcess.restype = wintypes.HANDLE
            self._k32 = k32
            job = k32.CreateJobObjectW(None, None)

            class _Basic(ctypes.Structure):
                _fields_ = [("a", ctypes.c_int64), ("b", ctypes.c_int64), ("LimitFlags", wintypes.DWORD),
                            ("MinimumWorkingSetSize", ctypes.c_size_t), ("MaximumWorkingSetSize", ctypes.c_size_t),
                            ("ActiveProcessLimit", wintypes.DWORD), ("Affinity", ctypes.c_size_t),
                            ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

            class _Ext(ctypes.Structure):
                _fields_ = [("Basic", _Basic), ("Io", ctypes.c_uint64 * 6), ("ProcessMemoryLimit", ctypes.c_size_t),
                            ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                            ("PeakJobMemoryUsed", ctypes.c_size_t)]

            info = _Ext()
            info.Basic.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            k32.SetInformationJobObject(wintypes.HANDLE(job), 9, ctypes.byref(info), ctypes.sizeof(info))
            # 멈춘 채로 만들어 Job 에 넣은 뒤 깨운다 — 손자가 Job 밖에서 태어날 틈을 없앤다
            self.proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=out, stderr=subprocess.STDOUT,
                                         stdin=subprocess.DEVNULL, creationflags=0x4)  # CREATE_SUSPENDED
            h = int(self.proc._handle)  # type: ignore[attr-defined]
            if not k32.AssignProcessToJobObject(wintypes.HANDLE(job), wintypes.HANDLE(h)):
                print(f"경고: Job Object 연결 실패({ctypes.get_last_error()}) — 손자 정리 안 될 수 있음", flush=True)
            ctypes.WinDLL("ntdll").NtResumeProcess(wintypes.HANDLE(h))
            self.job = job
        else:
            self.proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=out, stderr=subprocess.STDOUT,
                                         stdin=subprocess.DEVNULL, start_new_session=True)

    def kill(self) -> None:
        if IS_WIN:
            if self.job:
                from ctypes import wintypes
                self._k32.TerminateJobObject(wintypes.HANDLE(self.job), 1)
        else:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            pass

    def close(self) -> None:
        """자식이 끝난 뒤: 남은 자손을 정리하고 핸들을 닫는다(Job 닫힘 = 남은 자손 종료)."""
        if IS_WIN:
            if self.job:
                from ctypes import wintypes
                self._k32.CloseHandle(wintypes.HANDLE(self.job))
                self.job = None
        else:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass


SUMMARY_RES = [
    re.compile(r"^\s*(\d+/\d+ 통과)\s*$"),
    re.compile(r"^(Ran \d+ tests?)\b"),
    re.compile(r"^(OK(?: \(.*\))?|FAILED \(.*\))\s*$"),
]


def extract_summary(text: str) -> str | None:
    """결과 줄: `k/n 통과`(마지막 것) · unittest `Ran N tests` + `OK`/`FAILED (...)`. 없으면 None."""
    found: dict[int, str] = {}
    for line in text.splitlines():
        for i, rx in enumerate(SUMMARY_RES):
            m = rx.match(line)
            if m:
                found[i] = m.group(1)
    parts = [found[i] for i in sorted(found)]
    return " · ".join(parts) if parts else None


def load_weights(log_dir: Path) -> dict[str, float]:
    w: dict[str, float] = {}
    try:
        w.update(json.loads(SEED.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    try:
        prev = json.loads((log_dir / TIMES).read_text(encoding="utf-8"))
        for f in prev.get("files", []):
            if isinstance(f.get("seconds"), (int, float)):
                w[f["name"]] = float(f["seconds"])
    except (OSError, ValueError, AttributeError):
        pass
    return w


def git_state(root: Path) -> str | None:
    try:
        r = subprocess.run(["git", "status", "--porcelain", "--ignored"], cwd=root, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def tail(path: Path, n: int = 30) -> str:
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:])
    except OSError:
        return "(출력 없음)"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="하네스 selftest 병렬 러너 (WO-H95)")
    ap.add_argument("--workers", type=int, default=min(os.cpu_count() or 1, 8))
    ap.add_argument("--sequential", action="store_true", help="워커 1 · 이름순")
    ap.add_argument("--fail-fast", action="store_true", help="첫 실패 뒤 새 파일을 시작하지 않음")
    ap.add_argument("--timeout", type=float, default=1800.0, help="파일마다 초(기본 1800)")
    ap.add_argument("--log-dir", default=str(Path(tempfile.gettempdir()) / "agent-harness-selftest"))
    ap.add_argument("--root", default=".")
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--only", nargs="+", default=None, help="파일 이름(확장자 없이)으로 좁힘")
    a = ap.parse_args(argv)
    if not 1 <= a.workers <= 32:
        print("오류: --workers 는 1 ~ 32", file=sys.stderr)
        return 2
    if a.timeout <= 0:
        print("오류: --timeout 은 양수", file=sys.stderr)
        return 2
    root = Path(a.root).resolve()
    files = sorted((root / "harness" / "selftest").glob("check_*.py"))
    if a.only:
        names = {f.stem for f in files}
        missing = [n for n in a.only if n not in names]
        if missing:
            print(f"오류: 없는 파일 {missing}", file=sys.stderr)
            return 2
        files = [f for f in files if f.stem in set(a.only)]
    if not files:
        print(f"오류: {root}/harness/selftest/check_*.py 없음", file=sys.stderr)
        return 2
    log_dir = Path(a.log_dir).resolve()
    log_dir.mkdir(parents=True, exist_ok=True)

    workers = 1 if a.sequential else a.workers
    if a.sequential:
        queue = list(files)
    else:
        w = load_weights(log_dir)
        queue = sorted(files, key=lambda f: (-w.get(f.stem, float("inf")), f.stem))
    env = dict(os.environ)
    # 순차 러너와 같은 환경: 더하지 않는다(사례가 dict(**environ, PYTHONIOENCODING=...) 로 직접 넣는다 — 겹치면 TypeError)
    env.pop("PYTHONDONTWRITEBYTECODE", None)

    before = git_state(root)
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    t_start = time.monotonic()
    n = len(files)
    running: dict[str, tuple[_Tree, float, object]] = {}
    results: list[dict] = []
    stop = False

    def finish(name: str, status: str, rc: int | None, secs: float) -> None:
        out = log_dir / f"{name}.out"
        try:
            summary = extract_summary(out.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            summary = None
        results.append({"name": name, "seconds": round(secs, 2), "rc": rc, "status": status, "summary": summary})
        shown = status if status != "FAIL" else f"FAIL({rc})"
        print(f"[{len(results)}/{n}] {name} ... {shown} ({secs:.1f}s)", flush=True)

    def reap(tree: _Tree, fh) -> None:
        tree.close()
        fh.close()

    try:
        while queue or running:
            while queue and len(running) < workers and not stop:
                f = queue.pop(0)
                fh = open(log_dir / f"{f.stem}.out", "wb")
                running[f.stem] = (_Tree([a.python, str(f)], root, env, fh), time.monotonic(), fh)
            if stop and not running:
                break
            time.sleep(0.05)
            for name in list(running):
                tree, t0, fh = running[name]
                rc = tree.proc.poll()
                secs = time.monotonic() - t0
                if rc is None and secs > a.timeout:
                    tree.kill()
                    reap(tree, fh)
                    del running[name]
                    finish(name, "TIMEOUT", tree.proc.returncode, secs)
                elif rc is not None:
                    reap(tree, fh)
                    del running[name]
                    finish(name, "OK" if rc == 0 else "FAIL", rc, secs)
                else:
                    continue
                if a.fail_fast and results[-1]["status"] != "OK" and not stop:
                    stop = True
                    queue.clear()
            if stop and running:
                for name in list(running):
                    tree, t0, fh = running.pop(name)
                    tree.kill()
                    reap(tree, fh)
                    finish(name, "KILLED", tree.proc.returncode, time.monotonic() - t0)
    except KeyboardInterrupt:
        for name in list(running):
            tree, t0, fh = running.pop(name)
            tree.kill()
            reap(tree, fh)
            finish(name, "KILLED", tree.proc.returncode, time.monotonic() - t0)
        print("중단(Ctrl+C) — 돌던 파일을 트리째 정리했다", flush=True)
        stop = True

    total = time.monotonic() - t_start
    ok = sum(r["status"] == "OK" for r in results)
    fail = sum(r["status"] == "FAIL" for r in results)
    tmo = sum(r["status"] == "TIMEOUT" for r in results)
    print(f"DONE {ok}/{n} (FAIL {fail}, TIMEOUT {tmo}) {total:.1f}s", flush=True)
    for r in results:
        if r["status"] in ("FAIL", "TIMEOUT"):
            print(f"\n--- {r['name']} ({r['status']}) 마지막 30줄 ---\n{tail(log_dir / (r['name'] + '.out'))}", flush=True)

    # 다음 실행 가중치: 이번에 안 돌린 파일(--only · fail-fast)은 직전 기록을 남긴다
    merged = {r["name"]: r for r in results if r["status"] in ("OK", "FAIL", "TIMEOUT")}
    try:
        prev = json.loads((log_dir / TIMES).read_text(encoding="utf-8")).get("files", [])
        for p in prev:
            merged.setdefault(p["name"], p)
    except (OSError, ValueError, AttributeError, KeyError, TypeError):
        pass
    doc = {"workers": workers, "total_seconds": round(total, 2), "started_at": started_at,
           "files": sorted(merged.values(), key=lambda r: r["name"])}
    (log_dir / TIMES).write_bytes((json.dumps(doc, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))

    after = git_state(root)
    if before is not None and after is not None and before != after:
        print("경고: 실행 전후 저장소 상태(git status --porcelain --ignored)가 다르다 — 사례가 저장소에 썼을 수 있음",
              flush=True)
    return 0 if ok == n and not stop else 1


if __name__ == "__main__":
    sys.exit(main())
