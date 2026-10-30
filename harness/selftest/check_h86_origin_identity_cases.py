"""하네스 자기 검사: 상설 원본을 프로젝트마다 구분 · release 는 저장소 정체성으로 확인 (WO-H86).

계약은 dev/WO-H86.md §계약. 가짜 Orca(repo list · repo add · worktree show · worktree rm)와 임시 저장소만 쓴다.
사용: python harness/selftest/check_h86_origin_identity_cases.py
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
# 도구 사본을 임시 루트에서 돌린다: 구현 전 기본 원본(<도구 루트>/.jobs/…)이 실제 하네스 저장소를 건드리지 않게.
DISPATCH: Path

FAKE_ORCA = r'''
import json, os, sys
from pathlib import Path
st = Path(os.environ["FAKE_ORCA_STATE"])
a = sys.argv[1:]
with (st / "calls.log").open("a", encoding="utf-8") as f:
    f.write(json.dumps(a, ensure_ascii=False) + "\n")
def load(name, default):
    p = st / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default
repos = load("repos.json", [])
wts = load("worktrees.json", {})
if a[:2] == ["repo", "list"]:
    if (st / "listfail").exists():
        print("boom", file=sys.stderr); sys.exit(1)
    print(json.dumps({"ok": True, "result": {"repos": repos}})); sys.exit(0)
if a[:2] == ["repo", "add"]:
    path = a[a.index("--path") + 1]
    rid = "repo-%d" % (len(repos) + 1)
    repos.append({"id": rid, "path": path, "displayName": Path(path).name})
    (st / "repos.json").write_text(json.dumps(repos), encoding="utf-8")
    print(json.dumps({"ok": True, "result": {"repo": {"id": rid, "path": path}}})); sys.exit(0)
if a[:2] == ["worktree", "show"]:
    if (st / "showfail").exists():
        print("boom", file=sys.stderr); sys.exit(1)
    sel = a[a.index("--worktree") + 1]
    w = wts.get(sel[len("path:"):]) if sel.startswith("path:") else None
    if w is None:
        print(json.dumps({"ok": False, "error": {"code": "selector_not_found"}})); sys.exit(1)
    print(json.dumps({"ok": True, "result": {"worktree": w}})); sys.exit(0)
if a[:2] == ["worktree", "rm"]:
    if (st / "rmfail").exists():
        print("rm_failed", file=sys.stderr); sys.exit(1)
    if (st / "rmnotok").exists():
        print(json.dumps({"ok": False, "error": {"code": "dirty"}})); sys.exit(0)
    print(json.dumps({"ok": True, "result": {}})); sys.exit(0)
print("unknown", a, file=sys.stderr); sys.exit(1)
'''


def git(cwd: Path, *args: str) -> str:
    p = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args], cwd=cwd,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode:
        raise RuntimeError(f"git {args}: {p.stderr}")
    return p.stdout.strip()


def make_src(base: Path, name: str, ignore_jobs: bool = True) -> Path:
    src = base / name
    src.mkdir()
    git(src, "init", "-q", "-b", "main")
    (src / "README.md").write_text(f"# {name}\n", encoding="utf-8")
    if ignore_jobs:
        (src / ".gitignore").write_text("/.jobs/\n", encoding="utf-8")
    git(src, "add", "-A")
    git(src, "commit", "-qm", "init")
    return src


def main() -> int:
    results: list[tuple[str, bool, str]] = []

    def case(name: str, good: bool, detail: str = "") -> None:
        results.append((name, good, detail))

    global DISPATCH
    with tempfile.TemporaryDirectory(prefix="h86-", ignore_cleanup_errors=True) as tmp:
        base = Path(tmp)
        toolroot = base / "toolroot"
        for sub in ("adapters", "harness"):  # orca_dispatch → orca_watch → collect_usage → harness/usage
            shutil.copytree(ROOT / sub, toolroot / sub, ignore=shutil.ignore_patterns("__pycache__"))
        DISPATCH = toolroot / "adapters" / "tools" / "orca_dispatch.py"
        fake = base / "fake_orca.py"
        fake.write_text(FAKE_ORCA, encoding="utf-8")

        def new_state(name: str) -> Path:
            st = base / f"state-{name}"
            st.mkdir()
            return st

        def tool(st: Path, *args: str) -> subprocess.CompletedProcess[str]:
            env = dict(os.environ, FAKE_ORCA_STATE=str(st))
            return subprocess.run([PY, str(DISPATCH), *args, "--orca", str(fake)], capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", env=env, timeout=120)

        def calls(st: Path) -> list[list[str]]:
            p = st / "calls.log"
            return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []

        def of(st: Path, a: str, b: str) -> list[list[str]]:
            return [c for c in calls(st) if c[:2] == [a, b]]

        def parse(r: subprocess.CompletedProcess[str]) -> dict:
            try:
                return json.loads(r.stdout.strip().splitlines()[-1])
            except Exception:
                return {}

        def real_reject(r: subprocess.CompletedProcess[str]) -> bool:
            out = r.stdout + r.stderr
            return r.returncode == 2 and "invalid choice" not in out and "unrecognized arguments" not in out \
                and bool(out.strip())

        # ---------- N: stage 기본 원본 이름 ----------
        st = new_state("n")
        src_a = make_src(base, "proj-a")
        src_b = make_src(base, "proj-b")
        ra = tool(st, "stage", "--src", str(src_a), "--name", "s1")
        rb = tool(st, "stage", "--src", str(src_b), "--name", "s1")
        oa, ob = parse(ra), parse(rb)
        want_a = (src_a / ".jobs" / "proj-a-dev-origin").resolve()
        want_b = (src_b / ".jobs" / "proj-b-dev-origin").resolve()
        case("N01 --origin 없이 stage → 원본 = <src>/.jobs/<src 이름>-dev-origin, 거기에 생성",
             ra.returncode == 0 and Path(oa.get("origin", "")).resolve() == want_a and want_a.is_dir(),
             ra.stdout + ra.stderr)
        adds = of(st, "repo", "add")
        add_paths = [Path(c[c.index("--path") + 1]).resolve() for c in adds]
        case("N02 다른 저장소 둘 → 원본 둘 · 등록 둘, 폴더 이름(Orca 프로젝트 이름)이 서로 다름",
             rb.returncode == 0 and add_paths == [want_a, want_b] and oa.get("repo") != ob.get("repo")
             and want_a.name != want_b.name, str(add_paths))
        case("N03 기본 원본은 src 의 무시 대상이라 src 작업 트리가 깨끗하다",
             ra.returncode == 0 and git(src_a, "status", "--porcelain") == "", git(src_a, "status", "--porcelain"))

        st = new_state("n4")
        src_c = make_src(base, "proj-c", ignore_jobs=False)
        r = tool(st, "stage", "--src", str(src_c), "--name", "s1")
        case("N04 src 가 .jobs 를 무시하지 않음 + 원본이 src 안 → 종료 2, 원본 안 만듦, Orca 호출 없음",
             real_reject(r) and not (src_c / ".jobs").exists() and not calls(st), r.stdout + r.stderr)

        st = new_state("n5")
        outside = base / "outside-origin"
        r = tool(st, "stage", "--src", str(src_c), "--origin", str(outside), "--name", "s1")
        case("N05 --origin 이 src 밖이면 무시 검사 없이 진행(종료 0)",
             r.returncode == 0 and outside.is_dir(), r.stdout + r.stderr)

        st = new_state("n6")
        inside_ok = src_a / ".jobs" / "custom-origin"
        r = tool(st, "stage", "--src", str(src_a), "--origin", str(inside_ok), "--name", "s1")
        case("N06 --origin 을 src 안 무시 경로로 직접 주면 진행(종료 0)",
             r.returncode == 0 and inside_ok.is_dir(), r.stdout + r.stderr)

        st = new_state("n7")
        src_e = make_src(base, "proj-e", ignore_jobs=False)
        (src_e / ".git" / "info").mkdir(parents=True, exist_ok=True)
        (src_e / ".git" / "info" / "exclude").write_text("/.jobs/\n", encoding="utf-8")
        r = tool(st, "stage", "--src", str(src_e), "--name", "s1")
        case("N07 .gitignore 에는 없고 .git/info/exclude 로만 무시 → git 판정을 따라 허용(종료 0, 등록 1회, src 깨끗)",
             r.returncode == 0 and (src_e / ".jobs" / "proj-e-dev-origin").is_dir()
             and len(of(st, "repo", "add")) == 1 and git(src_e, "status", "--porcelain") == "",
             r.stdout + r.stderr)

        # ---------- L: release 정체성 확인 ----------
        origin = base / "rel-origin"
        origin.mkdir()
        ws_own = base / "orca" / "workspaces" / "rel-origin" / "w1"
        ws_other = base / "orca" / "workspaces" / "rel-origin" / "w2"
        ws_renamed = base / "orca" / "workspaces" / "rel-origin-2" / "w3"
        ws_mainflag = base / "orca" / "workspaces" / "rel-origin" / "w4"

        def rel_state(name: str, **flags: bool) -> Path:
            s = new_state(name)
            (s / "repos.json").write_text(json.dumps([
                {"id": "own-1", "path": str(origin), "displayName": "rel-origin"},
                {"id": "other-9", "path": str(base / "elsewhere" / "rel-origin"), "displayName": "rel-origin"},
            ]), encoding="utf-8")
            (s / "worktrees.json").write_text(json.dumps({
                str(ws_own): {"repoId": "own-1", "path": str(ws_own), "isMainWorktree": False},
                str(ws_other): {"repoId": "other-9", "path": str(ws_other), "isMainWorktree": False},
                str(ws_renamed): {"repoId": "own-1", "path": str(ws_renamed), "isMainWorktree": False},
                str(origin): {"repoId": "own-1", "path": str(origin), "isMainWorktree": True},
                str(ws_mainflag): {"repoId": "own-1", "path": str(ws_mainflag), "isMainWorktree": True},
            }), encoding="utf-8")
            for k, v in flags.items():
                if v:
                    (s / k).touch()
            return s

        def release(s: Path, ws: Path, *extra: str):
            return tool(s, "release", "--workspace", str(ws), "--origin", str(origin), *extra)

        s = rel_state("l1")
        r = release(s, ws_own)
        rm = of(s, "worktree", "rm")
        case("L01 원본 저장소 소유 작업 공간 → worktree rm --worktree path:<ws> --json, --force 없음, 종료 0",
             r.returncode == 0 and len(rm) == 1 and f"path:{ws_own}" in rm[0] and "--json" in rm[0]
             and "--force" not in rm[0] and bool(of(s, "worktree", "show")), r.stdout + r.stderr)

        s = rel_state("l2")
        r = release(s, ws_own, "--discard")
        rm = of(s, "worktree", "rm")
        case("L02 --discard → --force 포함", r.returncode == 0 and len(rm) == 1 and "--force" in rm[0],
             r.stdout + r.stderr)

        s = rel_state("l3")
        r = release(s, ws_other)
        case("L03 부모 폴더 이름은 같지만 다른 저장소(repoId 다름) 작업 공간 → 종료 2, rm 없음",
             real_reject(r) and not of(s, "worktree", "rm"), r.stdout + r.stderr)

        s = rel_state("l4")
        (s / "repos.json").write_text("[]", encoding="utf-8")
        r = release(s, ws_own)
        case("L04 원본이 Orca 에 등록돼 있지 않음 → 종료 2, rm 없음",
             real_reject(r) and not of(s, "worktree", "rm"), r.stdout + r.stderr)

        s = rel_state("l5")
        r = release(s, base / "orca" / "workspaces" / "rel-origin" / "nope")
        case("L05 Orca 가 모르는 작업 공간(show 실패) → 종료 2, rm 없음",
             real_reject(r) and not of(s, "worktree", "rm"), r.stdout + r.stderr)

        s = rel_state("l6")
        r = release(s, origin)
        case("L06 원본 자신의 체크아웃(isMainWorktree) → 종료 2, rm 없음",
             real_reject(r) and not of(s, "worktree", "rm"), r.stdout + r.stderr)

        s = rel_state("l6b")
        r = release(s, ws_mainflag)
        case("L06b 경로는 원본과 다르지만 Orca 가 isMainWorktree=true 로 알림 → 종료 2, rm 없음(필드 자체를 본다)",
             real_reject(r) and not of(s, "worktree", "rm"), r.stdout + r.stderr)

        s = rel_state("l7")
        r = release(s, ws_renamed)
        case("L07 부모 폴더 이름이 달라도 저장소가 같으면 허용(종료 0, rm 1회) — 이름 비교 안 함",
             r.returncode == 0 and len(of(s, "worktree", "rm")) == 1, r.stdout + r.stderr)

        s = rel_state("l8", rmfail=True)
        r = release(s, ws_own)
        case("L08 worktree rm 실패 → 종료 1", r.returncode == 1, r.stdout + r.stderr)
        s = rel_state("l9", rmnotok=True)
        r = release(s, ws_own)
        case("L09 worktree rm 결과 ok 아님 → 종료 1", r.returncode == 1, r.stdout + r.stderr)

        s = rel_state("l10", listfail=True)
        r = release(s, ws_own)
        case("L10 repo list 실패 → 종료 1, show · rm 없음",
             r.returncode == 1 and not of(s, "worktree", "rm") and not of(s, "worktree", "show"), r.stdout + r.stderr)

        s = rel_state("l11")
        odd = str(origin).replace("/", "\\").upper() + "\\"
        (s / "repos.json").write_text(json.dumps([{"id": "own-1", "path": odd}]), encoding="utf-8")
        r = release(s, ws_own)
        case("L11 등록 경로가 역슬래시 · 대소문자 · 끝 슬래시만 다르면 같은 원본으로 찾음(종료 0)",
             r.returncode == 0 and len(of(s, "worktree", "rm")) == 1, r.stdout + r.stderr)

        s = rel_state("l12")
        src_d = make_src(base, "proj-d")
        r = tool(s, "release", "--src", str(src_d), "--workspace", str(ws_own))
        case("L12 release 의 기본 원본도 <src>/.jobs/<src 이름>-dev-origin → 그 원본은 미등록이라 종료 2, rm 없음",
             real_reject(r) and not of(s, "worktree", "rm"), r.stdout + r.stderr)

    ok = 0
    for name, good, detail in results:
        print(("OK  " if good else "BAD ") + name + (f"  — {detail}"[:300] if not good else ""))
        ok += good
    print(f"\n{ok}/{len(results)} 통과")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
