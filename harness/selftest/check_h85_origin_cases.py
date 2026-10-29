"""하네스 자기 검사: 상설 원본 하나로 워커를 띄우는 `stage` · `release` (WO-H85).

계약은 dev/WO-H85.md §계약. 가짜 Orca(repo list · repo add · worktree create · worktree rm)와 임시 저장소만 쓴다.
사용: python harness/selftest/check_h85_origin_cases.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable
DISPATCH = ROOT / "adapters" / "tools" / "orca_dispatch.py"

FAKE_ORCA = r'''
import json, os, sys
from pathlib import Path
st = Path(os.environ["FAKE_ORCA_STATE"])
a = sys.argv[1:]
with (st / "calls.log").open("a", encoding="utf-8") as f:
    f.write(json.dumps(a, ensure_ascii=False) + "\n")
repos_file = st / "repos.json"
repos = json.loads(repos_file.read_text(encoding="utf-8")) if repos_file.exists() else []
if a[:2] == ["repo", "list"]:
    if (st / "listfail").exists():
        print("boom", file=sys.stderr); sys.exit(1)
    if (st / "listbad").exists():
        print("not json"); sys.exit(0)
    print(json.dumps({"ok": True, "result": {"repos": repos}})); sys.exit(0)
if a[:2] == ["repo", "add"]:
    if (st / "addfail").exists():
        print(json.dumps({"ok": False, "error": {"code": "boom"}})); sys.exit(1)
    if (st / "addnoid").exists():
        print(json.dumps({"ok": True, "result": {"repo": {}}})); sys.exit(0)
    path = a[a.index("--path") + 1]
    rid = "repo-%d" % (len(repos) + 1)
    repos.append({"id": rid, "path": path, "displayName": Path(path).name})
    repos_file.write_text(json.dumps(repos), encoding="utf-8")
    print(json.dumps({"ok": True, "result": {"repo": {"id": rid, "path": path}}})); sys.exit(0)
if a[:2] == ["worktree", "create"]:
    ws = (st / "workspace.txt").read_text(encoding="utf-8").strip()
    print(json.dumps({"ok": True, "result": {"worktree": {"path": ws}, "launch": {"outcome": {"handle": "term_stage-0001"}}}}))
    sys.exit(0)
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


def safe_git(cwd: Path, *args: str) -> str:
    """origin 이 아직 없거나 git 이 실패해도 예외 없이 빈 문자열 (Red 가 구현 전에도 사례 단위로 실패하게)."""
    if not cwd.is_dir():
        return ""
    p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.stdout.strip() if p.returncode == 0 else ""


def main() -> int:
    results: list[tuple[str, bool, str]] = []

    def case(name: str, good: bool, detail: str = "") -> None:
        results.append((name, good, detail))

    with tempfile.TemporaryDirectory(prefix="h85-") as tmp:
        base = Path(tmp)
        fake = base / "fake_orca.py"
        fake.write_text(FAKE_ORCA, encoding="utf-8")

        src = base / "src"
        src.mkdir()
        git(src, "init", "-q", "-b", "main")
        (src / "briefs").mkdir()
        (src / "README.md").write_text("# source\n", encoding="utf-8")
        (src / "briefs" / "OLD.md").write_text("old brief\n", encoding="utf-8")
        git(src, "add", "-A")
        git(src, "commit", "-qm", "source")
        ref_sha = git(src, "rev-parse", "HEAD")
        (src / "briefs" / "NEW.md").write_text("new brief (uncommitted)\n", encoding="utf-8")
        (src / "briefs" / "OTHER.md").write_text("other brief\n", encoding="utf-8")

        def new_state(name: str) -> Path:
            st = base / f"state-{name}"
            st.mkdir()
            return st

        def tool(st: Path, *args: str) -> subprocess.CompletedProcess[str]:
            env = dict(os.environ, FAKE_ORCA_STATE=str(st))
            return subprocess.run([PY, str(DISPATCH), *args, "--orca", str(fake)], capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", env=env, timeout=120)

        def stage(st: Path, origin: Path, name: str, *files: str, ref: str | None = None, src_dir: Path = src):
            args = ["stage", "--src", str(src_dir), "--origin", str(origin), "--name", name]
            if ref:
                args += ["--ref", ref]
            for f in files:
                args += ["--file", f]
            return tool(st, *args)

        def calls(st: Path) -> list[list[str]]:
            p = st / "calls.log"
            return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []

        def adds(st: Path) -> list[list[str]]:
            return [c for c in calls(st) if c[:2] == ["repo", "add"]]

        def parse(r: subprocess.CompletedProcess[str]) -> dict:
            try:
                return json.loads(r.stdout.strip().splitlines()[-1])
            except Exception:
                return {}

        origin = base / "dev-origin"
        st = new_state("a")

        # T01 처음 stage: 원본 생성 · 한 번 등록 · JSON
        r = stage(st, origin, "first", "briefs/NEW.md")
        out = parse(r)
        case("T01 첫 stage → 원본 생성, repo add 정확히 1회(경로 = 원본), registered true, JSON 필드",
             r.returncode == 0 and origin.is_dir() and len(adds(st)) == 1
             and adds(st)[0][adds(st)[0].index("--path") + 1] == str(origin)
             and out.get("registered") is True and out.get("repo") == "id:repo-1"
             and out.get("branch") == "stage/first" and len(out.get("base", "")) == 40
             and Path(out.get("origin", "")).resolve() == origin.resolve(),
             r.stdout + r.stderr)

        # T02 파일이 기준 커밋 위 새 커밋으로 올라감
        base_sha = out.get("base", "")
        content = safe_git(origin, "show", f"{base_sha}:briefs/NEW.md") if base_sha else ""
        parent = safe_git(origin, "rev-parse", f"{base_sha}^") if base_sha else ""
        author = safe_git(origin, "log", "-1", "--format=%an <%ae>|%s", base_sha) if base_sha else ""
        case("T02 커밋 안 된 파일이 같은 상대 경로로 올라가고 부모 = 기준 커밋, 작성자 KMS, 메시지 'stage first'",
             content == "new brief (uncommitted)" and parent == ref_sha
             and author == "KMS <noreply@local>|stage first", f"{content!r} {parent} {author}")

        # T03 기준 커밋의 파일은 원본에도 있다(복제 + fetch 한 커밋)
        case("T03 기준 커밋의 기존 파일이 원본 커밋에 그대로 있다",
             bool(base_sha) and safe_git(origin, "show", f"{base_sha}:briefs/OLD.md") == "old brief")

        # T04 두 번째 stage(다른 이름) → 같은 원본 · 이미 등록: repo add 추가 없음, 두 브랜치 모두 남음
        r2 = stage(st, origin, "second", "briefs/OTHER.md")
        out2 = parse(r2)
        branches = safe_git(origin, "branch", "--list", "stage/*")
        case("T04 두 번째 stage → repo add 총 1회 유지, registered false, 같은 repo id, 브랜치 stage/first · stage/second",
             r2.returncode == 0 and len(adds(st)) == 1 and out2.get("registered") is False
             and out2.get("repo") == "id:repo-1" and "stage/first" in branches and "stage/second" in branches
             and out2.get("base") != base_sha, r2.stdout + r2.stderr + branches)

        # T05 두 번째 stage 의 작업 트리는 그 브랜치 내용(첫 stage 파일은 없음)
        case("T05 두 번째 stage 뒤 원본 작업 트리 = stage/second (이전 stage 의 NEW.md 없음)",
             (origin / "briefs" / "OTHER.md").is_file() and not (origin / "briefs" / "NEW.md").exists())

        # T06 같은 이름을 다시 stage 해도 성공(브랜치 재설정)
        r3 = stage(st, origin, "second", "briefs/OTHER.md")
        b3 = parse(r3).get("base", "")
        ahead = safe_git(origin, "rev-list", "--count", f"{ref_sha}..{b3}") if b3 else ""
        case("T06 같은 이름으로 다시 stage → 성공, repo add 여전히 1회, 커밋이 쌓이지 않음(기준 커밋 위 정확히 1개)",
             r3.returncode == 0 and len(adds(st)) == 1 and ahead == "1"
             and safe_git(origin, "rev-parse", f"{b3}^") == ref_sha, r3.stdout + r3.stderr + ahead)

        # T07 파일 없이 stage(기준 커밋 그대로): 커밋 안 함
        r4 = stage(st, origin, "plain")
        case("T07 --file 없음 → 새 커밋 없이 base = 기준 커밋",
             r4.returncode == 0 and parse(r4).get("base") == ref_sha, r4.stdout + r4.stderr)

        # T08 기준 커밋과 같은 내용의 파일 → 커밋 안 함
        r5 = stage(st, origin, "same", "README.md")
        case("T08 내용이 기준 커밋과 같은 파일만 → 새 커밋 없이 base = 기준 커밋",
             r5.returncode == 0 and parse(r5).get("base") == ref_sha, r5.stdout + r5.stderr)

        # T09 --ref 로 특정 커밋 지정
        (src / "later.md").write_text("later\n", encoding="utf-8")
        git(src, "add", "-A")
        git(src, "commit", "-qm", "later")
        r6 = stage(st, origin, "atref", ref=ref_sha)
        case("T09 --ref <커밋> → base 가 그 커밋(이후 커밋은 포함 안 됨)",
             r6.returncode == 0 and parse(r6).get("base") == ref_sha, r6.stdout + r6.stderr)

        # 이하 거부 사례: 원본 · Orca 변화 없음
        def untouched(st2: Path, origin2: Path, before: str | None) -> bool:
            after = safe_git(origin2, "rev-parse", "--abbrev-ref", "HEAD") if origin2.is_dir() else None
            return not calls(st2) and after == before

        st = new_state("rej")
        origin_rej = base / "rej-origin"
        r = stage(st, origin_rej, "ok", "briefs/NEW.md")  # 원본 하나 마련
        case("T09b 거부 사례의 준비 stage 가 성공", r.returncode == 0 and origin_rej.is_dir(), r.stdout + r.stderr)
        head_before = safe_git(origin_rej, "rev-parse", "--abbrev-ref", "HEAD")
        n_calls = len(calls(st))

        def real_reject(r: subprocess.CompletedProcess[str]) -> bool:
            # argparse 도 모르는 서브명령에 종료 2 를 내므로, 도구가 있어야 의미 있는 거부다(H78 B04 와 같은 교훈)
            return r.returncode == 2 and "invalid choice" not in (r.stdout + r.stderr) and bool((r.stdout + r.stderr).strip())

        def rejected(label: str, r: subprocess.CompletedProcess[str]) -> None:
            case(label, real_reject(r) and len(calls(st)) == n_calls
                 and safe_git(origin_rej, "rev-parse", "--abbrev-ref", "HEAD") == head_before, r.stdout + r.stderr)

        rejected("T10 이름 규칙 위반(공백 · 슬래시) → 종료 2, 원본 · Orca 변화 없음",
                 stage(st, origin_rej, "bad name/x", "briefs/NEW.md"))
        def state_of(o: Path) -> tuple[str, str, str, str]:
            return (safe_git(o, "rev-parse", "HEAD"), safe_git(o, "branch", "--list"),
                    safe_git(o, "status", "--porcelain"), safe_git(o, "diff", "--cached", "--name-only"))

        state_before = state_of(origin_rej)
        rejected("T10b 유효한 파일 + 잘못된 파일 함께 → 종료 2, 원본의 HEAD · 브랜치 · 작업 트리 · 인덱스 그대로(부분 반영 없음)",
                 stage(st, origin_rej, "partial", "briefs/OTHER.md", "briefs/NOPE.md"))
        case("T10c 위 거부 뒤 원본 상태 전후 동일", state_before == state_of(origin_rej) and state_before[0] != "",
             str(state_before) + " vs " + str(state_of(origin_rej)))
        rejected("T11 절대 경로 파일 → 종료 2", stage(st, origin_rej, "abs", str(src / "briefs" / "NEW.md")))
        rejected("T12 '..' 조각 파일 → 종료 2", stage(st, origin_rej, "dots", "briefs/../briefs/NEW.md"))
        rejected("T13 없는 파일 → 종료 2", stage(st, origin_rej, "missing", "briefs/NOPE.md"))
        rejected("T14 폴더를 파일로 → 종료 2", stage(st, origin_rej, "dir", "briefs"))
        rejected("T15 해석 안 되는 ref → 종료 2", stage(st, origin_rej, "badref", ref="no-such-ref-xyz"))

        plain = base / "not-git"
        plain.mkdir()
        (plain / "keep.txt").write_text("k", encoding="utf-8")
        st_ng = new_state("ng")
        r = stage(st_ng, plain, "ng")
        case("T16 원본 폴더가 있는데 git 저장소가 아님 → 종료 2, 폴더 그대로, Orca 호출 없음",
             real_reject(r) and not calls(st_ng) and (plain / "keep.txt").read_text() == "k"
             and not (plain / ".git").exists(), r.stdout + r.stderr)

        # T17~T19 Orca 실패
        st_lf = new_state("lf")
        (st_lf / "listfail").touch()
        r = stage(st_lf, base / "lf-origin", "lf")
        case("T17 repo list 실패 → 종료 1, repo add 호출 없음", r.returncode == 1 and not adds(st_lf),
             r.stdout + r.stderr)
        st_lb = new_state("lb")
        (st_lb / "listbad").touch()
        r = stage(st_lb, base / "lb-origin", "lb")
        case("T18 repo list 가 JSON 아님 → 종료 1, repo add 호출 없음", r.returncode == 1 and not adds(st_lb),
             r.stdout + r.stderr)
        st_af = new_state("af")
        (st_af / "addfail").touch()
        r = stage(st_af, base / "af-origin", "af")
        case("T19 repo add 실패 → 종료 1", r.returncode == 1 and len(adds(st_af)) == 1, r.stdout + r.stderr)
        st_ai = new_state("ai")
        (st_ai / "addnoid").touch()
        r = stage(st_ai, base / "ai-origin", "ai")
        case("T20 repo add 결과에 id 없음 → 종료 1", r.returncode == 1, r.stdout + r.stderr)

        # T21 경로 비교: 역슬래시 · 대소문자 · 끝 슬래시가 달라도 같은 저장소
        st_pn = new_state("pn")
        origin_pn = base / "Norm-Origin"
        odd = str(origin_pn).replace("/", "\\").upper() + "\\"
        (st_pn / "repos.json").write_text(json.dumps([{"id": "existing-9", "path": odd}]), encoding="utf-8")
        r = stage(st_pn, origin_pn, "pn")
        case("T21 등록 경로가 역슬래시 · 대소문자 · 끝 슬래시만 다르면 같은 저장소 → repo add 없음, id 재사용",
             r.returncode == 0 and not adds(st_pn) and parse(r).get("repo") == "id:existing-9"
             and parse(r).get("registered") is False, r.stdout + r.stderr)

        # T22 다른 경로 저장소는 같은 것으로 보지 않는다(접두어만 같음)
        st_pf = new_state("pf")
        origin_pf = base / "prefix-origin"
        (st_pf / "repos.json").write_text(json.dumps([{"id": "other-1", "path": str(origin_pf) + "-2"}]),
                                          encoding="utf-8")
        r = stage(st_pf, origin_pf, "pf")
        case("T22 경로가 접두어만 같은 다른 저장소 → 새로 등록(repo add 1회)",
             r.returncode == 0 and len(adds(st_pf)) == 1 and parse(r).get("registered") is True,
             r.stdout + r.stderr)

        # T23 stage 는 원본 밖 · worktree create 를 건드리지 않는다
        st_all = new_state("all")
        r = stage(st_all, base / "all-origin", "all", "briefs/NEW.md")
        kinds = {tuple(c[:2]) for c in calls(st_all)}
        case("T23 stage 가 부르는 Orca 명령은 repo list · repo add 뿐(worktree create 없음)",
             r.returncode == 0 and kinds <= {("repo", "list"), ("repo", "add")}, str(kinds))

        # T24 원본(--src)을 바꾸지 않는다
        src_status = git(src, "status", "--porcelain")
        src_head = git(src, "rev-parse", "HEAD")
        r = stage(new_state("srcsafe"), base / "srcsafe-origin", "ss", "briefs/NEW.md")
        case("T24 stage 뒤 --src 저장소의 HEAD · 작업 트리 상태 그대로",
             r.returncode == 0 and git(src, "rev-parse", "HEAD") == src_head
             and git(src, "status", "--porcelain") == src_status,
             r.stdout + r.stderr)

        # T25 stage 결과가 dispatch 로 이어진다 (워크트리는 같은 repo id, 기준 커밋 = base)
        st_e = new_state("e2e")
        origin_e = base / "e2e-origin"
        out = parse(stage(st_e, origin_e, "e2e", "briefs/NEW.md"))
        ws = base / "ws" / "e2e-origin" / "worker1"
        (ws / "briefs").mkdir(parents=True)
        (ws / "briefs" / "NEW.md").write_text("new brief (uncommitted)\n", encoding="utf-8")
        (st_e / "workspace.txt").write_text(str(ws), encoding="utf-8")
        env = dict(os.environ, FAKE_ORCA_STATE=str(st_e))
        d = subprocess.run([PY, str(DISPATCH), "dispatch", "--origin", out.get("origin", ""), "--repo", out.get("repo", ""),
                            "--name", "worker1", "--base", out.get("base", ""), "--brief", "briefs/NEW.md",
                            "--agent", "agy", "--model", "m-1", "--wo", "WO-X85", "--role", "reader",
                            "--cap-seconds", "30", "--interval", "1", "--orca", str(fake),
                            "--transcript-root", str(base / "empty-root"), "--no-window"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=60)
        wt = [c for c in calls(st_e) if c[:2] == ["worktree", "create"]]
        ok_args = bool(wt) and wt[0][wt[0].index("--repo") + 1] == "id:repo-1" \
            and wt[0][wt[0].index("--base-branch") + 1] == out.get("base")
        case("T25 stage 출력(repo · base)으로 dispatch → 같은 repo 에 worktree create(--base-branch = base), 새 repo add 없음",
             d.returncode == 0 and ok_args and len(adds(st_e)) == 1, d.stdout + d.stderr)

        # R: release
        st_r = new_state("rel")
        ws_ok = base / "orca" / "workspaces" / "dev-origin" / "h85-w1"
        ws_ok.mkdir(parents=True)

        def release(st2: Path, ws2: Path, *extra: str, origin2: Path = base / "dev-origin"):
            return tool(st2, "release", "--workspace", str(ws2), "--origin", str(origin2), *extra)

        r = release(st_r, ws_ok)
        rm = [c for c in calls(st_r) if c[:2] == ["worktree", "rm"]]
        case("R01 release → worktree rm --worktree path:<작업 공간> --json, --force 없음, 종료 0",
             r.returncode == 0 and len(rm) == 1 and f"path:{ws_ok}" in rm[0] and "--force" not in rm[0]
             and "--json" in rm[0], r.stdout + r.stderr)

        st_r2 = new_state("rel2")
        r = release(st_r2, ws_ok, "--discard")
        rm = [c for c in calls(st_r2) if c[:2] == ["worktree", "rm"]]
        case("R02 release --discard → --force 포함", r.returncode == 0 and len(rm) == 1 and "--force" in rm[0],
             r.stdout + r.stderr)

        st_r3 = new_state("rel3")
        stranger = base / "orca" / "workspaces" / "some-project" / "w"
        stranger.mkdir(parents=True)
        r = release(st_r3, stranger)
        case("R03 부모 폴더 이름이 원본 이름과 다른 작업 공간 → 종료 2, Orca 호출 없음",
             real_reject(r) and not calls(st_r3), r.stdout + r.stderr)

        for tag, parent_name in (("b", "dev-origin-evil"), ("c", "evil-dev-origin"), ("d", "dev-origin2")):
            st_x = new_state(f"rel3{tag}")
            wx = base / "orca" / "workspaces" / parent_name / "w"
            wx.mkdir(parents=True)
            r = release(st_x, wx)
            case(f"R03{tag} 부모 폴더 이름이 '{parent_name}'(부분 문자열만 같음) → 종료 2, Orca 호출 없음",
                 real_reject(r) and not calls(st_x), r.stdout + r.stderr)

        st_r4 = new_state("rel4")
        r = release(st_r4, base / "orca" / "workspaces" / "DEV-ORIGIN" / "w2")
        case("R04 부모 폴더 이름 비교는 대소문자 무시 → 통과(종료 0)", r.returncode == 0, r.stdout + r.stderr)

        st_r5 = new_state("rel5")
        (st_r5 / "rmfail").touch()
        r = release(st_r5, ws_ok)
        case("R05 worktree rm 실패 → 종료 1", r.returncode == 1, r.stdout + r.stderr)
        st_r6 = new_state("rel6")
        (st_r6 / "rmnotok").touch()
        r = release(st_r6, ws_ok)
        case("R06 worktree rm 결과 ok 아님 → 종료 1", r.returncode == 1, r.stdout + r.stderr)

        # G: 기존 서브명령 유지
        r = subprocess.run([PY, str(DISPATCH), "brief-check", str(src / "README.md")], capture_output=True,
                           text=True, encoding="utf-8")
        case("G01 기존 brief-check 는 그대로 동작", r.returncode == 0, r.stdout + r.stderr)

    ok = 0
    for name, good, detail in results:
        print(("OK  " if good else "BAD ") + name + (f"  — {detail}"[:300] if not good else ""))
        ok += good
    print(f"\n{ok}/{len(results)} 통과")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
