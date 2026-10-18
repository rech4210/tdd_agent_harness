"""하네스 자기 검사: HD-28 0a 판정 증거 계약 (WO-H24).

E01 ~ E09: run_gates.py 보고서의 evidence (커밋 결속, E08 하네스 자신의 캐시 · E09 fail-closed 는 1회차 판정 뒤 추가).
V01 ~ V07: harness/evidence.py check (보고서가 그 커밋의 통과 증거인가).
P01 ~ P11: harness/prepush.py (git pre-push 훅: 보호 ref 로 가는 push 전에 게이트를 다시 돌린다).
I01 ~ I03: prepush.py --install 과 실제 git push.
C01: CI 틀. V08 ~ V10 · P12 는 Codex 레드팀 뒤 R3 에서 추가.
고치기 전 Red 28 / 28 (2026-10-15, Windows · Python 3.11.9, core-v0.4.2): evidence · evidence.py · prepush.py · CI 틀 모두 없음.
사용: python harness/selftest/check_v05_evidence_cases.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
PY = sys.executable
ZERO = "0" * 40

PASS_GATE = '[[gate]]\nid = "G2"\nname = "n"\ncmd = ["python", "-c", "raise SystemExit(0)"]\n'
FAIL_GATE = '[[gate]]\nid = "G2"\nname = "n"\ncmd = ["python", "-c", "raise SystemExit(1)"]\n'
ENFORCE = '[enforcement]\nprotected_refs = ["refs/heads/main"]\nrole = "implementer"\n'


# 하위 프로세스는 바이트코드를 쓰게 한다 (실행 환경의 PYTHONDONTWRITEBYTECODE 에 판정이 기대지 않게, N-6)
ENV = {k: v for k, v in os.environ.items() if k != "PYTHONDONTWRITEBYTECODE"}


def sh(cwd: Path, *cmd: str, stdin: str | None = None, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(cmd), cwd=cwd, input=stdin, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=check, env=ENV)


def make_project(base: Path, name: str, gates: str = PASS_GATE, enforce: bool = True) -> Path:
    proj = base / name
    shutil.copytree(ROOT / "harness", proj / "harness", ignore=shutil.ignore_patterns("__pycache__", "selftest", "UPSTREAM"))
    shutil.copytree(ROOT / "adapters", proj / "adapters", ignore=shutil.ignore_patterns("__pycache__"))
    (proj / "config").mkdir(parents=True)
    text = '[harness]\nadapter = "python"\n' + (ENFORCE if enforce else "") + gates
    (proj / "config" / "gates.toml").write_text(text, encoding="utf-8", newline="\n")
    (proj / "reports").mkdir()
    (proj / "reports" / ".keep").write_text("", encoding="utf-8")
    (proj / "src.txt").write_text("n\n", encoding="utf-8", newline="\n")
    for cmd in (["git", "init", "-q", "-b", "main"], ["git", "config", "user.email", "t@example.invalid"],
                ["git", "config", "user.name", "t"], ["git", "add", "-A"], ["git", "commit", "-qm", "base"]):
        sh(proj, *cmd, check=True)
    return proj


def commit(proj: Path, name: str = "src.txt", text: str = "m\n") -> str:
    (proj / name).write_text(text, encoding="utf-8", newline="\n")
    sh(proj, "git", "add", "-A", check=True)
    sh(proj, "git", "commit", "-qm", "n", check=True)
    return head(proj)


def head(proj: Path, rev: str = "HEAD") -> str:
    return sh(proj, "git", "rev-parse", rev).stdout.strip()


def gates(proj: Path, base: str = "HEAD") -> subprocess.CompletedProcess[str]:
    return sh(proj, PY, "harness/run_gates.py", "--role", "implementer", "--base", base)


def report(proj: Path) -> dict:
    p = proj / "reports" / "gate_report.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def check(proj: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return sh(proj, PY, "harness/evidence.py", "check", *args)


def prepush(proj: Path, lines: list[str]) -> subprocess.CompletedProcess[str]:
    return sh(proj, PY, "harness/prepush.py", "origin", "file:///n", stdin="".join(l + "\n" for l in lines))


def e_cases(base: Path, results: list) -> None:
    def add(cid: str, good: bool, detail: str) -> None:
        results.append((cid, good, detail))

    p = make_project(base, "e01")
    first = head(p)
    second = commit(p)
    r = gates(p, "HEAD~1")
    ev = report(p).get("evidence", {})
    add("E01 통과 보고서의 evidence: schema 1 · target_sha = HEAD 전체 · base_sha 전체 · dirty false · exit 0 · ALL PASS",
        r.returncode == 0 and ev.get("schema") == 1 and ev.get("target_sha") == second and ev.get("base_sha") == first
        and ev.get("dirty") is False and ev.get("exit_code") == 0 and ev.get("verdict") == "ALL PASS",
        f"rc={r.returncode} evidence={ev}")
    runs = sorted((p / "reports" / "runs").glob("*.json"))
    run_ev = json.loads(runs[-1].read_text(encoding="utf-8")).get("evidence") if runs else None
    add("E06 실행 사본(reports/runs)에도 같은 evidence", run_ev == ev and bool(ev), f"run={run_ev}")
    add("E07 짧은 ref(HEAD~1)도 전체 커밋 해시로", len(ev.get("base_sha") or "") == 40, f"base_sha={ev.get('base_sha')}")

    p = make_project(base, "e02", gates=FAIL_GATE)
    r = gates(p)
    ev = report(p).get("evidence", {})
    add("E02 실패: exit_code 1 · verdict FAIL · 프로세스 종료 1",
        r.returncode == 1 and ev.get("exit_code") == 1 and ev.get("verdict") == "FAIL", f"rc={r.returncode} evidence={ev}")

    p = make_project(base, "e03")
    (p / "src.txt").write_text("changed\n", encoding="utf-8")
    gates(p)
    a = report(p).get("evidence", {}).get("dirty")
    sh(p, "git", "checkout", "--", "src.txt", check=True)
    (p / "new.txt").write_text("n\n", encoding="utf-8")
    gates(p)
    b = report(p).get("evidence", {}).get("dirty")
    (p / "new.txt").unlink()
    (p / "reports" / "note.md").write_text("n\n", encoding="utf-8")
    gates(p)
    c = report(p).get("evidence", {}).get("dirty")
    add("E03 dirty: 추적 파일 변경 true · 추적 안 된 새 파일 true · reports/ 아래만 바뀜 false",
        a is True and b is True and c is False, f"tracked={a} untracked={b} reports_only={c}")

    p = make_project(base, "e04")
    gates(p)
    before = (p / "reports" / "gate_report.json").read_bytes()
    r = gates(p, "no-such-ref")
    add("E04 기준 커밋을 찾을 수 없으면 종료 2, 보고서 덮어쓰지 않음",
        r.returncode == 2 and (p / "reports" / "gate_report.json").read_bytes() == before, f"rc={r.returncode} {r.stderr.strip()[-120:]}")

    p = make_project(base, "e05")
    gates(p)
    none_v = report(p).get("evidence", {}).get("harness_version", "missing")
    (p / "harness" / "UPSTREAM").write_text("a" * 40 + "\n", encoding="utf-8", newline="\n")
    commit(p, "harness/UPSTREAM", "a" * 40 + "\n")
    gates(p)
    v = report(p).get("evidence", {}).get("harness_version")
    add("E05 harness_version = harness/UPSTREAM 내용, 없으면 null", none_v is None and v == "a" * 40, f"none={none_v!r} with={v!r}")

    # N-6: 하네스 자신의 실행 캐시(harness/ 아래 인터프리터 캐시 폴더)는 프로젝트 .gitignore 에 없어도 dirty 가 아니다.
    # 프로젝트 쪽 캐시(harness/ 밖)는 프로젝트 책임이라 dirty 다.
    p = make_project(base, "e08")
    gates(p)
    gates(p)
    cache_dirs = [d.relative_to(p).as_posix() for d in (p / "harness").rglob("__pycache__")]
    first = report(p).get("evidence", {}).get("dirty")
    (p / "pkg" / "__pycache__").mkdir(parents=True)
    (p / "pkg" / "__pycache__" / "m.pyc").write_bytes(b"0")
    gates(p)
    outside = report(p).get("evidence", {}).get("dirty")
    add("E08 하네스 자신의 캐시만 생긴 트리는 dirty false (.gitignore 없이), harness/ 밖 캐시는 true",
        bool(cache_dirs) and first is False and outside is True, f"cache={cache_dirs[:2]} harness_only={first} outside={outside}")

    # 상태를 알 수 없으면 깨끗하다고 하지 않는다 (fail-closed)
    sys.path.insert(0, str(ROOT / "harness"))
    from evidence import check_dirty  # noqa: E402
    nonrepo = base / "e09-not-a-repo"
    nonrepo.mkdir()
    got = check_dirty(nonrepo)
    add("E09 git status 가 실패하면 dirty true (fail-closed)", got is True, f"returned={got!r}")


def v_cases(base: Path, results: list) -> None:
    def add(cid: str, good: bool, detail: str) -> None:
        results.append((cid, good, detail))

    p = make_project(base, "v01")
    gates(p)
    sha = head(p)
    r = check(p, "--sha", sha)
    add("V01 깨끗한 통과 보고서 + 같은 커밋 → 0", r.returncode == 0, f"rc={r.returncode} {r.stdout.strip()[-120:]}")
    r = check(p, "--sha", "HEAD")
    add("V07 --sha 는 ref 도 받는다 (rev-parse 로 전체 해시)", r.returncode == 0, f"rc={r.returncode}")
    old = head(p)
    commit(p)
    r = check(p, "--sha", "HEAD")
    add("V02 보고서가 다른 커밋의 것 → 1", r.returncode == 1, f"rc={r.returncode} report_target={report(p).get('evidence', {}).get('target_sha')} head={head(p)} old={old}")

    p = make_project(base, "v03")
    (p / "src.txt").write_text("changed\n", encoding="utf-8")
    gates(p)
    r = check(p, "--sha", "HEAD")
    add("V03 dirty 보고서 → 1", r.returncode == 1, f"rc={r.returncode}")

    p = make_project(base, "v04", gates=FAIL_GATE)
    gates(p)
    r = check(p, "--sha", "HEAD")
    add("V04 실패 보고서 → 1", r.returncode == 1, f"rc={r.returncode}")

    p = make_project(base, "v05")
    (p / "reports" / "gate_report.json").write_text(json.dumps({"passed": True, "gates": []}), encoding="utf-8")
    r = check(p, "--sha", "HEAD")
    add("V05 evidence 없는 옛 보고서 → 1", r.returncode == 1, f"rc={r.returncode}")

    p = make_project(base, "v06")
    r = check(p, "--sha", "HEAD")
    r2 = check(p, "--sha", "no-such-ref")
    add("V06 보고서 없음 → 1, 해석 못 하는 --sha → 2", r.returncode == 1 and r2.returncode == 2, f"rc={r.returncode},{r2.returncode}")

    # R3 (Codex RT07 · RT08 · RT12): 부분 실행 · 빈 실행은 전체 통과 증거가 아니다, 형식은 타입까지
    two = PASS_GATE + '[[gate]]\nid = "G7"\nname = "n"\ncmd = ["python", "-c", "raise SystemExit(1)"]\n'
    p = make_project(base, "v08", gates=two)
    full = gates(p)
    r_part = sh(p, PY, "harness/run_gates.py", "--role", "implementer", "--base", "HEAD", "--only", "G2")
    ev = report(p).get("evidence", {})
    r = check(p, "--sha", "HEAD")
    add("V08 --only 부분 실행: evidence.scope = {'only': ['G2']} · verdict 는 'ALL PASS' 가 아님, evidence.py check → 1",
        full.returncode == 1 and r_part.returncode == 0 and ev.get("scope") == {"only": ["G2"]}
        and ev.get("verdict") != "ALL PASS" and r.returncode == 1, f"full={full.returncode} part={r_part.returncode} evidence={ev} check={r.returncode}")

    p = make_project(base, "v09")
    gates(p)
    full_ev = report(p).get("evidence", {})
    before = (p / "reports" / "gate_report.json").read_bytes()
    r = sh(p, PY, "harness/run_gates.py", "--role", "implementer", "--base", "HEAD", "--only", "G-nope")
    add("V09 전체 실행 evidence.scope = 'full', --only 에 정의 안 된 게이트 → 2, 보고서 덮어쓰지 않음 (빈 실행 금지)",
        full_ev.get("scope") == "full" and r.returncode == 2 and (p / "reports" / "gate_report.json").read_bytes() == before,
        f"scope={full_ev.get('scope')!r} rc={r.returncode}")

    p = make_project(base, "v10")
    gates(p)
    rp = p / "reports" / "gate_report.json"
    good = json.loads(rp.read_text(encoding="utf-8"))
    rcs = []
    for key, val in (("schema", True), ("exit_code", False), ("dirty", 0)):
        bad_doc = json.loads(json.dumps(good))
        bad_doc["evidence"][key] = val
        rp.write_text(json.dumps(bad_doc), encoding="utf-8")
        rcs.append(check(p, "--sha", "HEAD").returncode)
    add("V10 타입이 다른 값 (schema true · exit_code false · dirty 0) → 모두 1", rcs == [1, 1, 1], f"rcs={rcs}")


def p_cases(base: Path, results: list) -> None:
    def add(cid: str, good: bool, detail: str) -> None:
        results.append((cid, good, detail))

    def line(proj: Path, local_sha: str, remote_sha: str, ref: str = "refs/heads/main") -> str:
        return f"{ref} {local_sha} {ref} {remote_sha}"

    p = make_project(base, "p01", gates=FAIL_GATE, enforce=False)
    old = head(p)
    new = commit(p)
    r = prepush(p, [line(p, new, old)])
    add("P01 [enforcement] 없으면 허용 (0), 게이트 안 돌림", r.returncode == 0 and not (p / "reports" / "gate_report.json").exists(),
        f"rc={r.returncode} {r.stdout.strip()[-120:]}")

    p = make_project(base, "p02")
    old = head(p)
    new = commit(p)
    r = prepush(p, [line(p, new, old)])
    ev = report(p).get("evidence", {})
    add("P02 보호 ref · 깨끗 · HEAD = 보낼 커밋 · 게이트 통과 → 0, 그 커밋의 evidence, base = 원격 커밋",
        r.returncode == 0 and ev.get("target_sha") == new and ev.get("base_sha") == old, f"rc={r.returncode} evidence={ev}")

    p = make_project(base, "p03", gates=FAIL_GATE)
    old = head(p)
    new = commit(p)
    r = prepush(p, [line(p, new, old)])
    add("P03 게이트 실패 → 1", r.returncode == 1 and report(p).get("evidence", {}).get("target_sha") == new, f"rc={r.returncode}")

    p = make_project(base, "p04")
    old = head(p)
    new = commit(p)
    sh(p, "git", "checkout", "-q", old, check=True)
    r = prepush(p, [line(p, new, old)])
    add("P04 보낼 커밋 ≠ HEAD → 1, 게이트 안 돌림", r.returncode == 1 and not (p / "reports" / "gate_report.json").exists(), f"rc={r.returncode}")

    p = make_project(base, "p05")
    old = head(p)
    new = commit(p)
    (p / "src.txt").write_text("dirty\n", encoding="utf-8")
    r = prepush(p, [line(p, new, old)])
    add("P05 작업 트리가 깨끗하지 않음 → 1, 게이트 안 돌림", r.returncode == 1 and not (p / "reports" / "gate_report.json").exists(), f"rc={r.returncode}")

    p = make_project(base, "p06", gates=FAIL_GATE)
    old = head(p)
    new = commit(p)
    r = prepush(p, [line(p, new, old, "refs/heads/feature")])
    add("P06 보호 안 된 ref 는 허용 (0), 게이트 안 돌림", r.returncode == 0 and not (p / "reports" / "gate_report.json").exists(), f"rc={r.returncode}")

    p = make_project(base, "p07")
    old = head(p)
    r = prepush(p, [f"(delete) {ZERO} refs/heads/main {old}"])
    add("P07 보호 ref 삭제 → 1", r.returncode == 1, f"rc={r.returncode}")

    p = make_project(base, "p08")
    new = commit(p)
    r = prepush(p, [line(p, new, "b" * 40)])
    add("P08 원격 커밋이 로컬에 없음 → 1 (먼저 fetch), 게이트 안 돌림",
        r.returncode == 1 and not (p / "reports" / "gate_report.json").exists(), f"rc={r.returncode} {r.stdout.strip()[-120:]}")

    p = make_project(base, "p09", gates=FAIL_GATE)
    old = head(p)
    new = commit(p)
    r = prepush(p, [line(p, new, old, "refs/heads/feature"), line(p, new, old)])
    add("P09 여러 줄: 보호 안 된 줄 + 실패하는 보호 줄 → 1", r.returncode == 1, f"rc={r.returncode}")

    p = make_project(base, "p10")
    new = head(p)
    r = prepush(p, [line(p, new, ZERO)])
    ev = report(p).get("evidence", {})
    add("P10 보호 ref 새로 만들기 (원격 0) → base = 보낼 커밋 자신, 게이트 결과대로 (통과 0)",
        r.returncode == 0 and ev.get("target_sha") == new and ev.get("base_sha") == new, f"rc={r.returncode} evidence={ev}")

    p = make_project(base, "p11")
    toml = p / "config" / "gates.toml"
    toml.write_text(toml.read_text(encoding="utf-8").replace('role = "implementer"\n', ""), encoding="utf-8", newline="\n")
    sh(p, "git", "commit", "-qam", "n", check=True)
    old = head(p, "HEAD~1")
    new = head(p)
    r = prepush(p, [line(p, new, old)])
    add("P11 [enforcement] 에 role 이 없으면 1 과 이유 (기본 역할로 조용히 돌리지 않음), 게이트 안 돌림",
        r.returncode == 1 and "role" in (r.stdout + r.stderr) and not (p / "reports" / "gate_report.json").exists(),
        f"rc={r.returncode} out={(r.stdout + r.stderr).strip()[-120:]}")

    # R3 (Codex RT10): 하위 모듈의 커밋 안 된 변경은 개인 Git 표시 설정과 무관하게 dirty
    lib = make_project(base, "p12-lib", enforce=False)
    p = make_project(base, "p12")
    sh(p, "git", "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(lib), "lib", check=True)
    sh(p, "git", "commit", "-qm", "sub", check=True)
    old = head(p, "HEAD~1")
    new = head(p)
    (p / "lib" / "src.txt").write_text("local\n", encoding="utf-8")
    sh(p, "git", "config", "diff.ignoreSubmodules", "all", check=True)
    r = prepush(p, [line(p, new, old)])
    add("P12 하위 모듈 작업 트리 변경 + diff.ignoreSubmodules=all → dirty 로 거부 (1), 게이트 안 돌림",
        r.returncode == 1 and not (p / "reports" / "gate_report.json").exists(), f"rc={r.returncode} out={(r.stdout + r.stderr).strip()[-120:]}")


def i_cases(base: Path, results: list) -> None:
    def add(cid: str, good: bool, detail: str) -> None:
        results.append((cid, good, detail))

    p = make_project(base, "i01")
    r1 = sh(p, PY, "harness/prepush.py", "--install")
    hook = p / ".git" / "hooks" / "pre-push"
    text1 = hook.read_text(encoding="utf-8") if hook.exists() else ""
    r2 = sh(p, PY, "harness/prepush.py", "--install")
    text2 = hook.read_text(encoding="utf-8") if hook.exists() else ""
    add("I01 --install: .git/hooks/pre-push 가 harness/prepush.py 를 부른다, 다시 설치해도 0 · 같은 내용",
        r1.returncode == 0 and r2.returncode == 0 and "harness/prepush.py" in text1 and text1 == text2,
        f"rc={r1.returncode},{r2.returncode} hook={text1[:120]!r}")

    p = make_project(base, "i02")
    hook = p / ".git" / "hooks" / "pre-push"
    hook.write_text("#!/bin/sh\necho other\n", encoding="utf-8", newline="\n")
    r = sh(p, PY, "harness/prepush.py", "--install")
    add("I02 다른 훅이 이미 있으면 덮어쓰지 않고 1", r.returncode == 1 and hook.read_text(encoding="utf-8") == "#!/bin/sh\necho other\n",
        f"rc={r.returncode}")

    results_push = []
    for key, gate, want_ok in (("fail", FAIL_GATE, False), ("pass", PASS_GATE, True)):
        p = make_project(base, f"i03-{key}", gates=gate)
        remote = base / f"i03-{key}-remote.git"
        sh(base, "git", "init", "-q", "--bare", "-b", "main", str(remote), check=True)
        sh(p, "git", "remote", "add", "origin", str(remote), check=True)
        sh(p, "git", "push", "-q", "--no-verify", "origin", "main", check=True)
        old = head(p)
        new = commit(p)
        sh(p, PY, "harness/prepush.py", "--install", check=False)
        r = sh(p, "git", "push", "-q", "origin", "main")
        remote_head = sh(base, "git", "--git-dir", str(remote), "rev-parse", "main").stdout.strip()
        results_push.append((key, r.returncode, remote_head == (new if want_ok else old)))
    add("I03 실제 git push: 게이트 실패면 거부 (원격 그대로), 통과면 반영",
        results_push[0][1] != 0 and results_push[0][2] and results_push[1][1] == 0 and results_push[1][2], str(results_push))


def c_cases(results: list) -> None:
    ci = ROOT / "templates" / "ci" / "github-harness-gates.yml"
    text = ci.read_text(encoding="utf-8") if ci.exists() else ""
    doc = (ROOT / "docs" / "enforcement.md")
    dtext = doc.read_text(encoding="utf-8") if doc.exists() else ""
    results.append(("C01 CI 틀은 run_gates.py 와 evidence.py check 를 부르고, 문서가 보호 브랜치 필수 체크와 --no-verify 한계를 적는다",
                    "harness/run_gates.py" in text and "harness/evidence.py check" in text
                    and "필수" in dtext and "--no-verify" in dtext, f"ci={ci.exists()} doc={doc.exists()}"))


def main() -> int:
    results: list[tuple[str, bool, str]] = []
    base = Path(tempfile.mkdtemp(prefix="v05-evidence-"))
    try:
        e_cases(base, results)
        v_cases(base, results)
        p_cases(base, results)
        i_cases(base, results)
    finally:
        shutil.rmtree(base, ignore_errors=True)
    c_cases(results)
    for name, good, detail in results:
        print(f"{'OK ' if good else 'BAD'} {name}  — {detail}")
    bad_count = sum(not good for _, good, _ in results)
    print(f"결과: OK {len(results) - bad_count} / BAD {bad_count} / 합계 {len(results)}")
    return 1 if bad_count else 0


if __name__ == "__main__":
    sys.exit(main())
