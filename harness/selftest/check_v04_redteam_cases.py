"""하네스 자기 검사: core-v0.4 레드팀 (WO-H19, Codex) 에서 확인한 뚫림 · 오탐의 판정 사례 (WO-H20).

원본 공격: dev/redteam/wo-h19_codex_attacks.py · REDTEAM-v04.md. 판정과 선별: dev/review/JUDGE-wo-h19.md.
여기에는 고치기로 한 것만 사례로 둔다 (정책으로 남긴 것 · 실제 경로가 없는 것은 판정 기록에).

- X  G0: 속성(-diff)으로 추가 줄 숨기기, 대문자 확장자, 정상 U+FFFD 이름 오탐, 커밋에 든 변경을 작업 트리로 되돌려 숨기기
- Y  GT 경계 표: 머리보다 많은 칸, 바깥 파이프 없는 표, 이스케이프 파이프 오탐, 파생 표본 판별, '계약 밖' 표기
- Z  피드백 모드: 슬라이스 override 로 종류 · ID 바꾸기, 판정 산출물 경로를 지우는 test_report
- W  KPI 위임 기록: 반복 수는 0 이상의 정수만 (bool · 음수 · 실수 거부)

X05 · Y06 · Z04 · W04 는 지금도 통과해야 하는 회귀 방지 사례다. 나머지는 고치기 전 Red.
사용: python harness/selftest/check_v04_redteam_cases.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import check_g0_paths_cases as gp  # noqa: E402
import check_v04_cases as v4  # noqa: E402
from check_kpi_cases import PY, events, make_project  # noqa: E402
from traceability import check_traceability  # noqa: E402


# ---------- X: G0 ----------

def x_cases(results: list[tuple[str, bool, str]]) -> None:
    repo = gp.make_repo()
    try:
        # 기준에 추적 중인 소스 파일 하나
        gp.reset(repo)
        gp.write(repo, "src/demo/ordinary.py", "value = 1\n")
        gp.git(repo, "add", "-A")
        gp.git(repo, "commit", "-qm", "ordinary source")
        gp.git(repo, "tag", "-f", "base")

        # X01 (P04): 구현자가 허용 경로(src/**) 안에 .gitattributes 로 -diff 를 걸고 추적 파일에 우회 줄
        gp.write(repo, "src/demo/.gitattributes", "*.py -diff\n")
        gp.write(repo, "src/demo/ordinary.py", "value = 1  # pragma: no cover\n")
        ok, bad, files = gp.g0(repo)
        results.append(("X01 src 안 .gitattributes 의 -diff 로 추가 줄 숨기기 → [우회] 를 잡는다",
                        not ok and any("[우회]" in b and "src/demo/ordinary.py" in b for b in bad), "; ".join(bad)[:160] or f"files={files}"))

        # X02 (P05): 대문자 확장자
        gp.reset(repo)
        gp.write(repo, "src/demo/hidden.PY", "value = 1  # pragma: no cover\n")
        ok, bad, files = gp.g0(repo)
        results.append(("X02 대문자 확장자(hidden.PY)의 우회 줄 → [우회] (확장자 비교는 대소문자 무시)",
                        not ok and any("[우회]" in b for b in bad), "; ".join(bad)[:160] or f"files={files}"))

        # X03 (P10 오탐): 올바른 UTF-8 이름에 실제 U+FFFD 문자 → 해석 실패가 아니다
        gp.reset(repo)
        gp.write(repo, "src/demo/real�.py", "value = 1\n")
        ok, bad, files = gp.g0(repo)
        results.append(("X03 올바른 UTF-8 이름의 실제 U+FFFD 문자 → 통과 (오탐 아님)", ok, "; ".join(bad)[:160]))

        # X04 (P03 류): 금지 경로 변경을 커밋하고 작업 트리에서만 되돌림 → 커밋에 든 변경도 잡는다
        gp.reset(repo)
        gp.write(repo, "config/policy.toml", "limit = 999\n")
        gp.git(repo, "add", "-A")
        gp.git(repo, "commit", "-qm", "change protected file")
        gp.git(repo, "show", "base:config/policy.toml")  # 존재 확인
        (repo / "config" / "policy.toml").write_bytes(gp.git(repo, "show", "base:config/policy.toml").encode("utf-8"))
        ok, bad, files = gp.g0(repo)
        results.append(("X04 금지 경로 변경을 커밋 후 작업 트리에서만 되돌림 → config/policy.toml [권한] 위반",
                        not ok and any("[권한]" in b and "config/policy.toml" in b for b in bad), f"files={files} bad={bad[:2]}"))
        gp.git(repo, "reset", "-q", "--hard", "base")

        # X05 (회귀 방지): 허용 소스의 정상 변경은 통과
        gp.reset(repo)
        gp.write(repo, "src/demo/ordinary.py", "value = 2\n")
        ok, bad, files = gp.g0(repo)
        results.append(("X05 허용 소스의 정상 변경 → 통과 (회귀 방지)", ok, "; ".join(bad)[:160]))
    finally:
        shutil.rmtree(repo, ignore_errors=True)


# ---------- Y: GT 경계 표 ----------

def spec_with(table: str) -> str:
    return "# 기능\n\n## Requirements\n\n- **FR-001**: 입력을 검증한다\n\n## Boundary tables\n\n" + table


def gt_with(spec: str, extra_files: dict[str, str] | None = None) -> tuple[bool, list[str], list[str]]:
    root = Path(tempfile.mkdtemp(prefix="v04rt-gt-"))
    try:
        files = {"specs/001-input/spec.md": spec, "reports/decisions.md": v4.DECISIONS, "docs/DEFAULTS.md": v4.DEFAULTS,
                 "tests/unit/test_input.py": v4.TEST, "specs/001-input/samples/original-1.json": "{}\n",
                 **(extra_files or {})}
        for rel, text in files.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text, encoding="utf-8", newline="\n")
        return check_traceability(v4.CFG, root)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def y_cases(results: list[tuple[str, bool, str]]) -> None:
    head = "| 입력 | 정의역 | 경계 | 승격 근거 |\n|:---|:---|:---|:---|\n"

    ok, bad, _ = gt_with(spec_with(head + "| n | 0 ~ | 0 포함 |  | D-01 |\n"))
    results.append(("Y01 머리(4칸)보다 많은 칸의 행 (근거 칸은 비고 5번째 칸에 D-01) → [경계] 실패",
                    any(b.startswith("[경계]") for b in bad), "; ".join(bad)[:160] or "통과"))

    ok, bad, notes = gt_with(spec_with("입력 | 정의역 | 경계 | 승격 근거\n:--- | :--- | :--- | :---\nn | 0 ~ | 0 포함 | [결정 대기]\n"))
    results.append(("Y02 바깥 파이프 없는 표의 [결정 대기] 행 → [경계] 실패 (표를 건너뛰지 않는다)",
                    any(b.startswith("[경계]") for b in bad), "; ".join(bad)[:160] or "통과"))

    ok, bad, _ = gt_with(spec_with(head + "| n | 0 ~ | 0 포함 | 피해: 값 a\\|b 가 섞여 기록이 사라진다 |\n"))
    results.append(("Y03 피해 시나리오 안의 이스케이프 파이프(\\|) → 통과 (칸 구분이 아니다)",
                    not any(b.startswith("[경계]") for b in bad), "; ".join(bad)[:160]))

    ok, bad, _ = gt_with(spec_with(head + "| a | 0 ~ | 0 포함 | 표본:samples/Derived-2.json |\n"
                                   "| b | 0 ~ | 0 포함 | 표본:samples/derived/x.json |\n"),
                         {"specs/001-input/samples/Derived-2.json": "{}\n", "specs/001-input/samples/derived/x.json": "{}\n"})
    hits = [b for b in bad if b.startswith("[경계]")]
    results.append(("Y04 파생 표본 판별: 대소문자 무시(Derived-*) · samples 아래 경로의 어느 마디든 derived 로 시작 → 두 행 모두 [경계]",
                    len(hits) == 2, "; ".join(hits)[:200] or "통과"))

    ok, bad, _ = gt_with(spec_with(head + "| n | 0 ~ | 0 포함 | 계약 밖 아님 |\n"))
    results.append(("Y05 '계약 밖 아님' → [경계] 실패 (계약 밖은 정확히 '계약 밖' 또는 '계약 밖: <이유>')",
                    any(b.startswith("[경계]") for b in bad), "; ".join(bad)[:160] or "통과"))

    ok, bad, notes = gt_with(spec_with(head + "| a | 0 ~ | 0 포함 | 계약 밖 |\n| b | 0 ~ | 0 포함 | 계약 밖: 실제 입력에 없다 |\n"
                                       "| c | 0 ~ | 0 포함 | 표본:samples/original-1.json |\n"))
    results.append(("Y06 '계약 밖' · '계약 밖: 이유' · 원문 표본 → 통과, 계약 안 1 · 계약 밖 2 (회귀 방지)",
                    not any(b.startswith("[경계]") for b in bad) and "계약 안 1" in " ".join(notes) and "계약 밖 2" in " ".join(notes),
                    ("; ".join(bad) + " | " + " ".join(notes))[:200]))


# ---------- Z: 피드백 모드 ----------

def z_cases(results: list[tuple[str, bool, str]]) -> None:
    base = Path(tempfile.mkdtemp(prefix="v04rt-fb-"))
    try:
        def feedback(proj: Path, *extra: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run([PY, "harness/run_gates.py", "--feedback", "--role", "implementer", "--base", "HEAD",
                                   "--wo", "WO-50", *extra], cwd=proj, capture_output=True, text=True, encoding="utf-8")

        def project(name: str, slices: str | None = None, gates: str = v4.FEEDBACK_ONLY_G2) -> Path:
            p = make_project(base, name)
            (p / "config" / "gates.toml").write_text(gates, encoding="utf-8")
            if slices is not None:
                (p / "config" / "slices.toml").write_text(slices, encoding="utf-8")
            return p

        p = project("ovr-kind", '[slice.S1.override.G2]\nkind = "coverage"\nreport = "reports/coverage.json"\nmin_branch_percent = 100\n')
        r = feedback(p, "--slice", "S1")
        results.append(("Z01 슬라이스 override 가 피드백 게이트(G2)의 kind 를 coverage 로 → 거부 (2), 이벤트 없음",
                        r.returncode == 2 and not events(p), f"exit={r.returncode} {r.stdout[-100:]}"))

        p = project("ovr-id", '[slice.S1.override.G2]\nid = "G3"\n')
        r = feedback(p, "--slice", "S1")
        results.append(("Z02 슬라이스 override 가 피드백 게이트의 id 를 바꿈 → 거부 (2), 이벤트 없음",
                        r.returncode == 2 and not events(p), f"exit={r.returncode} {r.stdout[-100:]}"))

        gates = v4.FEEDBACK_ONLY_G2.replace('cmd = ["python", "-c", "raise SystemExit(1)"]',
                                            'cmd = ["python", "-c", "raise SystemExit(1)"]\ntest_report = "reports/gate_report.json"')
        p = project("alias-report", gates=gates)
        report = p / "reports" / "gate_report.json"
        report.write_text('{"judged": true}\n', encoding="utf-8")
        r = feedback(p)
        results.append(("Z03 피드백 게이트의 test_report 가 판정 보고서 경로 → 거부 (2), 판정 보고서 보존",
                        r.returncode == 2 and report.exists() and report.read_text(encoding="utf-8") == '{"judged": true}\n',
                        f"exit={r.returncode} 보존={report.exists()}"))

        p = project("ovr-ok", '[slice.S1.override.G2]\ncmd = ["python", "-c", "raise SystemExit(0)"]\n')
        r = feedback(p, "--slice", "S1")
        kinds = [e.get("kind") for e in events(p)]
        results.append(("Z04 슬라이스 override 가 명령만 바꿈 → 정상 피드백 (0), feedback_run 1 (회귀 방지)",
                        r.returncode == 0 and kinds == ["feedback_run"], f"exit={r.returncode} {kinds} {r.stderr[-120:]}"))
    finally:
        shutil.rmtree(base, ignore_errors=True)


# ---------- W: KPI 위임 기록 ----------

def w_cases(results: list[tuple[str, bool, str]]) -> None:
    base = Path(tempfile.mkdtemp(prefix="v04rt-kpi-"))
    try:
        from check_kpi_cases import ev, kpi_rows, write_events

        def count(name: str, value: str) -> str | None:
            proj = make_project(base, name)
            wo = proj / "work_orders" / "WO-51.md"
            wo.parent.mkdir(parents=True, exist_ok=True)
            wo.write_text("# WO-51\n\n```yaml\nagent: agy\njob: wo51\n```\n", encoding="utf-8", newline="\n")
            (proj / "config" / "delegation.toml").write_text(
                '[delegation]\nrecord = ".jobs/{job}/meta.json"\nfamily_key = "family"\nfeedback_key = "n"\n', encoding="utf-8")
            rec = proj / ".jobs" / "wo51" / "meta.json"
            rec.parent.mkdir(parents=True, exist_ok=True)
            rec.write_text('{"family": "google", "n": ' + value + "}\n", encoding="utf-8")
            write_events(proj, [ev("2026-10-10T10:00:00Z", "gate_run", wo="WO-51", role="implementer", cause="implementer",
                                   passed=False, only=[], first_failed="G2", g0_violations=0, review_required=[], seconds=1)])
            return kpi_rows(proj).get("WO-51", {}).get("판정 전 피드백 실행 수")

        for cid, value in (("W01 bool(true)", "true"), ("W02 음수(-1)", "-1"), ("W03 실수(1.5)", "1.5")):
            got = count(cid.split()[0], value)
            results.append((f"{cid} 반복 수 → '기록 없음' (0 이상의 정수만)", got == "기록 없음", str(got)))
        got = count("W04", "0")
        results.append(("W04 반복 수 0 → '0' (회귀 방지)", got == "0", str(got)))
    finally:
        shutil.rmtree(base, ignore_errors=True)


def main() -> int:
    results: list[tuple[str, bool, str]] = []
    x_cases(results)
    y_cases(results)
    z_cases(results)
    w_cases(results)
    all_good = True
    for name, good, detail in results:
        all_good &= good
        print(f"{'OK ' if good else 'BAD'} {name}{('  — ' + detail) if detail else ''}")
    print("결과: 모든 사례가 기대와 일치" if all_good else "결과: 기대와 다른 사례가 있다")
    return 0 if all_good else 1


if __name__ == "__main__":
    sys.exit(main())
