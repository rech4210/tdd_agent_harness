"""하네스 자기 검사: 읽기 전용 점검 감싸개의 내용 기준 변경 감지 (WO-H55, 독립 일감 F7, 위험도 high).

배경: codex_audit.py 는 실행 전후 `git status` 출력(무시 · 하위 모듈 포함)을 비교한다. 이미 있던 무시 파일의 **내용**이
바뀌거나 이미 있던 무시 폴더 **안에** 새 파일이 생기면 출력(`!! build/`)이 같아 감지하지 못한다(JUDGE-wo-h48 남은 관찰).

계약 (adapters/tools/codex_audit.py — 기존 계약 check_doc_roles_cases.py 는 그대로)
- 실행 전후에 작업 사본의 모든 파일(`.git/` 아래 제외, 하위 모듈 작업 트리 포함)의 상대 경로 → SHA-256 표를 만든다.
  표가 다르면(내용 변경 · 새 파일 · 삭제) 종료 1 + “작업 사본 변경” + 달라진 경로. 내용이 같은 파일의 시각만 바뀐 것은 변경이 아니다.
- 표를 만들다 파일을 읽지 못하면(권한 등) 그 경로를 알리고 종료 1(실행 전이면 2).
- `.git/` 내부 변경은 기존 실행 뒤 git 상태 확인(B08)이 맡는다.

사용: python harness/selftest/check_audit_hash_cases.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_kpi_cases import PY, make_project  # noqa: E402

FAKE = r'''import os, sys, time, pathlib
a = sys.argv[1:]
out = a[a.index("-o") + 1]; ws = pathlib.Path(a[a.index("-C") + 1])
mode = os.environ.get("FAKE_MODE", "ok")
if mode == "editignored":
    (ws / "build" / "out.txt").write_text("changed", encoding="utf-8")
if mode == "newinignoreddir":
    (ws / "build" / "new.txt").write_text("x", encoding="utf-8")
if mode == "touchonly":
    st = (ws / "README.md").stat()
    os.utime(ws / "README.md", (st.st_atime + 100, st.st_mtime + 100))
if mode == "rmcached":
    import subprocess
    subprocess.run(["git", "-C", str(ws), "rm", "-q", "--cached", "README.md"], check=True)
if mode == "chmodindex":
    import subprocess
    subprocess.run(["git", "-C", str(ws), "update-index", "--chmod=+x", "README.md"], check=True)
if mode == "editignoredsame":
    (ws / "build" / "out.txt").write_text("old", encoding="utf-8")
pathlib.Path(out).write_text("| 결과 |\n", encoding="utf-8")
'''


def git(p: Path, *a: str) -> None:
    subprocess.run(["git", *a], cwd=p, capture_output=True, text=True, check=True)


def main() -> int:
    rows: list[tuple[str, bool, str]] = []

    def case(name: str, good: bool, detail: str = "") -> None:
        rows.append((name, bool(good), detail[:300]))

    base = Path(tempfile.mkdtemp(prefix="audithash-"))
    try:
        p = make_project(base, "p")
        fake = base / "fake.py"
        fake.write_text(FAKE, encoding="utf-8")
        prompt = base / "prompt.md"
        prompt.write_text("점검", encoding="utf-8")
        outd = base / "out"
        outd.mkdir()

        def ws(name: str) -> Path:
            w = base / name
            w.mkdir()
            for c in (["init", "-q", "-b", "main"], ["config", "user.email", "t@example.invalid"], ["config", "user.name", "t"]):
                git(w, *c)
            (w / "README.md").write_text("x\n", encoding="utf-8")
            (w / ".gitignore").write_text("build/\n", encoding="utf-8")
            git(w, "add", "-A")
            git(w, "commit", "-qm", "i")
            (w / "build").mkdir()
            (w / "build" / "out.txt").write_text("old", encoding="utf-8")
            return w

        def audit(w: Path, out: str, mode: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run([PY, "adapters/tools/codex_audit.py", "--workspace", str(w), "--prompt-file", str(prompt),
                                   "--codex-bin", str(fake), "--out", str(outd / out)], cwd=p, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", env={**os.environ, "FAKE_MODE": mode}, timeout=60)

        r = audit(ws("w1"), "1.md", "editignored")
        case("H01 이미 있던 무시 파일의 내용이 바뀜 → 종료 1 · '작업 사본 변경' · 경로 build/out.txt",
             r.returncode == 1 and "작업 사본 변경" in r.stdout + r.stderr and "build/out.txt" in (r.stdout + r.stderr).replace("\\", "/"),
             f"{r.returncode} {(r.stdout + r.stderr)[-160:]}")
        r = audit(ws("w2"), "2.md", "newinignoreddir")
        case("H02 이미 있던 무시 폴더 안에 새 파일 → 종료 1 · 경로 build/new.txt",
             r.returncode == 1 and "build/new.txt" in (r.stdout + r.stderr).replace("\\", "/"), f"{r.returncode}")
        r = audit(ws("w3"), "3.md", "touchonly")
        case("H03 내용은 같고 시각만 바뀜 → 종료 0 (오탐 없음)", r.returncode == 0, f"{r.returncode} {(r.stdout + r.stderr)[-160:]}")
        r = audit(ws("w4"), "4.md", "editignoredsame")
        case("H04 같은 내용으로 다시 씀 → 종료 0", r.returncode == 0, f"{r.returncode}")
        # H06 · H07 (Sol 검토 지적 C17 · C18, 리드 보정): 내용은 같아도 git 인덱스가 바뀌면 변경 — 상태 비교를 유지해야 한다
        r6 = audit(ws("w6"), "6.md", "rmcached")
        r7 = audit(ws("w7"), "7.md", "chmodindex")
        case("H06 · H07 git rm --cached · 인덱스 실행 비트 변경 → 종료 1 · '작업 사본 변경' (git 상태 비교 유지)",
             r6.returncode == 1 and r7.returncode == 1 and "작업 사본 변경" in r6.stdout + r6.stderr,
             f"{r6.returncode} {r7.returncode}")
        r = audit(ws("w5"), "5.md", "ok")
        case("H05 아무것도 안 바뀜 → 종료 0 (회귀 방지)", r.returncode == 0, f"{r.returncode} {(r.stdout + r.stderr)[-160:]}")
    finally:
        shutil.rmtree(base, ignore_errors=True)
    ok = all(g for _, g, _ in rows)
    for n, gd, d in rows:
        print(f"{'OK ' if gd else 'BAD'} {n}{('  — ' + d) if d and not gd else ''}")
    print("결과: 모든 사례가 기대와 일치" if ok else "결과: 기대와 다른 사례가 있다")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
