"""하네스 자기 검사: selftest 병렬 러너 (WO-H95).

계약은 dev/WO-H95.md §3 · §4. 임시 root 에 가짜 harness/selftest/check_*.py 를 만들어 러너를 돌린다(실제 suite 는 돌리지 않는다).
- P01 통과만 → 0 · P02 실패 하나 → 1 · 끝까지 실행(뒤 파일도 돈다)
- P03 --fail-fast 는 첫 실패 뒤 새 파일을 시작하지 않는다
- P04 타임아웃 → TIMEOUT · 1 · 손자 프로세스가 남지 않는다 · P05 fail-fast 정리도 손자까지
- P06 진행 줄이 파일 끝날 때마다(전체 끝나기 전) 나온다
- P07 LPT: 직전 시간 기록 내림차순 · 모르는 파일 먼저 · P08 --sequential 이름순
- P09 결과 JSON 필드 · summary(`k/n 통과` · unittest) · P10 인자 오류 2 · P11 저장소 변경 경고
사용: python harness/selftest/check_h95_parallel_runner_cases.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "run_suite_parallel.py"
PY = sys.executable

# 시작하면 ORDER_FILE 에 자기 이름을 한 줄 붙인다
HEAD = 'import os, sys, time, subprocess\nopen(os.environ["ORDER_FILE"], "a").write(os.path.basename(__file__)[:-3] + "\\n")\n'
PASS = HEAD + 'print("3/3 통과")\n'
UNIT = HEAD + 'print("Ran 4 tests in 0.1s\\n\\nOK (skipped=1)")\n'
FAIL = HEAD + 'print("BAD x")\nsys.exit(2)\n'
SLOW = HEAD + 'time.sleep(3)\nprint("1/1 통과")\n'
# 잠자는 손자를 띄우고 pid 를 남긴 뒤 자신도 잔다
HANG = HEAD + ('p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])\n'
               'open(os.environ["PID_FILE"], "w").write(str(p.pid))\ntime.sleep(120)\n')
DIRTY = HEAD + 'open("stray.txt", "w").write("x")\n'


def alive(pid: int) -> bool:
    if os.name == "nt":
        r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True, errors="replace")
        return str(pid) in r.stdout
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def make(td: Path, files: dict[str, str], git: bool = False) -> tuple[Path, Path, Path]:
    root = td / "root"
    (root / "harness" / "selftest").mkdir(parents=True)
    for name, body in files.items():
        (root / "harness" / "selftest" / f"{name}.py").write_bytes(body.encode("utf-8"))
    if git:
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A"], cwd=root, check=True)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "i"], cwd=root, check=True)
    return root, td / "logs", td / "order.txt"


def run(root: Path, logs: Path, order: Path, *args: str, pid_file: Path | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ, ORDER_FILE=str(order), PID_FILE=str(pid_file or (root.parent / "pid.txt")))
    return subprocess.run([PY, str(RUNNER), "--root", str(root), "--log-dir", str(logs), "--python", PY, *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=300)


def order_of(order: Path) -> list[str]:
    return order.read_text(encoding="utf-8").split() if order.exists() else []


def main() -> int:
    if not RUNNER.is_file():
        # 공개본 · 소비 프로젝트에는 scripts/ 가 없다(하네스 개발 도구, scripts/export_public.py 가 뺀다)
        print("SKIP scripts/run_suite_parallel.py 없음(하네스 개발 저장소 전용)")
        return 0
    results: list[tuple[str, bool, str]] = []

    def case(name: str, good: bool, detail: str = "") -> None:
        results.append((name, good, detail))

    def td() -> Path:
        return Path(tempfile.mkdtemp(prefix="h95-"))

    r_, l_, o_ = make(td(), {"check_a": PASS, "check_b": UNIT})
    p = run(r_, l_, o_, "--workers", "2")
    case("P01 통과만 → 종료 0 · DONE 2/2", p.returncode == 0 and "DONE 2/2" in p.stdout, p.stdout + p.stderr)

    r_, l_, o_ = make(td(), {"check_a": FAIL, "check_b": PASS, "check_c": PASS})
    p = run(r_, l_, o_, "--sequential")
    case("P02 실패 하나 → 종료 1 · 끝까지(b · c 도 실행) · 실패 꼬리 요약",
         p.returncode == 1 and order_of(o_) == ["check_a", "check_b", "check_c"] and "FAIL(2)" in p.stdout
         and "BAD x" in p.stdout, p.stdout + str(order_of(o_)))

    r_, l_, o_ = make(td(), {"check_a": FAIL, "check_b": PASS, "check_c": PASS})
    p = run(r_, l_, o_, "--sequential", "--fail-fast")
    case("P03 --fail-fast 는 첫 실패 뒤 새 파일을 시작하지 않음 · 종료 1",
         p.returncode == 1 and order_of(o_) == ["check_a"], p.stdout + str(order_of(o_)))

    t = td()
    r_, l_, o_ = make(t, {"check_hang": HANG})
    pid_file = t / "pid.txt"
    p = run(r_, l_, o_, "--timeout", "3", pid_file=pid_file)
    gpid = int(pid_file.read_text()) if pid_file.exists() else 0
    time.sleep(0.5)
    case("P04 타임아웃 → TIMEOUT · 종료 1 · 손자 정리",
         p.returncode == 1 and "TIMEOUT" in p.stdout and gpid > 0 and not alive(gpid),
         f"gpid={gpid} alive={gpid and alive(gpid)}\n{p.stdout}")

    t = td()
    r_, l_, o_ = make(t, {"check_a_hang": HANG, "check_b_fail": HEAD + 'time.sleep(1.5)\nsys.exit(3)\n'})
    pid_file = t / "pid.txt"
    p = run(r_, l_, o_, "--workers", "2", "--fail-fast", pid_file=pid_file)
    gpid = int(pid_file.read_text()) if pid_file.exists() else 0
    time.sleep(0.5)
    case("P05 fail-fast 정리 → 돌던 파일 KILLED · 손자 정리 · 빠르게 종료",
         p.returncode == 1 and "KILLED" in p.stdout and gpid > 0 and not alive(gpid),
         f"gpid={gpid}\n{p.stdout}")

    r_, l_, o_ = make(td(), {"check_fast": PASS, "check_slow": SLOW})
    env = dict(os.environ, ORDER_FILE=str(o_), PID_FILE=str(r_.parent / "pid.txt"))
    proc = subprocess.Popen([PY, str(RUNNER), "--root", str(r_), "--log-dir", str(l_), "--python", PY, "--workers", "2"],
                            stdout=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", env=env)
    first = proc.stdout.readline()
    still = proc.poll() is None
    rest = proc.communicate(timeout=60)[0]
    case("P06 진행 줄이 파일 끝날 때마다(전체 끝나기 전)", "check_fast ... OK" in first and still, first + rest)

    t = td()
    r_, l_, o_ = make(t, {"check_a": PASS, "check_b": PASS, "check_c": PASS, "check_new": PASS})
    l_.mkdir()
    (l_ / "selftest-times.json").write_text(json.dumps({"files": [
        {"name": "check_a", "seconds": 1.0}, {"name": "check_b", "seconds": 9.0}, {"name": "check_c", "seconds": 5.0}]}),
        encoding="utf-8")
    p = run(r_, l_, o_, "--workers", "1")
    case("P07 LPT — 모르는 파일 먼저, 그다음 직전 시간 내림차순",
         order_of(o_) == ["check_new", "check_b", "check_c", "check_a"], str(order_of(o_)))

    r_, l_, o_ = make(td(), {"check_c": PASS, "check_a": PASS, "check_b": PASS})
    p = run(r_, l_, o_, "--sequential")
    case("P08 --sequential 이름순", order_of(o_) == ["check_a", "check_b", "check_c"], str(order_of(o_)))

    r_, l_, o_ = make(td(), {"check_a": PASS, "check_b": UNIT, "check_c": FAIL})
    p = run(r_, l_, o_)
    doc = json.loads((l_ / "selftest-times.json").read_text(encoding="utf-8")) if (l_ / "selftest-times.json").exists() else {}
    by = {f["name"]: f for f in doc.get("files", [])}
    fields_ok = all(set(f) >= {"name", "seconds", "rc", "status", "summary"} for f in by.values())
    case("P09 결과 JSON 필드 · summary 추출",
         {"workers", "total_seconds", "started_at"} <= set(doc) and fields_ok
         and by.get("check_a", {}).get("summary") == "3/3 통과"
         and by.get("check_b", {}).get("summary") == "Ran 4 tests · OK (skipped=1)"
         and by.get("check_c", {}).get("status") == "FAIL" and by.get("check_c", {}).get("rc") == 2,
         json.dumps(doc, ensure_ascii=False)[:600])

    r_, l_, o_ = make(td(), {"check_a": PASS})
    bad = [run(r_, l_, o_, *x).returncode for x in (["--workers", "0"], ["--workers", "x"], ["--only", "check_zz"])]
    case("P10 인자 오류 → 종료 2", bad == [2, 2, 2], str(bad))

    r_, l_, o_ = make(td(), {"check_a": DIRTY}, git=True)
    p = run(r_, l_, o_)
    r2, l2, o2 = make(td(), {"check_a": PASS}, git=True)
    p2 = run(r2, l2, o2)
    case("P11 저장소 변경 경고(종료 코드는 그대로) · 변경 없으면 경고 없음",
         p.returncode == 0 and "경고: 실행 전후" in p.stdout and p2.returncode == 0 and "경고" not in p2.stdout,
         p.stdout + p2.stdout)

    bad = [x for x in results if not x[1]]
    for name, ok, detail in results:
        print(f"{'OK ' if ok else 'BAD'} {name}{('  — ' + detail) if detail and not ok else ''}")
    print(f"\n{len(results) - len(bad)}/{len(results)} 통과")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
