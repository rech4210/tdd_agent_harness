"""하네스 자기 검사: 문서 최신화 역할 · 읽기 전용 점검 실행 · 상태 줄 검사 (WO-H48, HD-52, 설계 dev/DESIGN-W5-doc-roles.md).

지시서 §9-7 (점검 Codex 가 읽기 전용을 넘지 않음) · §9-8 (편집 AGY 가 허용 파일만) · §9-9 (lead 의 diff · 정본 검증).

계약
R 역할 (config/roles.toml, 사람 승인 HD-52 — 이 사례는 G0 동작으로 확인한다)
  - doc_editor: docs/HANDOFF.md · reports/** 는 G0 통과. 하네스 소유 docs(예: docs/sdd-integration.md),
    docs/PRODUCT.md · docs/DEFAULTS.md, src/ · tests/ · config/ 는 G0 실패.
  - doc_auditor: 어떤 파일을 바꿔도 G0 실패(reports/ 포함).
  - [separation] pairs 에 ["doc_auditor", "doc_editor"].
A 점검 실행 감싸개 adapters/tools/codex_audit.py (Core 밖, upgrade 로 배포)
  `--workspace <git 작업 사본> --prompt-file <파일> --out <파일> [--model gpt-6-luna] [--effort medium]
   [--cap-seconds 900] [--codex-bin codex] [--print-plan]`
  - 실행 명령 = [<codex-bin>, "exec", "-s", "read-only", "-m", <model>, "-c", 'model_reasoning_effort="<effort>"',
    "-C", <workspace>, "-o", <out>, <프롬프트 파일 내용>]. --codex-bin 이 .py 로 끝나면 앞에 현재 파이썬을 붙인다.
    `--print-plan` 은 {"cmd": [...]} JSON 한 줄을 출력하고 실행하지 않는다(종료 0).
  - 명령에 "-s" "read-only" 가 붙어서 정확히 한 번. "workspace-write" · "danger-full-access" · "--dangerously" ·
    "--full-auto" 는 어디에도 없다. --model · --effort 값이 "-" 로 시작하면 종료 2.
  - 사전 검사 (어기면 종료 2, codex 를 띄우지 않음): workspace 가 git 작업 사본, `git status --porcelain
    --untracked-files=all` 이 비어 있음, --out 이 workspace 밖, --prompt-file 이 있고 비어 있지 않음.
  - 실행 뒤: 작업 사본 상태가 비어 있지 않으면 종료 1 + "작업 사본 변경" 과 바뀐 경로. --out 이 없거나 비면 종료 1 +
    "결과 없음". codex 종료 코드가 0 이 아니면 종료 1. 상한을 넘기면 **자식 프로세스 트리 전체**를 끝내고 종료 3 +
    "상한". 상한이어도 실행 뒤 작업 사본 검사는 하며, 바뀌었으면 종료 1(“작업 사본 변경”)이 우선한다. 그 밖은 종료 0.
  - (B, Sol 품질 검토 2회차 수용) 변경 검사는 무시 목록 파일 · 하위 모듈(ignore 설정 무시)까지 포함하고, 실행 뒤 git
    상태 확인이 실패하면 종료 1. skip-worktree · assume-unchanged 플래그가 있는 작업 사본 · 이미 있는 --out ·
    --cap-seconds ≤ 0 · 실행 못 하는 --codex-bin 은 종료 2(실행 안 함, traceback 없음). --effort 는 low · medium ·
    high · xhigh, --model 은 [A-Za-z0-9._-]+ 만. 상한에서 트리 종료가 실패해도 정해진 시간 안에 직접 자식을 끝내고 그 사실을 알린다.
H 상태 줄 검사 adapters/tools/check_handoff_state.py
  - (B) --file 은 저장소 상대 경로로 정규화(절대 경로 · 대소문자 다른 표기도 같은 파일). 상태 줄 인식은 첫 줄 BOM,
    앞의 `> ` · `- ` · `* `, 감싼 `**`, 전각 콜론 `：` 을 걷어낸 뒤 `상태:` 로 시작하는 줄. 코드 블록(``` 울타리) 안은 비교하지 않는다.
  `--base <rev> [--file docs/HANDOFF.md]`: 그 파일에서 `상태:` 로 시작하는 줄들(앞 공백 무시)을 기준 커밋 판과
  작업 트리 판에서 비교. 같으면 종료 0, 다르면 종료 1 + 두 판의 줄을 출력. 양쪽 모두 파일이 없으면 0. 기준 rev 를
  못 찾으면 종료 2. 읽기만 한다.

사용: python harness/selftest/check_doc_roles_cases.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_kpi_cases import PY, ROOT, make_project  # noqa: E402

FAKE = r'''import os, sys, time, pathlib
a = sys.argv[1:]
out = a[a.index("-o") + 1]; ws = a[a.index("-C") + 1]
mode = os.environ.get("FAKE_MODE", "ok")
if mode == "spawn":
    import subprocess
    code = "import time, pathlib, sys; time.sleep(8); pathlib.Path(sys.argv[1]).write_text('late')"
    subprocess.Popen([sys.executable, "-c", code, os.environ["FAKE_MARKER"]])
    time.sleep(30)
if mode == "touchsleep":
    pathlib.Path(ws, "docs").mkdir(exist_ok=True)
    pathlib.Path(ws, "docs", "note.md").write_text("x", encoding="utf-8")
    time.sleep(30)
if mode == "sleep":
    time.sleep(30)
if mode == "fail":
    sys.exit(5)
if mode == "touch":
    pathlib.Path(ws, "docs", "note.md").parent.mkdir(exist_ok=True)
    pathlib.Path(ws, "docs", "note.md").write_text("x", encoding="utf-8")
if mode != "noout":
    pathlib.Path(out).write_text("| 문서 | 낡은 곳 |\n", encoding="utf-8")
'''


def git(p: Path, *a: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *a], cwd=p, capture_output=True, text=True, encoding="utf-8", errors="replace")


def commit_all(p: Path, msg: str) -> None:
    git(p, "add", "-A")
    git(p, "commit", "-qm", msg)


def g0(p: Path, role: str, rel: str) -> int:
    f = p / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("changed\n", encoding="utf-8")
    commit_all(p, f"change {rel}")
    r = subprocess.run([PY, "harness/check_protected_paths.py", "--base", "HEAD~1", "--role", role], cwd=p,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    git(p, "reset", "-q", "--hard", "HEAD~1")
    return r.returncode


def tool(p: Path, name: str, *a: str, env: dict | None = None, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run([PY, f"adapters/tools/{name}", *a], cwd=p, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env={**os.environ, **(env or {})}, timeout=timeout)


def main() -> int:
    results: list[tuple[str, bool, str]] = []

    def case(name: str, good: bool, detail: str = "") -> None:
        results.append((name, good, detail))

    base = Path(tempfile.mkdtemp(prefix="docroles-"))
    try:
        # ── R: 역할 (G0 동작) ──
        p = make_project(base, "proj")
        shutil.copy(ROOT / "config" / "roles.toml", p / "config" / "roles.toml")
        (p / ".gitignore").write_text("__pycache__/\n*.pyc\n", encoding="utf-8")
        (p / "docs").mkdir(exist_ok=True)
        for d in ("HANDOFF.md", "PRODUCT.md", "DEFAULTS.md", "sdd-integration.md"):
            (p / "docs" / d).write_text("상태: 1단계\n", encoding="utf-8")
        commit_all(p, "docs")
        ok_paths = ["docs/HANDOFF.md", "reports/audit.md"]
        bad_paths = ["docs/sdd-integration.md", "docs/PRODUCT.md", "docs/DEFAULTS.md", "src/p/a.py", "tests/unit/t.py",
                     "config/gates.toml"]
        got_ok = {x: g0(p, "doc_editor", x) for x in ok_paths}
        got_bad = {x: g0(p, "doc_editor", x) for x in bad_paths}
        case("R01 doc_editor: HANDOFF · reports 는 G0 통과", all(c == 0 for c in got_ok.values()), str(got_ok))
        case("R02 doc_editor: 하네스 소유 docs · PRODUCT · DEFAULTS · src · tests · config 는 G0 실패",
             all(c == 1 for c in got_bad.values()), str(got_bad))
        got_a = {x: g0(p, "doc_auditor", x) for x in ["reports/audit.md", "docs/HANDOFF.md"]}
        case("R03 doc_auditor: reports 포함 어떤 변경도 G0 실패", all(c == 1 for c in got_a.values()), str(got_a))
        pairs = tomllib.loads((ROOT / "config" / "roles.toml").read_text(encoding="utf-8")).get("separation", {}).get("pairs", [])
        case("R04 분리 쌍 [doc_auditor, doc_editor]", ["doc_auditor", "doc_editor"] in pairs, str(pairs[-2:]))

        # ── A: 점검 실행 감싸개 ──
        has_a = (p / "adapters" / "tools" / "codex_audit.py").exists()
        ws = base / "ws"
        ws.mkdir()
        for c in (["init", "-q", "-b", "main"], ["config", "user.email", "t@example.invalid"], ["config", "user.name", "t"]):
            git(ws, *c)
        (ws / "README.md").write_text("x\n", encoding="utf-8")
        commit_all(ws, "init")
        out_dir = base / "out"
        out_dir.mkdir()
        prompt = base / "prompt.md"
        prompt.write_text("최근 커밋과 문서를 대조해 낡은 곳을 표로", encoding="utf-8")
        fake = base / "fake_codex.py"
        fake.write_text(FAKE, encoding="utf-8")
        common = ["--workspace", str(ws), "--prompt-file", str(prompt), "--codex-bin", str(fake)]

        r = tool(p, "codex_audit.py", *common, "--out", str(out_dir / "plan.md"), "--print-plan") if has_a else None
        try:
            cmd = json.loads(r.stdout.strip().splitlines()[-1])["cmd"] if r and r.returncode == 0 else []
        except Exception:
            cmd = []
        idx = [i for i, x in enumerate(cmd) if x == "-s"]
        joined = " ".join(cmd)
        case("A01 --print-plan: exec · -s read-only 정확히 한 번 · -m gpt-6-luna · -C · -o · 프롬프트 내용, 실행 안 함",
             has_a and len(idx) == 1 and cmd[idx[0] + 1] == "read-only" and "exec" in cmd and "-m" in cmd
             and cmd[cmd.index("-m") + 1] == "gpt-6-luna" and str(ws) in cmd and str(out_dir / "plan.md") in cmd
             and cmd[-1] == prompt.read_text(encoding="utf-8") and not (out_dir / "plan.md").exists(), str(cmd)[:300])
        case("A02 금지 인자 없음 (workspace-write · danger-full-access · --dangerously · --full-auto)",
             bool(cmd) and not any(b in joined for b in ("workspace-write", "danger-full-access", "--dangerously", "--full-auto")), joined[:200])
        r_inj = tool(p, "codex_audit.py", *common, "--out", str(out_dir / "i.md"), "--model", "--dangerously-bypass-approvals-and-sandbox") if has_a else None
        r_inj2 = tool(p, "codex_audit.py", *common, "--out", str(out_dir / "i.md"), "--effort", "-s") if has_a else None
        case("A03 --model · --effort 가 '-' 로 시작 → 종료 2", bool(r_inj) and r_inj.returncode == 2 and r_inj2.returncode == 2,
             f"{r_inj and r_inj.returncode} {r_inj2 and r_inj2.returncode}")

        r_ok = tool(p, "codex_audit.py", *common, "--out", str(out_dir / "ok.md")) if has_a else None
        case("A04 정상: 결과 파일 있음 · 작업 사본 변경 0 → 종료 0",
             bool(r_ok) and r_ok.returncode == 0 and (out_dir / "ok.md").is_file(), f"{r_ok and r_ok.returncode} {r_ok and r_ok.stderr[-200:]}")
        r_t = tool(p, "codex_audit.py", *common, "--out", str(out_dir / "t.md"), env={"FAKE_MODE": "touch"}) if has_a else None
        case("A05 점검 중 작업 사본에 파일이 생김 → 종료 1 · '작업 사본 변경' · 경로 표시",
             bool(r_t) and r_t.returncode == 1 and "작업 사본 변경" in (r_t.stdout + r_t.stderr) and "note.md" in (r_t.stdout + r_t.stderr),
             f"{r_t and r_t.returncode} {r_t and (r_t.stdout + r_t.stderr)[-200:]}")
        r_pre = tool(p, "codex_audit.py", *common, "--out", str(out_dir / "pre.md")) if has_a else None
        case("A06 작업 사본이 이미 더러우면(앞 사례가 남긴 파일) → 종료 2 · codex 실행 안 함 (결과 파일 없음)",
             bool(r_pre) and r_pre.returncode == 2 and not (out_dir / "pre.md").exists(), f"{r_pre and r_pre.returncode}")
        shutil.rmtree(ws / "docs", ignore_errors=True)
        r_n = tool(p, "codex_audit.py", *common, "--out", str(out_dir / "n.md"), env={"FAKE_MODE": "noout"}) if has_a else None
        r_f = tool(p, "codex_audit.py", *common, "--out", str(out_dir / "f.md"), env={"FAKE_MODE": "fail"}) if has_a else None
        case("A07 결과 없음 → 종료 1 '결과 없음', codex 실패 종료 → 종료 1",
             bool(r_n) and r_n.returncode == 1 and "결과 없음" in (r_n.stdout + r_n.stderr) and r_f.returncode == 1,
             f"{r_n and r_n.returncode} {r_f and r_f.returncode}")
        r_c = tool(p, "codex_audit.py", *common, "--out", str(out_dir / "c.md"), "--cap-seconds", "3", env={"FAKE_MODE": "sleep"}) if has_a else None
        case("A08 상한 초과 → 프로세스 끝냄 · 종료 3 · '상한'", bool(r_c) and r_c.returncode == 3 and "상한" in (r_c.stdout + r_c.stderr),
             f"{r_c and r_c.returncode}")
        # A10 (리드 관찰 L1): 상한에 걸려도 실행 뒤 작업 사본 검사를 한다 — 바뀌었으면 종료 1 · '작업 사본 변경'
        r_ct = tool(p, "codex_audit.py", *common, "--out", str(out_dir / "ct.md"), "--cap-seconds", "3",
                    env={"FAKE_MODE": "touchsleep"}) if has_a else None
        case("A10 상한 초과 전에 작업 사본을 바꿈 → 종료 1 · '작업 사본 변경' (상한 종료 3 으로 숨기지 않음)",
             bool(r_ct) and r_ct.returncode == 1 and "작업 사본 변경" in (r_ct.stdout + r_ct.stderr), f"{r_ct and r_ct.returncode}")
        shutil.rmtree(ws / "docs", ignore_errors=True)
        # A11 (리드 관찰 L2): 상한이면 자식의 자식 프로세스까지 끝낸다
        marker = out_dir / "late.txt"
        r_sp = tool(p, "codex_audit.py", *common, "--out", str(out_dir / "sp.md"), "--cap-seconds", "3",
                    env={"FAKE_MODE": "spawn", "FAKE_MARKER": str(marker)}) if has_a else None
        __import__("time").sleep(12)
        case("A11 상한 초과 → 손자 프로세스도 끝남 (12초 뒤 늦은 쓰기 없음) · 종료 3",
             bool(r_sp) and r_sp.returncode == 3 and not marker.exists(), f"{r_sp and r_sp.returncode} marker={marker.exists()}")
        r_in = tool(p, "codex_audit.py", *common, "--out", str(ws / "inside.md")) if has_a else None
        notgit = base / "notgit"
        notgit.mkdir()
        r_ng = tool(p, "codex_audit.py", "--workspace", str(notgit), "--prompt-file", str(prompt), "--codex-bin", str(fake),
                    "--out", str(out_dir / "ng.md")) if has_a else None
        empty = base / "empty.md"
        empty.write_text("  \n", encoding="utf-8")
        r_ep = tool(p, "codex_audit.py", "--workspace", str(ws), "--prompt-file", str(empty), "--codex-bin", str(fake),
                    "--out", str(out_dir / "ep.md")) if has_a else None
        case("A09 사전 검사: --out 이 작업 사본 안 · git 아님 · 빈 프롬프트 → 종료 2, 실행 안 함",
             bool(r_in) and r_in.returncode == 2 and not (ws / "inside.md").exists() and r_ng.returncode == 2 and r_ep.returncode == 2,
             f"{r_in and r_in.returncode} {r_ng and r_ng.returncode} {r_ep and r_ep.returncode}")

        # ── H: 상태 줄 검사 ──
        has_h = (p / "adapters" / "tools" / "check_handoff_state.py").exists()
        h = p / "docs" / "HANDOFF.md"
        h.write_text("# 인계\n\n상태: 1단계\n\n## 2. 지금\n- a\n", encoding="utf-8")
        commit_all(p, "handoff")
        h.write_text("# 인계\n\n상태: 1단계\n\n## 2. 지금\n- a\n- b (편집)\n", encoding="utf-8")
        r1 = tool(p, "check_handoff_state.py", "--base", "HEAD") if has_h else None
        h.write_text("# 인계\n\n  상태: 2단계\n\n## 2. 지금\n- a\n", encoding="utf-8")
        r2 = tool(p, "check_handoff_state.py", "--base", "HEAD") if has_h else None
        case("H01 본문만 바뀜 → 종료 0", bool(r1) and r1.returncode == 0, f"{r1 and r1.returncode}")
        case("H02 상태 줄 바뀜(앞 공백 포함) → 종료 1 · 두 판 줄 출력",
             bool(r2) and r2.returncode == 1 and "1단계" in r2.stdout + r2.stderr and "2단계" in r2.stdout + r2.stderr,
             f"{r2 and r2.returncode}")
        r3 = tool(p, "check_handoff_state.py", "--base", "nope-rev") if has_h else None
        git(p, "checkout", "-q", "--", "docs/HANDOFF.md")
        r4 = tool(p, "check_handoff_state.py", "--base", "HEAD", "--file", "docs/none.md") if has_h else None
        h.write_text("# 인계\n\n상태: 1단계\n상태: 숨은 줄\n", encoding="utf-8")
        r5 = tool(p, "check_handoff_state.py", "--base", "HEAD") if has_h else None
        case("H03 기준 rev 없음 → 2, 양쪽 파일 없음 → 0, 상태 줄 추가(여러 줄) → 1",
             bool(r3) and r3.returncode == 2 and r4.returncode == 0 and r5.returncode == 1,
             f"{r3 and r3.returncode} {r4 and r4.returncode} {r5 and r5.returncode}")
        case("H04 검사 도구는 아무것도 쓰지 않음 (git status 의 HANDOFF 외 변경 0)",
             has_h and [l for l in git(p, "status", "--porcelain").stdout.splitlines() if "HANDOFF" not in l and "reports/" not in l] == [],
             git(p, "status", "--porcelain").stdout[:200])

        # ── B: Codex Sol 품질 검토(2회차) 지적 — 리드 재현 뒤 수용 ──
        def mk_ws(name: str) -> Path:
            w = base / name
            w.mkdir()
            for c in (["init", "-q", "-b", "main"], ["config", "user.email", "t@example.invalid"], ["config", "user.name", "t"]):
                git(w, *c)
            (w / "README.md").write_text("x\n", encoding="utf-8")
            commit_all(w, "init")
            return w

        def audit(w: Path, out: str, *extra: str, mode: str = "ok") -> subprocess.CompletedProcess[str]:
            return tool(p, "codex_audit.py", "--workspace", str(w), "--prompt-file", str(prompt), "--codex-bin", str(fake),
                        "--out", str(out_dir / out), *extra, env={"FAKE_MODE": mode})

        if has_a:
            w = mk_ws("ws-ignored")
            (w / ".gitignore").write_text("docs/note.md\n", encoding="utf-8")
            commit_all(w, "ignore")
            r = audit(w, "b01.md", mode="touch")
            case("B01 무시 목록에 든 파일이 새로 생김 → 종료 1 · '작업 사본 변경'",
                 r.returncode == 1 and "작업 사본 변경" in r.stdout + r.stderr, f"{r.returncode}")
            w = mk_ws("ws-skip")
            (w / "docs").mkdir()
            (w / "docs" / "note.md").write_text("before\n", encoding="utf-8")
            commit_all(w, "note")
            git(w, "update-index", "--skip-worktree", "docs/note.md")
            r1 = audit(w, "b02a.md", mode="touch")
            git(w, "update-index", "--no-skip-worktree", "docs/note.md")
            git(w, "checkout", "--", "docs/note.md")
            git(w, "update-index", "--assume-unchanged", "docs/note.md")
            r2 = audit(w, "b02b.md", mode="touch")
            case("B02 skip-worktree · assume-unchanged 플래그가 있는 작업 사본 → 종료 2 (실행 전 거부)",
                 r1.returncode == 2 and r2.returncode == 2 and not (out_dir / "b02a.md").exists(), f"{r1.returncode} {r2.returncode}")
            sub = mk_ws("ws-sub-src")
            (sub / "note.md").write_text("old\n", encoding="utf-8")
            commit_all(sub, "sub")
            w = mk_ws("ws-sub")
            git(w, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(sub), "docs")
            git(w, "config", "-f", ".gitmodules", "submodule.docs.ignore", "all")
            commit_all(w, "submodule")
            r = audit(w, "b03.md", mode="touch")
            case("B03 하위 모듈(ignore=all) 안에서 파일이 생김 → 종료 1", r.returncode == 1, f"{r.returncode}")
            r = audit(mk_ws("ws-stale"), "b04.md")
            r2 = audit(mk_ws("ws-stale2"), "b04.md")
            case("B04 --out 이 이미 있음(이전 실행 결과) → 종료 2 · codex 실행 안 함",
                 r.returncode == 0 and r2.returncode == 2, f"{r.returncode} {r2.returncode}")
            w = mk_ws("ws-cap")
            rc0 = audit(w, "b05a.md", "--cap-seconds", "0").returncode
            rcn = audit(w, "b05b.md", "--cap-seconds", "-1").returncode
            case("B05 --cap-seconds 0 · 음수 → 종료 2", rc0 == 2 and rcn == 2, f"{rc0} {rcn}")
            r = tool(p, "codex_audit.py", "--workspace", str(w), "--prompt-file", str(prompt),
                     "--codex-bin", str(base / "missing.exe"), "--out", str(out_dir / "b06.md"))
            case("B06 없는 실행 파일 → 종료 2 · Traceback 없음", r.returncode == 2 and "Traceback" not in r.stderr, f"{r.returncode}")
            bad_vals = [("--effort", 'medium"\nsandbox_mode="workspace'), ("--effort", "extreme"), ("--model", 'gpt-6-luna"'),
                        ("--model", "a b")]
            codes_v = [audit(w, "b07.md", k, v, "--print-plan").returncode for k, v in bad_vals]
            ok_e = [audit(w, "b07.md", "--effort", e, "--print-plan").returncode for e in ("low", "medium", "high", "xhigh")]
            case("B07 effort 는 low · medium · high · xhigh 만, model 은 영문자 · 숫자 · . _ - 만 (그 밖은 종료 2)",
                 codes_v == [2, 2, 2, 2] and ok_e == [0, 0, 0, 0], f"{codes_v} {ok_e}")
            w = mk_ws("ws-lnk")
            lnk = out_dir / "b08.md"
            try:
                os.link(w / ".git" / "config", lnk)
                linked = True
            except OSError:
                linked = False
            r = audit(w, "b08.md")
            ok_cfg = git(w, "status").returncode
            case("B08 결과 파일이 작업 사본 .git 파일의 하드 링크 → 성공(0)으로 끝나지 않음",
                 (not linked) or r.returncode != 0, f"linked={linked} rc={r.returncode} git={ok_cfg}")

        if has_h:
            q = make_project(base, "state2")
            (q / "docs").mkdir(exist_ok=True)
            hq = q / "docs" / "HANDOFF.md"

            def state_case(before: str, after: str, *extra: str) -> int:
                hq.write_bytes(before.encode("utf-8"))
                commit_all(q, "h")
                hq.write_bytes(after.encode("utf-8"))
                rc = tool(q, "check_handoff_state.py", "--base", "HEAD", *extra).returncode
                git(q, "checkout", "--", "docs/HANDOFF.md")
                return rc

            same = "# 인계\n\n상태: old\n"
            r_abs = state_case(same, same, "--file", str(hq))
            r_case = state_case(same, same, "--file", "DOCS/handoff.md")
            case("B09 같은 내용 · 절대 경로 또는 대소문자만 다른 경로 → 종료 0 (오탐 아님)", r_abs == 0 and r_case == 0, f"{r_abs} {r_case}")
            marks = [state_case(f"{pre}상태:{post} old\n", f"{pre}상태:{post} new\n")
                     for pre, post in (("**", "**"), ("> ", ""), ("- ", ""))]
            case("B10 강조 · 인용 · 목록 표기의 상태 줄이 바뀜 → 종료 1", marks == [1, 1, 1], str(marks))
            wide = state_case("상태： old\n", "상태： new\n")
            bom = state_case("﻿상태: old\n", "﻿상태: new\n")
            case("B11 전각 콜론 · 첫 줄 BOM 의 상태 줄이 바뀜 → 종료 1", wide == 1 and bom == 1, f"{wide} {bom}")
            fence = state_case("상태: s\n\n```text\n상태: old\n```\n", "상태: s\n\n```text\n상태: new\n```\n")
            case("B12 코드 블록 안 예시만 바뀜 → 종료 0 (본문 상태 줄만 비교)", fence == 0, str(fence))
    finally:
        shutil.rmtree(base, ignore_errors=True)

    all_good = True
    for name, good, detail in results:
        all_good &= good
        print(f"{'OK ' if good else 'BAD'} {name}{('  — ' + detail) if detail and not good else ''}")
    print("결과: 모든 사례가 기대와 일치" if all_good else "결과: 기대와 다른 사례가 있다")
    return 0 if all_good else 1


if __name__ == "__main__":
    sys.exit(main())
