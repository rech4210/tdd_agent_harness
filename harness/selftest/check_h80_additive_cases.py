"""하네스 자기 검사: 추가 · 변이 제거 테스트의 H3 자동 승인 (WO-H80, HD-81 Q2).

계약은 dev/WO-H80.md §계약 R · A · V.
- R: run_gates.mutation_gate 가 LAST_MUTATION 에 변이 집계를 남기고(반환은 그대로), gate_result 가 변이 게이트 항목에 담는다.
- A: mark.py auto-approve additive 가 git · 잠금 · 판정 증거 · 변이 집계를 직접 확인하고, 모두 참일 때만 기록한다.
  거부 때는 어떤 파일도 바꾸지 않는다.
- V: harness/auto_approval.is_valid_auto 하나로 step.py · kpi.py 가 같은 판정을 한다.

사용: python harness/selftest/check_h80_additive_cases.py
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_kpi_cases import PY, events, make_project  # noqa: E402

ROLES = '''[roles.test_author]
allow = ["tests/**", "reports/**", "work_orders/**"]
deny  = ["src/**", "config/**"]

[roles.implementer]
allow = ["src/**", "reports/**"]
deny  = ["tests/**", "config/**"]
'''
SLICES = '[slice.WO-51]\ntitle = "t"\nfiles = ["src/a.py"]\n'
WO = "WO-51"
MUT_BASE = {"total": 10, "killed": 8, "survived": 2, "timeout": 0, "no_tests": 0, "excluded": 0}
MUT_HEAD = {"total": 10, "killed": 10, "survived": 0, "timeout": 0, "no_tests": 0, "excluded": 0}
NEW_TEST = "from src.a import f\n\ndef test_f_zero():\n    assert f(0) == 1\n"


def git(p: Path, *a: str) -> str:
    return subprocess.run(["git", *a], cwd=p, capture_output=True, text=True, check=True).stdout.strip()


def write(p: Path, rel: str, text: str | bytes) -> None:
    f = p / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        f.write_bytes(text)
    else:
        f.write_text(text, encoding="utf-8", newline="\n")


def commit_all(p: Path, msg: str) -> str:
    git(p, "add", "-A")
    git(p, "commit", "-qm", msg)
    return git(p, "rev-parse", "HEAD")


def lf_sha(data: bytes) -> str:
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def put_events(p: Path, evs: list[dict]) -> None:
    lines = [json.dumps({"ts": "2026-10-27T00:00:00Z", "head": "x", "branch": "main", **e}, ensure_ascii=False) for e in evs]
    (p / "reports" / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def cls(risk: str = "low", wo: str = WO) -> dict:
    return {"kind": "wo_class", "wo": wo, "tier": "T2", "risk": risk, "basis": "prospective", "confirmed_by": "user",
            "reason": "fixture"}


def report(target: str, base: str, *, role: str = "test_author", passed: object = True, scope: str = "full",
           exit_code: object = 0, dirty: object = False, schema: object = 1, wo: str = WO,
           mutation: dict | None = None, started: str = "2026-10-27T01:00:00Z") -> dict:
    gates = [{"id": "G2", "name": "tests", "passed": True, "seconds": 0.1, "detail": ""}]
    g6 = {"id": "G6", "name": "mutation", "passed": passed, "seconds": 0.1, "detail": ""}
    if mutation is not None:
        g6["mutation"] = mutation
    gates.append(g6)
    return {"wo": wo, "slice": wo, "role": role, "cause": None, "base": base, "started": started, "passed": passed,
            "gates": gates,
            "evidence": {"schema": schema, "target_sha": target, "base_sha": base, "dirty": dirty, "harness_version": None,
                         "scope": scope, "exit_code": exit_code, "verdict": "ALL PASS"}}


def base_run(p: Path, base: str, name: str = "base.json", *, mutation: dict = MUT_BASE,
             started: str = "2026-10-27T00:30:00Z", **kw) -> None:
    write(p, f"reports/runs/{name}", json.dumps(report(base, base, passed=False, exit_code=1, mutation=mutation,
                                                       started=started, **kw)))


def build(base_dir: Path, name: str, *, risk: str = "low", roles: str | None = ROLES, locked: list[str] | None = None,
          new_files: dict[str, str] | None = None, lock_override: str | None = None,
          base_extra: Callable[[Path], None] | None = None) -> tuple[Path, str, str]:
    """기준 커밋(제품 · 잠긴 테스트 2개 · 잠기지 않은 작업 지시서 · 잠금)과 HEAD(새 테스트 추가), 정상 증거를 만든다."""
    p = make_project(base_dir, name)
    if locked is not None:
        write(p, "config/gates.toml", '[harness]\nadapter = "python"\nlocked = ' + json.dumps(locked) + "\n")
    if roles is not None:
        write(p, "config/roles.toml", roles)
    write(p, "config/slices.toml", SLICES)
    write(p, "src/a.py", "def f(x):\n    return x + 1\n")
    write(p, "tests/test_a.py", "from src.a import f\n\ndef test_f():\n    assert f(1) == 2\n")
    write(p, "tests/test_a2.py", "def test_two():\n    assert 2 == 2\n")
    write(p, "work_orders/WO-51.md", "# WO-51\n")
    if base_extra:
        base_extra(p)
    lock = {rel: lf_sha((p / rel).read_bytes()) for rel in ("tests/test_a.py", "tests/test_a2.py")}
    write(p, "config/test_lock.json", lock_override if lock_override is not None else json.dumps(lock, indent=2))
    base = commit_all(p, "base with lock")
    for rel, text in (new_files or {"tests/test_b.py": NEW_TEST}).items():
        write(p, rel, text)
    head = commit_all(p, "add tests")
    put_events(p, [cls(risk)])
    write(p, "reports/gate_report.json", json.dumps(report(head, base, mutation=MUT_HEAD)))
    base_run(p, base)
    return p, base, head


def head_variant(p: Path, base: str, mutate: Callable[[Path], None], *, index_only: bool = False) -> str:
    """기준은 그대로 두고 HEAD 를 다시 만든 뒤 정상 증거를 새 HEAD 에 맞춘다.
    index_only: mutate 가 인덱스를 직접 고친다(모드 · gitlink). git add -A 로 덮지 않고 그대로 커밋한다."""
    git(p, "reset", "-q", "--hard", base)
    if index_only:
        # 플랫폼과 무관하게 인덱스의 모드 · 링크가 작업 트리와 어긋나 dirty 로 보이지 않게 한다 (3회차 E01)
        git(p, "config", "core.filemode", "false")
        git(p, "config", "core.symlinks", "false")
    write(p, "tests/test_b.py", NEW_TEST)
    git(p, "add", "tests/test_b.py")
    mutate(p)
    h = commit_index(p, "head variant") if index_only else commit_all(p, "head variant")
    write(p, "reports/gate_report.json", json.dumps(report(h, base, mutation=MUT_HEAD)))
    return h


def raw_modes(p: Path, base: str) -> str:
    return git(p, "diff", "--raw", "--no-renames", base, "HEAD")


def approve(p: Path, base: str, *extra: str, kind: str = "regression", note: str = "test_b asserts f(0)==1 boundary",
            by: str = "lead", wo: str = WO) -> subprocess.CompletedProcess[str]:
    args = [PY, "harness/mark.py", "auto-approve", "additive", "--wo", wo, "--kind", kind, "--base", base,
            "--review-note", note, "--by", by, *extra]
    return subprocess.run(args, cwd=p, capture_output=True, text=True, encoding="utf-8", errors="replace")


def autos(p: Path) -> list[dict]:
    return [e for e in events(p) if e.get("kind") == "auto_approval"]


def snapshot(p: Path) -> dict[str, bytes]:
    snap = {f.relative_to(p).as_posix(): f.read_bytes() for f in sorted(p.rglob("*"))
            if f.is_file() and ".git" not in f.relative_to(p).parts and "__pycache__" not in f.parts}
    # git 상태도 보존 대상: HEAD · 인덱스(스테이지 내용) · 상태 요약
    for key, cmd in (("<HEAD>", ["rev-parse", "HEAD"]), ("<index>", ["ls-files", "-s"]),
                     ("<status>", ["status", "--porcelain", "--untracked-files=all"])):
        out = git(p, *cmd)
        if key == "<status>":  # 하네스 실행이 만드는 인터프리터 캐시는 보존 대상이 아니다
            out = "\n".join(l for l in out.splitlines() if "__pycache__" not in l)
        snap[key] = out.encode("utf-8")
    return snap


def commit_index(p: Path, msg: str) -> str:
    """git add 없이 지금 인덱스 그대로 커밋한다(인덱스에 직접 넣은 모드 · gitlink 를 보존, 플랫폼 무관)."""
    git(p, "commit", "-qm", msg)
    return git(p, "rev-parse", "HEAD")


def main() -> int:
    rows: list[tuple[str, bool, str]] = []

    def case(name: str, good: bool, detail: str = "") -> None:
        rows.append((name, bool(good), detail.replace("\n", " | ")[:300]))

    def accepted(label: str, p: Path, base: str, *extra: str, kind: str = "regression") -> dict:
        r = approve(p, base, *extra, kind=kind)
        a = autos(p)
        case(f"A0x {label} → 종료 0 · auto_approval 1건", r.returncode == 0 and len(a) == 1,
             f"rc={r.returncode} {(r.stdout + r.stderr)[-220:]}")
        return a[0] if a else {}

    def refused(label: str, p: Path, base: str, *extra: str, kind: str = "regression") -> None:
        before = snapshot(p)
        r = approve(p, base, *extra, kind=kind)
        out = r.stdout + r.stderr
        after = snapshot(p)
        changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
        lines = [l for l in out.splitlines() if l.strip()]
        reason = [l for l in lines if l.startswith("이유: ") and len(l.strip()) > len("이유:")]
        case(f"A1x {label} → 종료 1 · '조건 미달: 사람 H3 승인이 필요하다' + '이유: …' 줄 · 파일 · git 상태 변경 0",
             r.returncode == 1 and "조건 미달: 사람 H3 승인이 필요하다" in out and bool(reason) and not changed
             and "Traceback" not in out, f"rc={r.returncode} changed={changed[:3]} {out[-200:]}")

    tmp = Path(tempfile.mkdtemp(prefix="h80-"))
    try:
        # ── A: 정상 ───────────────────────────────────────────────────────────────
        p, base, head = build(tmp, "a01")
        e = accepted("regression 정상", p, base)
        case("A01 기록 필드 {wo, policy additive, additive_kind regression, base_sha, head_sha, files, review_note, by}, "
             "mutation 없음",
             e.get("policy") == "additive" and e.get("additive_kind") == "regression" and e.get("wo") == WO
             and e.get("base_sha") == base and e.get("head_sha") == head and e.get("files") == ["tests/test_b.py"]
             and e.get("review_note") == "test_b asserts f(0)==1 boundary" and e.get("by") == "lead"
             and "mutation" not in e, json.dumps(e, ensure_ascii=False)[:250])

        p, base, head = build(tmp, "a02")
        e = accepted("mutant-kill 정상(미검출 2 → 0)", p, base, kind="mutant-kill")
        case("A02 mutant-kill 기록에 mutation {base, head} 집계 그대로",
             e.get("additive_kind") == "mutant-kill" and e.get("mutation") == {"base": MUT_BASE, "head": MUT_HEAD},
             json.dumps(e, ensure_ascii=False)[:250])

        many = {f"tests/test_n{i:02d}.py": NEW_TEST for i in range(30)}
        many["work_orders/note.md"] = "n\n"
        p, base, head = build(tmp, "a03", new_files=dict(reversed(list(many.items()))))
        e = accepted("추가 31개(테스트 30 + 작업 지시서 1)", p, base)
        case("A03 files = 추가 경로 전체 정렬", e.get("files") == sorted(many), str(e.get("files"))[:200])

        p, base, head = build(tmp, "a04", roles='[roles.test_author]\nallow = ["spec_tests/**"]\n', locked=["spec_tests/**"],
                              new_files={"spec_tests/test_x.py": NEW_TEST})
        accepted("프로젝트 설정 경로(allow · locked = spec_tests/**)", p, base)

        p, base, head = build(tmp, "a05", base_extra=lambda q: write(q, "tests/test_a2.py", b"def test_two():\r\n    assert 2 == 2\r\n"))
        accepted("잠긴 파일이 CRLF 로 저장(해시는 LF 정규화로 비교)", p, base)

        p, base, head = build(tmp, "a06")
        put_events(p, [cls("high"), cls("low"), cls("high", wo="WO-99")])
        accepted("같은 WO 마지막 분류 low(앞 high · 다른 WO high 무시)", p, base)

        p, base, head = build(tmp, "a07")
        (p / "reports" / "runs" / "base.json").unlink()
        # 이름순 첫 적격(a_first, 미검출 0)을 고르면 거부, 가장 늦은 적격(y_new, 미검출 2)을 고르면 승인
        base_run(p, base, "a_first.json", mutation=dict(MUT_BASE, survived=0, killed=10), started="2026-10-27T00:10:00Z")
        base_run(p, base, "y_new.json", started="2026-10-27T00:50:00Z")
        base_run(p, base, "m_dirty.json", mutation=dict(MUT_BASE, survived=0, killed=10), started="2026-10-27T00:59:00Z",
                 dirty=True)
        base_run(p, base, "m_partial.json", mutation=dict(MUT_BASE, survived=0, killed=10),
                 started="2026-10-27T00:58:00Z", scope="partial")
        base_run(p, "f" * 40, "m_other.json", mutation=dict(MUT_BASE, survived=0, killed=10), started="2026-10-27T00:57:00Z")
        write(p, "reports/runs/broken.json", "{not json")
        accepted("mutant-kill: 가장 늦은 적격 기준 실행(y_new, 미검출 2) — 이름순 첫 적격 · 부적격(dirty · 부분 · 다른 커밋) · 깨진 파일 무시",
                 p, base, kind="mutant-kill")

        p, base, head = build(tmp, "a07b")
        (p / "reports" / "runs" / "base.json").unlink()
        base_run(p, base, "p1.json", mutation=dict(MUT_BASE, survived=0, killed=10), started="2026-10-27T00:50:00Z")
        base_run(p, base, "p2.json", started="2026-10-27T00:50:00Z")
        accepted("mutant-kill: started 같으면 파일 이름이 뒤인 것(p2, 미검출 2)", p, base, kind="mutant-kill")

        p, base, head = build(tmp, "a09", base_extra=lambda q: write(q, "reports/keep.md", "kept\n"))
        write(p, "reports/keep.md", "changed after commit\n")
        accepted("추적된 reports 파일의 작업 트리 변경은 깨끗함 판정에서 제외", p, base)

        p, base, head = build(tmp, "a08")
        (p / "reports" / "runs" / "base.json").unlink()
        for i in range(60):
            base_run(p, base, f"r{i:02d}.json", mutation=dict(MUT_BASE, survived=0, killed=10),
                     started=f"2026-10-27T00:{i:02d}:00Z")
        base_run(p, base, "zz_latest.json", started="2026-10-27T02:00:00Z")
        accepted("mutant-kill: 기준 실행 61개 중 마지막 적격(목록 끝)", p, base, kind="mutant-kill")

        # ── A: 조건 미달 → 종료 1, 파일 변경 0 ──────────────────────────────────
        p, base, _ = build(tmp, "b01")
        head_variant(p, base, lambda q: write(q, "src/a.py", "def f(x):\n    return x + 1  # touched\n"))
        refused("제품 파일 수정(M)", p, base)
        p, base, _ = build(tmp, "b02")
        head_variant(p, base, lambda q: write(q, "src/new.py", "X = 1\n"))
        refused("새 제품 파일(A, 테스트 경로 밖)", p, base)
        p, base, _ = build(tmp, "b03")
        head_variant(p, base, lambda q: write(q, "tests/test_a.py", "def test_f():\n    assert True\n"))
        refused("잠긴 테스트 수정(M)", p, base)
        p, base, _ = build(tmp, "b04")
        head_variant(p, base, lambda q: (q / "tests" / "test_a.py").unlink())
        refused("잠긴 테스트 삭제(D)", p, base)
        p, base, _ = build(tmp, "b05")
        head_variant(p, base, lambda q: git(q, "mv", "tests/test_a.py", "tests/test_a_renamed.py"))
        refused("이름 변경(R → D+A)", p, base)
        p, base, _ = build(tmp, "b06")
        head_variant(p, base, lambda q: write(q, "work_orders/WO-51.md", "# changed\n"))
        refused("잠기지 않은 허용 경로 기존 파일 수정(work_orders M)", p, base)
        p, base, _ = build(tmp, "b07")
        head_variant(p, base, lambda q: (q / "work_orders" / "WO-51.md").unlink())
        refused("잠기지 않은 허용 경로 기존 파일 삭제(work_orders D)", p, base)
        def blob(q: Path, text: str) -> str:
            return subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=q, input=text, capture_output=True,
                                  text=True, check=True).stdout.strip()

        def link(q: Path, rel: str, target: str) -> None:
            """심볼릭 링크를 인덱스에 넣고, core.symlinks=false 체크아웃과 같은 일반 파일(링크 대상 글자)을 작업 트리에 둔다."""
            git(q, "update-index", "--add", "--cacheinfo", f"120000,{blob(q, target)},{rel}")
            (q / rel).write_bytes(target.encode("utf-8"))

        def gitlink(q: Path, rel: str) -> None:
            git(q, "update-index", "--add", "--cacheinfo", f"160000,{git(q, 'rev-parse', 'HEAD')},{rel}")
            (q / rel).mkdir(parents=True, exist_ok=True)  # 초기화 안 된 하위 모듈 = 빈 폴더, git 상태 깨끗

        def index_case(name: str, label: str, mutate: Callable[[Path], None], want: str) -> None:
            p, base, _ = build(tmp, name)
            head_variant(p, base, mutate, index_only=True)
            raw = raw_modes(p, base)
            status = [l for l in git(p, "status", "--porcelain", "--untracked-files=all").splitlines()
                      if l.strip() and not l[3:].startswith("reports/")]
            case(f"A2x fixture 확인: {label} — raw diff 에 '{want}', reports 밖 작업 트리 깨끗(다른 조건이 가리지 않음)",
                 want in raw and not status, f"{raw[-160:]} status={status[:3]}")
            refused(label, p, base)

        index_case("b08", "잠긴 테스트 실행 권한만 변경(모드 M)",
                   lambda q: git(q, "update-index", "--chmod=+x", "tests/test_a.py"), "100644 100755")
        index_case("b15", "잠기지 않은 허용 경로 파일 실행 권한만 변경",
                   lambda q: git(q, "update-index", "--chmod=+x", "work_orders/WO-51.md"), "100644 100755")
        index_case("b09", "하위 모듈 추가(160000)", lambda q: gitlink(q, "tests/submod"), " 160000 ")
        index_case("b16", "심볼릭 링크 추가(120000)", lambda q: link(q, "tests/link_test.py", "test_a.py"), " 120000 ")
        index_case("b17", "잠기지 않은 허용 경로 기존 파일을 링크로 형식 변경(T)",
                   lambda q: link(q, "work_orders/WO-51.md", "WO-51-target.md"), " T\t")
        p, base, _ = build(tmp, "b10")
        head_variant(p, base, lambda q: write(q, "docs/note.md", "x\n"))
        refused("테스트 경로 밖 문서 추가", p, base)
        p, base, _ = build(tmp, "b11")
        head_variant(p, base, lambda q: write(q, "config/extra.toml", "x = 1\n"))
        refused("설정 파일 추가", p, base)
        b12_files = {**{f"tests/t{i:02d}.py": NEW_TEST for i in range(25)}, "zz_root.py": "Z = 1\n"}
        case("A2x fixture 확인: b12 금지 경로가 정렬상 마지막", sorted(b12_files)[-1] == "zz_root.py", "")
        p, base, _ = build(tmp, "b12", new_files=b12_files)
        refused("추가 26개 중 정렬상 마지막 하나가 허용 경로 밖", p, base)
        p, base, _ = build(tmp, "b13", new_files={"work_orders/extra.md": "x\n"})
        refused("잠금 범위 테스트 추가 없음(작업 지시서만)", p, base)
        p, base, _ = build(tmp, "b14", roles='[roles.test_author]\nallow = ["specs_only/**"]\n')
        refused("roles.toml test_author allow 에 tests/** 없음(하드코딩 금지)", p, base)

        p, base, _ = build(tmp, "c01")
        head_variant(p, base, lambda q: write(q, "config/test_lock.json", json.dumps({"tests/test_a.py": "0" * 64})))
        refused("잠금 파일 변경(M)", p, base)
        p, base, _ = build(tmp, "c03", lock_override=json.dumps({"tests/test_a.py": "0" * 64}))
        refused("잠금은 기준 · HEAD 에서 같지만 첫 항목 해시가 현재 파일과 다름", p, base)
        p, base, _ = build(tmp, "c04", lock_override=None)
        good_lock = json.loads((p / "config" / "test_lock.json").read_text(encoding="utf-8"))
        p, base, _ = build(tmp, "c05", lock_override=json.dumps({"tests/test_a.py": good_lock["tests/test_a.py"],
                                                                "tests/test_a2.py": "1" * 64}))
        refused("잠금 두 번째 항목만 해시 불일치", p, base)
        p, base, _ = build(tmp, "c06", lock_override=json.dumps({"tests/test_a.py": good_lock["tests/test_a.py"],
                                                                "tests/ghost.py": "2" * 64}))
        refused("잠금이 가리키는 파일이 없음", p, base)
        p, base, _ = build(tmp, "c07", lock_override="{not json")
        refused("잠금 파일 JSON 깨짐", p, base)
        p, base, _ = build(tmp, "c08", lock_override=json.dumps([["tests/test_a.py", "x"]]))
        refused("잠금 파일 최상위가 객체 아님", p, base)
        p, base, _ = build(tmp, "c09", lock_override=json.dumps({"tests/test_a.py": 5}))
        refused("잠금 해시 값이 문자열 아님", p, base)
        # 경계 사례 설계 L01: 잠금 경로가 프로젝트 밖을 가리키면 해시가 맞아도 거부
        outside = tmp / "outside_lock_target.py"
        outside.write_text("X = 1\n", encoding="utf-8")
        for name, key in (("c12", "../outside_lock_target.py"), ("c13", outside.resolve().as_posix()),
                          ("c14", "tests/../../outside_lock_target.py")):
            p, base, _ = build(tmp, name, lock_override=json.dumps({"tests/test_a.py": good_lock["tests/test_a.py"],
                                                                    key: lf_sha(outside.read_bytes())}))
            refused(f"잠금 경로가 프로젝트 밖({key[:30]})", p, base)
        p, base, _ = build(tmp, "c11", lock_override="﻿" + json.dumps(good_lock, indent=2))
        refused("잠금 파일 UTF-8 BOM(해시는 정상, BOM 만 다름)", p, base)
        p, base, _ = build(tmp, "c10")
        head_variant(p, base, lambda q: (q / "config" / "test_lock.json").unlink())
        refused("HEAD 에 잠금 파일 없음", p, base)

        p, base, _ = build(tmp, "d01")
        write(p, "tests/test_c.py", "def test_c():\n    assert 1\n")
        refused("추적 안 된 새 테스트(작업 트리 깨끗하지 않음)", p, base)
        p, base, _ = build(tmp, "d02")
        write(p, "tests/test_b.py", NEW_TEST + "# staged\n")
        git(p, "add", "tests/test_b.py")
        refused("추적 파일 staged 변경", p, base)
        p, base, _ = build(tmp, "d03")
        write(p, "tests/test_b.py", NEW_TEST + "# unstaged\n")
        refused("추적 파일 unstaged 변경", p, base)

        p, base, _ = build(tmp, "e01", risk="high")
        refused("분류 위험도 high", p, base)
        p, base, _ = build(tmp, "e02")
        put_events(p, [])
        refused("분류 기록 없음", p, base)
        p, base, _ = build(tmp, "e03")
        put_events(p, [cls("low"), cls("high")])
        refused("같은 WO 마지막 분류가 high(앞 low)", p, base)
        p, base, _ = build(tmp, "e04")
        put_events(p, [cls("low", wo="WO-99")])
        refused("다른 WO 분류만 있음", p, base)
        p, base, _ = build(tmp, "e05")
        put_events(p, [cls("high"), dict(cls("low"), confirmed_by="  ")])
        refused("유효 high 뒤 무효 low(확인자 공백) — 무효 기록으로 덮지 않음", p, base)
        p, base, _ = build(tmp, "e06")
        put_events(p, [cls("high"), dict(cls("low"), basis="retrospective")])
        refused("작업 전(prospective) high 뒤 사후(retrospective) low — wo_class_for 는 작업 전 우선", p, base)

        p, base, head = build(tmp, "f01")
        refused("기준 = HEAD", p, head)
        p, base, head = build(tmp, "f02")
        git(p, "checkout", "-q", "-b", "side", base)
        write(p, "tests/test_side.py", "def test_s():\n    assert 1\n")
        side = commit_all(p, "side")
        git(p, "checkout", "-q", "main")
        refused("기준이 HEAD 의 조상이 아님", p, side)
        p, base, _ = build(tmp, "f03", roles=None)
        refused("roles.toml 없음", p, base)
        p, base, _ = build(tmp, "f04", roles='[roles.implementer]\nallow = ["src/**"]\n')
        refused("roles.toml 에 test_author 없음", p, base)
        p, base, _ = build(tmp, "f05", roles='[roles.test_author\nallow = ["tests/**"]\n')
        refused("roles.toml TOML 깨짐", p, base)
        p, base, _ = build(tmp, "f06", roles="﻿" + ROLES)
        refused("roles.toml UTF-8 BOM", p, base)
        p, base, _ = build(tmp, "f07", roles='[roles.test_author]\nallow = "tests/**"\n')
        refused("roles.toml allow 가 목록 아님", p, base)

        def evidence(label: str, *, raw: bytes | str | None = None, missing: bool = False, **kw) -> None:
            p, base, head = build(tmp, "g-" + label.split()[0])
            if missing:
                (p / "reports" / "gate_report.json").unlink()
            elif raw is not None:
                write(p, "reports/gate_report.json", raw)
            else:
                target = kw.pop("target", head)
                write(p, "reports/gate_report.json", json.dumps(report(target, kw.pop("rbase", base), mutation=MUT_HEAD, **kw)))
            refused("판정 증거: " + label, p, base)

        evidence("g01 보고서 없음", missing=True)
        evidence("g02 대상 커밋이 HEAD 아님", target="0" * 40)
        evidence("g03 부분 실행", scope="partial")
        evidence("g04 종료 1", exit_code=1)
        evidence("g05 dirty true", dirty=True)
        evidence("g06 역할 implementer", role="implementer")
        evidence("g07 passed false", passed=False)
        evidence("g08 기준 커밋 불일치", rbase="1" * 40)
        evidence("g09 schema True(bool)", schema=True)
        evidence("g10 dirty 0(int)", dirty=0)
        evidence("g11 exit_code False(bool)", exit_code=False)
        evidence("g12 passed 1(int)", passed=1)
        evidence("g13 보고서 wo 다름", wo="WO-99")
        evidence("g14 JSON 깨짐", raw="{not json")
        evidence("g15 최상위 목록", raw="[]")
        evidence("g17 schema 2", schema=2)
        for fld in ("schema", "target_sha", "base_sha", "dirty", "scope", "exit_code"):
            p, base, head = build(tmp, f"g18-{fld}")
            rep = report(head, base, mutation=MUT_HEAD)
            del rep["evidence"][fld]
            write(p, "reports/gate_report.json", json.dumps(rep))
            refused(f"판정 증거: g18 evidence.{fld} 누락", p, base)
        for fld in ("wo", "role", "passed"):
            p, base, head = build(tmp, f"g20-{fld}")
            rep = report(head, base, mutation=MUT_HEAD)
            del rep[fld]
            write(p, "reports/gate_report.json", json.dumps(rep))
            refused(f"판정 증거: g20 보고서 {fld} 누락", p, base)
        p, base, head = build(tmp, "g19")
        rep = report(head, base, mutation=MUT_HEAD)
        rep["evidence"] = []
        write(p, "reports/gate_report.json", json.dumps(rep))
        refused("판정 증거: g19 evidence 가 객체 아님", p, base)
        p, base, head = build(tmp, "g16")
        write(p, "reports/gate_report.json", "﻿".encode("utf-8") + json.dumps(report(head, base, mutation=MUT_HEAD)).encode("utf-8"))
        refused("판정 증거: g16 UTF-8 BOM", p, base)

        p, base, _ = build(tmp, "h01")
        (p / "reports" / "runs" / "base.json").unlink()
        refused("mutant-kill: 기준 실행 기록 없음", p, base, kind="mutant-kill")
        p, base, head = build(tmp, "h02")
        write(p, "reports/gate_report.json", json.dumps(report(head, base, mutation=MUT_BASE)))
        refused("mutant-kill: 미검출 줄지 않음(2 → 2)", p, base, kind="mutant-kill")
        p, base, head = build(tmp, "h03")
        write(p, "reports/gate_report.json", json.dumps(report(head, base, mutation=dict(MUT_HEAD, total=11))))
        refused("mutant-kill: total 다름", p, base, kind="mutant-kill")
        p, base, head = build(tmp, "h04")
        write(p, "reports/gate_report.json", json.dumps(report(head, base, mutation=None)))
        refused("mutant-kill: HEAD 변이 집계 없음", p, base, kind="mutant-kill")
        p, base, _ = build(tmp, "h05")
        (p / "reports" / "runs" / "base.json").unlink()
        base_run(p, base, scope="partial")
        refused("mutant-kill: 기준 실행이 부분 범위뿐", p, base, kind="mutant-kill")
        p, base, _ = build(tmp, "h06")
        (p / "reports" / "runs" / "base.json").unlink()
        base_run(p, base, "a_older.json", mutation=dict(MUT_BASE, survived=5, killed=5), started="2026-10-27T00:10:00Z")
        base_run(p, base, "z_latest.json", mutation=dict(MUT_BASE, survived=0, killed=10), started="2026-10-27T00:40:00Z")
        refused("mutant-kill: 가장 늦은 적격 기준(z_latest, 미검출 0)에서 줄지 않음 — 이름순 첫(a_older)이면 승인돼 버림",
                p, base, kind="mutant-kill")
        # 경계 사례 설계 E01: started 는 문자열이 아니라 시각으로 비교(시간대 · 밀리초 표기 혼용)
        p, base, _ = build(tmp, "h11")
        (p / "reports" / "runs" / "base.json").unlink()
        base_run(p, base, "old_kst.json", started="2026-10-27T10:00:00+09:00")  # = 01:00Z, 미검출 2
        base_run(p, base, "new_utc.json", mutation=dict(MUT_BASE, survived=0, killed=10), started="2026-10-27T02:00:00Z")
        refused("mutant-kill: started 시간대 혼용 — 실제 최신(02:00Z, 미검출 0) 기준", p, base, kind="mutant-kill")
        p, base, _ = build(tmp, "h12")
        (p / "reports" / "runs" / "base.json").unlink()
        base_run(p, base, "plain.json", started="2026-10-27T01:00:00Z")  # 미검출 2
        base_run(p, base, "millis.json", mutation=dict(MUT_BASE, survived=0, killed=10), started="2026-10-27T01:00:00.500Z")
        refused("mutant-kill: started 밀리초 표기 — 0.5초 뒤 실행(미검출 0) 기준", p, base, kind="mutant-kill")
        p, base, _ = build(tmp, "h13")
        (p / "reports" / "runs" / "base.json").unlink()
        base_run(p, base, "garbage.json", started="not-a-time")
        refused("mutant-kill: started 를 해석할 수 없는 실행은 적격 아님(다른 적격 없음)", p, base, kind="mutant-kill")

        p, base, head = build(tmp, "h07")
        write(p, "reports/gate_report.json", json.dumps(report(head, base, mutation=dict(MUT_HEAD, survived=True))))
        refused("mutant-kill: 집계 필드 bool", p, base, kind="mutant-kill")
        p, base, head = build(tmp, "h08")
        base_run(p, base, mutation={"total": 10, "killed": 8, "survived": 2, "timeout": 0, "no_tests": 0, "excluded": 0})
        write(p, "reports/gate_report.json", json.dumps(report(head, base, mutation=dict(MUT_HEAD, killed=8, survived=1, no_tests=1))))
        refused("mutant-kill: (S,N,E) (2,0,0) → (1,1,0) — no_tests 포함 미검출 같음", p, base, kind="mutant-kill")
        p, base, head = build(tmp, "h09")
        base_run(p, base, mutation={"total": 10, "killed": 8, "survived": 2, "timeout": 0, "no_tests": 0, "excluded": 1})
        write(p, "reports/gate_report.json", json.dumps(report(head, base, mutation=dict(MUT_HEAD, killed=9, survived=1, excluded=0))))
        refused("mutant-kill: (S,N,E) (2,0,1) → (1,0,0) — excluded 빼면 미검출 같음", p, base, kind="mutant-kill")
        p, base, head = build(tmp, "h10")
        base_run(p, base, mutation={k: v for k, v in MUT_BASE.items() if k != "excluded"})
        refused("mutant-kill: 기준 집계 필드 누락", p, base, kind="mutant-kill")

        # ── A: 형식 오류 → 종료 2, 기록 없음 ──────────────────────────────────────
        p, base, head = build(tmp, "fmt")
        res: dict[str, int] = {}
        moved: dict[str, list[str]] = {}

        def fmt_call(label: str, fn: Callable[[], subprocess.CompletedProcess]) -> None:
            # 호출마다 앞뒤를 비교한다(뒤 호출이 앞의 변경을 되돌려 숨기지 못하게, 3회차 F15)
            before = snapshot(p)
            res[label] = fn().returncode
            after = snapshot(p)
            ch = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
            if ch:
                moved[label] = ch[:3]

        fmt_call("kind 없음", lambda: subprocess.run([PY, "harness/mark.py", "auto-approve", "additive", "--wo", WO, "--base", base,
                                                     "--review-note", "n", "--by", "lead"], cwd=p, capture_output=True, text=True))
        fmt_call("kind 값 밖", lambda: approve(p, base, kind="other"))
        fmt_call("base 짧음", lambda: approve(p, base[:12]))
        fmt_call("base 16진 아님", lambda: approve(p, "z" * 40))
        fmt_call("base 대문자", lambda: approve(p, base.upper()))
        fmt_call("note 공백", lambda: approve(p, base, note="   "))
        fmt_call("by 공백", lambda: approve(p, base, by=" "))
        fmt_call("wo 공백", lambda: approve(p, base, wo=" "))
        for opt, val in (("--gt", "pass"), ("--lazy-caught", "1/1"), ("--residual-defects", "0"),
                         ("--base-types", "unbounded,reject-preserve")):
            fmt_call("additive + " + opt, lambda opt=opt, val=val: approve(p, base, opt, val))
        d28 = [PY, "harness/mark.py", "auto-approve", "D-28", "--wo", WO, "--gt", "pass", "--lazy-caught", "1/1",
               "--residual-defects", "0", "--base-types", "unbounded,reject-preserve", "--by", "lead"]
        for opt, val in (("--kind", "regression"), ("--base", base), ("--review-note", "n")):
            fmt_call("D-28 + " + opt, lambda opt=opt, val=val: subprocess.run(d28 + [opt, val], cwd=p, capture_output=True, text=True))
        case("A30 형식 오류 15가지 → 종료 2 · 호출마다 파일 · git 상태 변경 0",
             all(v == 2 for v in res.values()) and not moved,
             str({k: v for k, v in res.items() if v != 2}) + f" moved={moved}")

        # ── V: 유효성 판정 하나로 ─────────────────────────────────────────────────
        sys.path.insert(0, str(ROOT / "harness"))
        try:
            import auto_approval  # noqa: E402
            import kpi  # noqa: E402
            have = True
        except Exception as ex:  # noqa: BLE001
            have = False
            case("V00 harness/auto_approval.py 가져오기", False, str(ex))
        if have:
            good = {"kind": "auto_approval", "wo": WO, "policy": "additive", "additive_kind": "regression",
                    "base_sha": "a" * 40, "head_sha": "b" * 40, "files": ["tests/test_b.py"], "review_note": "n", "by": "lead"}
            mk = dict(good, additive_kind="mutant-kill", mutation={"base": MUT_BASE, "head": MUT_HEAD})
            d28e = {"kind": "auto_approval", "wo": WO, "policy": "D-28", "gt": "pass", "lazy_caught": 1, "lazy_total": 1,
                    "residual_defects": 0, "base_types": ["unbounded", "reject-preserve"], "by": "lead"}
            drop = lambda d, k: {x: y for x, y in d.items() if x != k}  # noqa: E731
            bad = {
                "risk high": (good, "high"), "kind 없음": (drop(good, "kind"), "low"),
                "kind 다름": (dict(good, kind="test_lock"), "low"), "policy 다름": (dict(good, policy="Additive"), "low"),
                "additive_kind 밖": (dict(good, additive_kind="other"), "low"), "base=head": (dict(good, head_sha="a" * 40), "low"),
                "base 짧음": (dict(good, base_sha="a" * 12), "low"), "head 39자": (dict(good, head_sha="b" * 39), "low"),
                "sha 대문자": (dict(good, base_sha="A" * 40), "low"), "files 빈 목록": (dict(good, files=[]), "low"),
                "files 문자열": (dict(good, files="tests/test_b.py"), "low"), "files 원소 int": (dict(good, files=[1]), "low"),
                "review_note 공백": (dict(good, review_note="  "), "low"), "review_note int": (dict(good, review_note=5), "low"),
                "by 없음": (drop(good, "by"), "low"), "by int": (dict(good, by=1), "low"),
                "mk mutation 없음": (drop(mk, "mutation"), "low"),
                "mk 줄지 않음": (dict(mk, mutation={"base": MUT_BASE, "head": MUT_BASE}), "low"),
                "mk total 다름": (dict(mk, mutation={"base": MUT_BASE, "head": dict(MUT_HEAD, total=11)}), "low"),
                "mk head bool": (dict(mk, mutation={"base": MUT_BASE, "head": dict(MUT_HEAD, survived=False)}), "low"),
                "mk base bool": (dict(mk, mutation={"base": dict(MUT_BASE, total=True), "head": MUT_HEAD}), "low"),
                "mk head 문자열": (dict(mk, mutation={"base": MUT_BASE, "head": dict(MUT_HEAD, killed="10")}), "low"),
                "mk head 필드 누락": (dict(mk, mutation={"base": MUT_BASE, "head": drop(MUT_HEAD, "no_tests")}), "low"),
                "mk excluded 고려": (dict(mk, mutation={"base": dict(MUT_BASE, excluded=1),
                                                       "head": dict(MUT_HEAD, killed=9, survived=1)}), "low"),
                "mk no_tests 고려": (dict(mk, mutation={"base": MUT_BASE,
                                                       "head": dict(MUT_HEAD, killed=8, survived=1, no_tests=1)}), "low"),
                "head 16진 아님": (dict(good, head_sha="g" * 40), "low"), "by 공백": (dict(good, by="  "), "low"),
                "head 대문자": (dict(good, head_sha="B" * 40), "low"),
                "mutation 이 dict 아님": (dict(mk, mutation=[MUT_BASE, MUT_HEAD]), "low"),
            }
            for side in ("base", "head"):
                src_d = MUT_BASE if side == "base" else MUT_HEAD
                for fld in src_d:
                    for how, val in (("누락", None), ("문자열", "1"), ("bool", True)):
                        d = drop(src_d, fld) if val is None else dict(src_d, **{fld: val})
                        m2 = {"base": MUT_BASE, "head": MUT_HEAD, side: d}
                        bad[f"mk {side}.{fld} {how}"] = (dict(mk, mutation=m2), "low")
            wrong = [k for k, (ev, rk) in bad.items() if auto_approval.is_valid_auto(ev, rk)]
            case("V01 additive 정상(regression · mutant-kill) 유효, D-28 정상 유효 · high 무효(기존 규칙 그대로)",
                 auto_approval.is_valid_auto(good, "low") and auto_approval.is_valid_auto(mk, "medium")
                 and auto_approval.is_valid_auto(d28e, None) and not auto_approval.is_valid_auto(d28e, "high"), "")
            case(f"V02 additive 무효 {len(bad)}가지 → False", not wrong, f"유효로 판정됨: {wrong}")
            case("V03 kpi.is_valid_auto 는 auto_approval.is_valid_auto 와 같은 함수 객체",
                 getattr(kpi, "is_valid_auto", None) is auto_approval.is_valid_auto, "")
            # V04: step.py 의 실제 H3 경로가 공통 함수를 부르는지 — 공통 함수를 '항상 참' 으로 바꾸면 무효 이벤트도 H3 가 된다
            q = make_project(tmp, "v04")
            write(q, "config/slices.toml", SLICES)
            commit_all(q, "slices")
            put_events(q, [cls(), {"kind": "auto_approval", "wo": WO, "policy": "junk"}])
            probe = ("import sys, runpy; sys.path.insert(0, 'harness'); import auto_approval; "
                     "auto_approval.is_valid_auto = lambda e, r: e.get('policy') == 'junk'; "
                     "sys.argv = ['step.py', '--slice', 'WO-51', '--json']; runpy.run_path('harness/step.py', run_name='__main__')")
            r = subprocess.run([PY, "-c", probe], cwd=q, capture_output=True, text=True, encoding="utf-8")
            try:
                h3 = json.loads(r.stdout).get("evidence", {}).get("has_h3")
            except Exception:
                h3 = "parse-fail " + r.stdout[-80:] + r.stderr[-120:]
            case("V04 step.py H3 판정은 auto_approval.is_valid_auto 를 부른다(바꿔치기하면 has_h3 가 따라 참)", h3 is True, repr(h3))

            def h3(evs: list[dict], name: str) -> object:
                """step.py --check delegate 의 has_h3 와 D4 유무를 함께 본다(안내 모드는 blockers 를 비워 둔다)."""
                q = make_project(tmp, name)
                write(q, "config/slices.toml", SLICES)
                commit_all(q, "slices")
                put_events(q, evs)
                r = subprocess.run([PY, "harness/step.py", "--slice", WO, "--json", "--check", "delegate"], cwd=q,
                                   capture_output=True, text=True, encoding="utf-8")
                try:
                    d = json.loads(r.stdout)
                    has = d.get("evidence", {}).get("has_h3")
                    d4 = any(b.startswith("D4") for b in d.get("blockers", []))
                    return (has, d4)
                except Exception:
                    return "parse-fail " + r.stdout[-80:] + r.stderr[-80:]

            v = h3([cls(), good], "v05")
            case("V05 step.py: 유효한 additive → has_h3 참 · D4 없음", v == (True, False), repr(v))
            v = h3([cls(), dict(good, review_note=" ")], "v06")
            case("V06 step.py: 무효 additive(review_note 공백) → has_h3 거짓 · D4", v == (False, True), repr(v))
            v = h3([cls("high"), good], "v07")
            case("V07 step.py: 위험도 high → additive 도 has_h3 거짓 · D4", v == (False, True), repr(v))
            v = h3([cls(), d28e], "v08")
            case("V08 step.py: 기존 D-28 유효 이벤트 → has_h3 참 · D4 없음(기존 그대로)", v == (True, False), repr(v))

        # ── R: run_gates 변이 집계 ────────────────────────────────────────────────
        sys.path[:0] = [str(ROOT / "harness"), str(ROOT)]
        import run_gates  # noqa: E402
        from adapters.python import adapter  # noqa: E402
        gate = {"id": "G6", "name": "mutation", "kind": "mutation", "run": ["mutmut", "run"],
                "stats": ["mutmut", "export-cicd-stats"], "stats_file": "stats.json", "min_score": 0.5}
        with tempfile.TemporaryDirectory(prefix="h80-mut-") as mt:
            mroot = Path(mt)
            (mroot / "stats.json").write_text(json.dumps({"total": 4, "killed": 3, "survived": 1, "timeout": 0, "no_tests": 0}),
                                              encoding="utf-8")
            with patch.object(adapter, "ROOT", mroot), patch.object(run_gates, "ADAPTER", adapter), \
                    patch.object(run_gates, "run", return_value=(0, "")):
                out = run_gates.mutation_gate(gate)
                last = getattr(run_gates, "LAST_MUTATION", "missing")
            case("R01 mutation_gate 반환은 그대로 (통과, detail) 두 값", isinstance(out, tuple) and len(out) == 2, repr(out)[:120])
            case("R02 LAST_MUTATION = 여섯 필드 int", last == {"total": 4, "killed": 3, "survived": 1, "timeout": 0,
                                                            "no_tests": 0, "excluded": 0}
                 and all(type(v) is int for v in (last.values() if isinstance(last, dict) else [])), repr(last))
            (mroot / "stats.json").write_text(json.dumps({"total": 9, "killed": 5, "survived": 3, "timeout": 1, "no_tests": 0}),
                                              encoding="utf-8")
            with patch.object(adapter, "ROOT", mroot), patch.object(run_gates, "ADAPTER", adapter), \
                    patch.object(run_gates, "run", return_value=(0, "")), \
                    patch.object(run_gates, "equivalent_survivors", return_value=[("m1", "raise-message")]):
                run_gates.mutation_gate(dict(gate, equivalent_rules=["raise-message"]))
                last2 = getattr(run_gates, "LAST_MUTATION", "missing")
            case("R02b 다른 집계 · 등가 제외 1 → 그 값 그대로(상수 아님, excluded = 제외 수)",
                 last2 == {"total": 9, "killed": 5, "survived": 3, "timeout": 1, "no_tests": 0, "excluded": 1}, repr(last2))
            # 경계 사례 설계 R01: 정수가 아닌 집계(실수 · bool)는 잘라 저장하지 않는다 — 집계 없음(None)
            bad_counts = {}
            for label, counts in (("실수", {"total": 10, "killed": 8.9, "survived": 1.1, "timeout": 0, "no_tests": 0}),
                                  ("bool", {"total": 10, "killed": True, "survived": 1, "timeout": 0, "no_tests": 0})):
                (mroot / "stats.json").write_text(json.dumps(counts), encoding="utf-8")
                with patch.object(adapter, "ROOT", mroot), patch.object(run_gates, "ADAPTER", adapter), \
                        patch.object(run_gates, "run", return_value=(0, "")):
                    run_gates.mutation_gate(gate)
                    bad_counts[label] = getattr(run_gates, "LAST_MUTATION", "missing")
            case("R08 어댑터 집계가 정수가 아니면(실수 · bool) LAST_MUTATION = None (잘라 저장하지 않음)",
                 all(v is None for v in bad_counts.values()), repr(bad_counts))
        with patch.object(run_gates, "ADAPTER") as fake:
            fake.mutation_config_mismatch.return_value = None
            fake.run_mutation.return_value = {"error": "tool missing"}
            run_gates.LAST_MUTATION = {"stale": 1}
            run_gates.mutation_gate(gate)
            after_err = getattr(run_gates, "LAST_MUTATION", "missing")
        case("R03 어댑터 오류면 LAST_MUTATION = None", after_err is None, repr(after_err))
        with patch.object(run_gates, "ADAPTER") as fake:
            fake.mutation_config_mismatch.return_value = "설정 불일치"
            run_gates.LAST_MUTATION = {"stale": 1}
            run_gates.mutation_gate(dict(gate, expected_tool_config={"x": 1}))
            after_mis = getattr(run_gates, "LAST_MUTATION", "missing")
        case("R04 설정 불일치 조기 반환에서도 LAST_MUTATION = None", after_mis is None, repr(after_mis))
        gr = getattr(run_gates, "gate_result", None)
        if gr is None:
            case("R05 run_gates.gate_result 함수", False, "없음")
        else:
            counts = {"total": 7, "killed": 4, "survived": 2, "timeout": 0, "no_tests": 0, "excluded": 1}
            run_gates.LAST_MUTATION = dict(counts)
            m = gr(gate, False, 1.26, "d")
            run_gates.LAST_MUTATION["killed"] = 99
            plain = gr({"id": "G2", "name": "tests"}, True, 0.04, "x")
            run_gates.LAST_MUTATION = None
            m_none = gr(gate, True, 0.0, "y")
            case("R05 gate_result: 기존 다섯 키 그대로(seconds 소수 첫째 자리)",
                 {k: m.get(k) for k in ("id", "name", "passed", "seconds", "detail")}
                 == {"id": "G6", "name": "mutation", "passed": False, "seconds": 1.3, "detail": "d"}
                 and plain == {"id": "G2", "name": "tests", "passed": True, "seconds": 0.0, "detail": "x"}, f"{m} {plain}")
            case("R06 변이 게이트 항목에 mutation 사본(뒤 변경 영향 없음, excluded 포함), 다른 게이트 · LAST_MUTATION None 이면 키 없음",
                 m.get("mutation") == counts and "mutation" not in plain and "mutation" not in m_none, f"{m} {m_none}")
            src = (ROOT / "harness" / "run_gates.py").read_text(encoding="utf-8")
            case("R07 게이트 루프가 gate_result 로 항목을 만든다(results.append(gate_result(…)))",
                 "results.append(gate_result(" in src, "")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    all_good = bool(rows)
    for name, good, detail in rows:
        all_good &= good
        print(f"{'OK ' if good else 'BAD'} {name}  — {detail}")
    print("결과: 모든 사례가 기대와 일치" if all_good else "결과: 기대와 다른 사례가 있다")
    return 0 if all_good else 1


if __name__ == "__main__":
    sys.exit(main())
