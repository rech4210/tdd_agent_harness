"""WO-H77 A (HD-79): pre-push 의 선택적 판정 증거 재사용.

[enforcement] reuse_evidence = true 일 때만, 보호 줄마다 reports/gate_report.json 이 그 push 의 통과 증거이면
run_gates.py 를 다시 돌리지 않는다. 증거 조건: evidence.py verify_evidence(보낼 커밋) 통과(schema · target_sha ·
scope full · dirty false · exit 0) + evidence.base_sha == 그 줄의 기준 커밋 + evidence.verdict == "ALL PASS" +
보고서 role == [enforcement] role. 대상 커밋과 깨끗한 트리 결속이 설정 · 하네스 내용을 고정한다.
기본값은 끔(기존 동작). 사전 거부(삭제 · HEAD 불일치 · dirty · fetch)는 재사용보다 먼저다. 값이 bool 이 아니면 거부(1).
로컬 보고서는 위조할 수 있다 — 이 선택은 그 위험을 받아들이고, CI 는 계속 새로 돌린다(docs/enforcement.md).
게이트 실행 여부는 게이트가 reports/marker 에 덧붙이는 글자 수로 센다(reports/ 는 dirty 에서 제외).
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

MARK_GATE = ('[[gate]]\nid = "G2"\nname = "n"\n'
             'cmd = ["python", "-c", "open(\'reports/marker\', \'a\').write(\'x\')"]\n')
ENV = {k: v for k, v in os.environ.items() if k != "PYTHONDONTWRITEBYTECODE"}


def sh(cwd: Path, *cmd: str, stdin: str | None = None, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(cmd), cwd=cwd, input=stdin, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=check, env=ENV)


MARK_FAIL_GATE = ('[[gate]]\nid = "G2"\nname = "n"\n'
                  'cmd = ["python", "-c", "open(\'reports/marker\', \'a\').write(\'x\'); raise SystemExit(1)"]\n')


def make_project(base: Path, name: str, reuse: str | None = "true",
                 refs: str = '["refs/heads/main"]', role: str = "implementer", gate: str = MARK_GATE) -> Path:
    proj = base / name
    shutil.copytree(ROOT / "harness", proj / "harness", ignore=shutil.ignore_patterns("__pycache__", "selftest", "UPSTREAM"))
    shutil.copytree(ROOT / "adapters", proj / "adapters", ignore=shutil.ignore_patterns("__pycache__"))
    (proj / "config").mkdir(parents=True)
    enforce = f'[enforcement]\nprotected_refs = {refs}\nrole = "{role}"\n'
    if reuse is not None:
        enforce += f"reuse_evidence = {reuse}\n"
    (proj / "config" / "gates.toml").write_text('[harness]\nadapter = "python"\n' + enforce + gate,
                                                encoding="utf-8", newline="\n")
    (proj / "reports").mkdir()
    (proj / "reports" / ".keep").write_text("", encoding="utf-8")
    (proj / "src.txt").write_text("n\n", encoding="utf-8", newline="\n")
    for cmd in (["git", "init", "-q", "-b", "main"], ["git", "config", "user.email", "t@example.invalid"],
                ["git", "config", "user.name", "t"], ["git", "add", "-A"], ["git", "commit", "-qm", "base"]):
        sh(proj, *cmd, check=True)
    return proj


def commit(proj: Path, text: str = "m\n") -> str:
    (proj / "src.txt").write_text(text, encoding="utf-8", newline="\n")
    sh(proj, "git", "add", "-A", check=True)
    sh(proj, "git", "commit", "-qm", "n", check=True)
    return head(proj)


def head(proj: Path, rev: str = "HEAD") -> str:
    return sh(proj, "git", "rev-parse", rev).stdout.strip()


def gates(proj: Path, base: str, *extra: str, role: str = "implementer") -> subprocess.CompletedProcess[str]:
    return sh(proj, PY, "harness/run_gates.py", "--role", role, "--base", base, *extra)


def report_bytes(proj: Path) -> bytes:
    return (proj / "reports" / "gate_report.json").read_bytes()


def last_base(proj: Path) -> str:
    """마지막 게이트 실행의 기준 커밋 (보고서가 덮어써졌는지로 어느 줄이 돌았는지 본다)."""
    try:
        return json.loads(report_bytes(proj).decode("utf-8")).get("evidence", {}).get("base_sha", "")
    except Exception:
        return ""


def marks(proj: Path) -> int:
    p = proj / "reports" / "marker"
    return len(p.read_text(encoding="utf-8")) if p.exists() else 0


def edit_report(proj: Path, fn) -> None:
    p = proj / "reports" / "gate_report.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    fn(d)
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")


def prepush(proj: Path, lines: list[str]) -> subprocess.CompletedProcess[str]:
    return sh(proj, PY, "harness/prepush.py", "origin", "file:///n", stdin="".join(l + "\n" for l in lines))


def line(local_sha: str, remote_sha: str, ref: str = "refs/heads/main") -> str:
    return f"{ref} {local_sha} {ref} {remote_sha}"


def ready(base: Path, name: str, **kw) -> tuple[Path, str, str]:
    """기준 커밋 old, 보낼 커밋 new, new 에서 --base old 로 통과한 증거가 있는 프로젝트."""
    p = make_project(base, name, **kw)
    old = head(p)
    new = commit(p)
    r = gates(p, old)
    if r.returncode != 0:
        raise RuntimeError(f"준비 게이트 실패: {r.stdout[-200:]} {r.stderr[-200:]}")
    return p, old, new


def main() -> int:
    results: list[tuple[str, bool, str]] = []

    def case(cid: str, good: bool, detail: str = "") -> None:
        results.append((cid, good, detail))

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)

        p, old, new = ready(base, "a01", reuse=None)
        before = marks(p)
        r = prepush(p, [line(new, old)])
        case("A01 reuse_evidence 없음(기본 끔): 유효 증거가 있어도 게이트를 다시 돌림 · 0",
             r.returncode == 0 and marks(p) == before + 1, f"rc={r.returncode} marks={before}->{marks(p)}")

        p, old, new = ready(base, "a01b", reuse="false")
        before = marks(p)
        r = prepush(p, [line(new, old)])
        case("A01b reuse_evidence = false: 다시 돌림", r.returncode == 0 and marks(p) == before + 1,
             f"rc={r.returncode} marks={before}->{marks(p)}")

        p, old, new = ready(base, "a02")
        before = marks(p)
        r = prepush(p, [line(new, old)])
        out = r.stdout + r.stderr
        case("A02 켬 + 같은 커밋 · 기준 · 역할 · ALL PASS 증거 → 0 · 게이트 안 돌림 · 출력에 '재사용' · 대상 · 기준 SHA(12자 이상)",
             r.returncode == 0 and marks(p) == before and "재사용" in out and new[:12] in out and old[:12] in out,
             f"rc={r.returncode} marks={before}->{marks(p)} out={(r.stdout + r.stderr).strip()[-160:]}")

        p, old, new = ready(base, "a03")
        newer = commit(p, "z\n")
        before = marks(p)
        r = prepush(p, [line(newer, old)])
        case("A03 켬 + 증거가 이전 커밋 것 → 다시 돌림", r.returncode == 0 and marks(p) == before + 1,
             f"rc={r.returncode} marks={before}->{marks(p)}")

        p, old, new = ready(base, "a04")
        edit_report(p, lambda d: d.update(role="test_author"))
        before = marks(p)
        r = prepush(p, [line(new, old)])
        case("A04 켬 + 보고서 role 이 [enforcement] role 과 다름 → 다시 돌림",
             r.returncode == 0 and marks(p) == before + 1, f"rc={r.returncode} marks={before}->{marks(p)}")

        p = make_project(base, "a05")
        old = head(p)
        new = commit(p)
        gates(p, new)
        before = marks(p)
        r = prepush(p, [line(new, old)])
        case("A05 켬 + 증거의 base_sha 가 그 줄의 기준과 다름(원격 기준 변경) → 다시 돌림",
             r.returncode == 0 and marks(p) == before + 1, f"rc={r.returncode} marks={before}->{marks(p)}")

        p = make_project(base, "a06")
        old = head(p)
        new = commit(p)
        gates(p, old, "--only", "G2")
        before = marks(p)
        r = prepush(p, [line(new, old)])
        case("A06 켬 + 일부 게이트(--only) 증거 → 다시 돌림", r.returncode == 0 and marks(p) == before + 1,
             f"rc={r.returncode} marks={before}->{marks(p)}")

        p, old, new = ready(base, "a07")
        edit_report(p, lambda d: d["evidence"].update(exit_code=1, verdict="FAIL"))
        before = marks(p)
        r = prepush(p, [line(new, old)])
        case("A07 켬 + 실패 증거 → 다시 돌림(지금 통과면 0)", r.returncode == 0 and marks(p) == before + 1,
             f"rc={r.returncode} marks={before}->{marks(p)}")

        p, old, new = ready(base, "a08", reuse='"yes"')
        before = marks(p)
        r = prepush(p, [line(new, old)])
        case("A08 reuse_evidence 가 bool 이 아님 → 1 · 이유에 reuse_evidence · 게이트 안 돌림",
             r.returncode == 1 and marks(p) == before and "reuse_evidence" in (r.stdout + r.stderr),
             f"rc={r.returncode} marks={before}->{marks(p)} out={(r.stdout + r.stderr).strip()[-160:]}")

        p = make_project(base, "a09")
        old = head(p)
        new = commit(p)
        r = prepush(p, [line(new, old)])
        case("A09 켬 + 보고서 없음 → 다시 돌림 · 0", r.returncode == 0 and marks(p) == 1, f"rc={r.returncode} marks={marks(p)}")

        p, old, new = ready(base, "a10")
        (p / "reports" / "gate_report.json").write_text("{not json", encoding="utf-8")
        before = marks(p)
        r = prepush(p, [line(new, old)])
        case("A10 켬 + 깨진 보고서 → 오류 없이 다시 돌림 · 0", r.returncode == 0 and marks(p) == before + 1,
             f"rc={r.returncode} marks={before}->{marks(p)} err={r.stderr.strip()[-160:]}")

        p, old, new = ready(base, "a11")
        (p / "src.txt").write_text("dirty\n", encoding="utf-8")
        before = marks(p)
        r = prepush(p, [line(new, old)])
        case("A11 켬 + 유효 증거라도 작업 트리 dirty → 1 · 게이트 안 돌림 (사전 거부가 먼저)",
             r.returncode == 1 and marks(p) == before, f"rc={r.returncode} marks={before}->{marks(p)}")

        p, old, new = ready(base, "a12")
        edit_report(p, lambda d: d["evidence"].update(verdict="SLICE PASS"))
        before = marks(p)
        r = prepush(p, [line(new, old)])
        case("A12 켬 + verdict 가 ALL PASS 아님(SLICE PASS) → 다시 돌림", r.returncode == 0 and marks(p) == before + 1,
             f"rc={r.returncode} marks={before}->{marks(p)}")

        p, old, new = ready(base, "a13")
        edit_report(p, lambda d: d["evidence"].update(dirty=True))
        before = marks(p)
        r = prepush(p, [line(new, old)])
        case("A13 켬 + 증거 생성 때 dirty → 다시 돌림", r.returncode == 0 and marks(p) == before + 1,
             f"rc={r.returncode} marks={before}->{marks(p)}")

        p = make_project(base, "a14", refs='["refs/heads/main", "refs/heads/release"]')
        root = head(p)
        mid = commit(p, "mid\n")
        new = commit(p, "new\n")
        gates(p, mid)
        before = marks(p)
        r = prepush(p, [line(new, mid), line(new, root, "refs/heads/release")])
        case("A14 켬 + 보호 줄 둘: 기준이 맞는 줄(mid)은 재사용 출력, root 기준 줄만 게이트 1회 (검토 필수-07)",
             r.returncode == 0 and marks(p) == before + 1 and last_base(p) == root and mid[:12] in r.stdout + r.stderr,
             f"rc={r.returncode} marks={before}->{marks(p)} base={last_base(p)[:12]}")

        p = make_project(base, "a14b", refs='["refs/heads/main", "refs/heads/release"]')
        root = head(p)
        mid = commit(p, "mid\n")
        new = commit(p, "new\n")
        gates(p, mid)
        before = marks(p)
        r = prepush(p, [line(new, root, "refs/heads/release"), line(new, mid)])
        case("A14b 순서 반대: 재사용 판단은 훅 시작 때의 보고서로 → root 줄만 게이트 1회 · mid 줄 재사용 출력",
             r.returncode == 0 and marks(p) == before + 1 and last_base(p) == root and mid[:12] in r.stdout + r.stderr,
             f"rc={r.returncode} marks={before}->{marks(p)} base={last_base(p)[:12]}")

        # ---- 검토(Codex gpt-6.1-sol) 반영: 필드 하나씩만 바꾼 증거는 재사용하지 않는다 (필수-01~04, 선택-02)
        p, old, new = ready(base, "a20")
        good = report_bytes(p)
        rp = p / "reports" / "gate_report.json"
        mutations = [
            ("scope 만 부분", lambda d: d["evidence"].update(scope={"only": ["G2"]})),
            ("exit_code 만 1", lambda d: d["evidence"].update(exit_code=1)),
            ("schema 누락", lambda d: d["evidence"].pop("schema")),
            ("schema 2", lambda d: d["evidence"].update(schema=2)),
            ("schema true", lambda d: d["evidence"].update(schema=True)),
            ("schema \"1\"", lambda d: d["evidence"].update(schema="1")),
            ("exit_code false", lambda d: d["evidence"].update(exit_code=False)),
            ("exit_code \"0\"", lambda d: d["evidence"].update(exit_code="0")),
            ("dirty 0", lambda d: d["evidence"].update(dirty=0)),
            ("dirty \"false\"", lambda d: d["evidence"].update(dirty="false")),
            ("target_sha 짧은 접두사", lambda d: d["evidence"].update(target_sha=new[:12])),
            ("최상위 passed false", lambda d: d.update(passed=False)),
            ("role 누락", lambda d: d.pop("role")),
        ]
        for label, fn in mutations:
            d = json.loads(good.decode("utf-8"))
            fn(d)
            rp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8", newline="\n")
            before = marks(p)
            r = prepush(p, [line(new, old)])
            case(f"A20 켬 + {label} → 다시 돌림 · 0", r.returncode == 0 and marks(p) == before + 1,
                 f"rc={r.returncode} marks={before}->{marks(p)} err={r.stderr.strip()[-120:]}")
        for label, text in [("최상위 null", "null"), ("최상위 숫자", "3"), ("최상위 배열", "[]"),
                            ("evidence 없음", '{"role": "implementer", "passed": true}'),
                            ("evidence null", '{"role": "implementer", "passed": true, "evidence": null}'),
                            ("evidence 배열", '{"role": "implementer", "passed": true, "evidence": []}')]:
            rp.write_text(text, encoding="utf-8", newline="\n")
            before = marks(p)
            r = prepush(p, [line(new, old)])
            case(f"A21 켬 + 구조가 틀린 보고서({label}) → 예외 없이 다시 돌림 · 0 (필수-04)",
                 r.returncode == 0 and marks(p) == before + 1 and "Traceback" not in r.stderr,
                 f"rc={r.returncode} marks={before}->{marks(p)} err={r.stderr.strip()[-160:]}")

        # 필수-05: 재사용 가능한 첫 줄이 있어도 뒤 보호 줄의 사전 거부가 이긴다
        refs2 = '["refs/heads/main", "refs/heads/release"]'
        for label, mk in [
            ("보호 ref 삭제", lambda new, old, mid: f"(delete) {'0' * 40} refs/heads/release {old}"),
            ("보낼 커밋 ≠ HEAD", lambda new, old, mid: line(mid, old, "refs/heads/release")),
            ("원격 커밋이 로컬에 없음", lambda new, old, mid: line(new, "b" * 40, "refs/heads/release")),
        ]:
            p = make_project(base, f"a22-{len(results)}", refs=refs2)
            old = head(p)
            mid = commit(p, "mid\n")
            new = commit(p, "new\n")
            gates(p, old)
            before = marks(p)
            r = prepush(p, [line(new, old), mk(new, old, mid)])
            case(f"A22 켬 + 첫 줄 재사용 가능 · 둘째 보호 줄 {label} → 1 · 게이트 0회 (필수-05)",
                 r.returncode == 1 and marks(p) == before, f"rc={r.returncode} marks={before}->{marks(p)}")

        p = make_project(base, "a23", gate=MARK_FAIL_GATE)
        old = head(p)
        new = commit(p)
        r = prepush(p, [line(new, old)])
        case("A23 켬 + 보고서 없음 + 지금 실패하는 게이트 → 게이트 1회 · 1 (필수-06)",
             r.returncode == 1 and marks(p) == 1, f"rc={r.returncode} marks={marks(p)}")

        p = make_project(base, "a24")
        new = commit(p)
        gates(p, new)
        before = marks(p)
        r = prepush(p, [line(new, "0" * 40)])
        case("A24 켬 + 보호 ref 새로 만들기(원격 0) · 증거 base = 보낼 커밋 → 재사용 · 0 (필수-08①)",
             r.returncode == 0 and marks(p) == before, f"rc={r.returncode} marks={before}->{marks(p)}")

        p = make_project(base, "a25", role="test_author")
        old = head(p)
        new = commit(p)
        g = gates(p, old, role="test_author")
        before = marks(p)
        r = prepush(p, [line(new, old)])
        case("A25 켬 + 설정 · 보고서가 같은 다른 역할(test_author) → 재사용 · 0 (필수-08②)",
             g.returncode == 0 and r.returncode == 0 and marks(p) == before,
             f"gates={g.returncode} rc={r.returncode} marks={before}->{marks(p)}")

        p = make_project(base, "a26", refs=refs2)
        old = head(p)
        new = commit(p)
        gates(p, old)
        before = marks(p)
        r = prepush(p, [line(new, old), line(new, old, "refs/heads/release")])
        case("A26 켬 + 같은 기준의 보호 줄 둘 → 둘 다 재사용 · 게이트 0회 (필수-08③)",
             r.returncode == 0 and marks(p) == before, f"rc={r.returncode} marks={before}->{marks(p)}")

        for val in ("0", "1"):
            p, old, new = ready(base, f"a27-{val}", reuse=val)
            before = marks(p)
            r = prepush(p, [line(new, old)])
            case(f"A27 reuse_evidence = {val} (bool 아님) → 1 · 이유에 reuse_evidence · 게이트 0회 (필수-09)",
                 r.returncode == 1 and marks(p) == before and "reuse_evidence" in (r.stdout + r.stderr),
                 f"rc={r.returncode} marks={before}->{marks(p)}")

        p = make_project(base, "a29")
        old = head(p)
        new = commit(p)
        gates(p, old, role=" implementer ")
        before = marks(p)
        r = prepush(p, [line(new, old)])
        case("A29 (Codex 경계 사례 B04) 실제 보고서 role 이 ' implementer '(공백) · 설정 implementer → 재사용 안 함 · 다시 돌림",
             r.returncode == 0 and marks(p) == before + 1, f"rc={r.returncode} marks={before}->{marks(p)}")

        p, old, new = ready(base, "a30")
        rp = p / "reports" / "gate_report.json"
        rp.write_bytes(b"\xef\xbb\xbf" + rp.read_bytes())
        chk = sh(p, PY, "harness/evidence.py", "check", new)
        before = marks(p)
        r = prepush(p, [line(new, old)])
        case("A30 (Codex 경계 사례 B09) BOM 붙은 보고서: evidence.py check 처럼 증거로 보지 않음 → 다시 돌림",
             chk.returncode != 0 and r.returncode == 0 and marks(p) == before + 1,
             f"check={chk.returncode} rc={r.returncode} marks={before}->{marks(p)}")

        p, old, new = ready(base, "a28", reuse='"yes"')
        r = prepush(p, [line(new, old, "refs/heads/feature")])
        case("A28 bool 아닌 설정이어도 보호 줄이 없는 push 는 기존대로 0 (선택-01: 설정 검사는 보호 줄이 있을 때만)",
             r.returncode == 0, f"rc={r.returncode}")

    all_good = True
    for cid, good, detail in results:
        all_good &= good
        print(f"{'OK ' if good else 'BAD'} {cid}{('  — ' + detail) if (detail and not good) else ''}")
    print("결과: 모든 사례가 기대와 일치" if all_good else "결과: 기대와 다른 사례가 있다")
    return 0 if all_good else 1


if __name__ == "__main__":
    sys.exit(main())
