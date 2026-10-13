"""게이트 실행기 (Core): config/gates.toml 의 게이트를 순서대로 돌리고 결과를 기록한다.

Core 는 언어 · 도구를 모른다. 도구 실행과 결과 해석은 어댑터(adapters/<이름>/adapter.py)가 하고,
Core 는 정규화된 결과에 판정 규칙(임계값, 분기 없는 파일 처리, 등가 제외 집계, SKIP)을 적용한다.

모델 호출 없음. 판정은 전부 결정적인 스크립트와 도구가 한다 (원칙 P5).
에이전트의 보고서가 아니라 이 실행기가 쓴 reports/gate_report.json 이 병합 판단의 근거다.

사용:
  python harness/run_gates.py --role implementer --base main
  python harness/run_gates.py --role implementer --base main --only G0,G1,G2
  python harness/run_gates.py ... --keep-going     # 실패해도 끝까지 (진단용)
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path

from adapter_loader import load_adapter
from events import log_event, now_utc
from arch import check_architecture
from paths import ARCHITECTURE, GATES, ROOT, SLICES
from traceability import check_traceability

REPORT = ROOT / "reports" / "gate_report.json"
RUNS = ROOT / "reports" / "runs"  # 실행마다 사본. 재시도 횟수 · 첫 통과 여부의 원천

ADAPTER = load_adapter()

# gates.toml [env]: 모든 게이트 하위 프로세스에 강제로 넘기는 환경변수.
# 예: TZ 를 서비스 시간대와 다르게 고정해, 실행 기계의 현지 시간대에 기대는 코드를 드러낸다
GATE_ENV: dict[str, str] = {}


def _is_protected_report(path_str: str) -> bool:
    try:
        p = (ROOT / path_str).resolve()
        gate_rep = (ROOT / "reports" / "gate_report.json").resolve()
        runs_dir = (ROOT / "reports" / "runs").resolve()
        return p == gate_rep or p == runs_dir or p.is_relative_to(runs_dir)
    except Exception:
        return False


def proc_env() -> dict[str, str]:
    return {**os.environ, **GATE_ENV}


def resolve(cmd: list[str]) -> list[str]:
    """C9: Core 자신의 스크립트(harness/*.py)는 Core 를 돌리는 인터프리터로 실행한다. 나머지는 어댑터가 푼다."""
    if len(cmd) > 1 and cmd[0] == "python" and cmd[1].replace("\\", "/").startswith("harness/"):
        return [sys.executable, *cmd[1:]]
    return ADAPTER.resolve_command(cmd)


def run(cmd: list[str]) -> tuple[int, str]:
    cmd = resolve(cmd)
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", env=proc_env())
    tail = (p.stdout + p.stderr).strip().splitlines()[-30:]
    return p.returncode, "\n".join(tail)


def run_full(cmd: list[str]) -> str:
    """출력 전체가 필요할 때 (run 은 마지막 30줄만 남긴다)."""
    p = subprocess.run(
        resolve(cmd), cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=proc_env(),
    )
    return p.stdout


def coverage_gate(gate: dict) -> tuple[bool, str]:
    path = ROOT / gate["report"]
    if not path.exists():
        return False, f"커버리지 보고서 없음: {gate['report']} (G2 가 먼저 돌아야 한다)"
    files = ADAPTER.read_coverage(path)  # 정규화: {파일: branches_total · branches_covered · lines_total · lines_covered}
    lines, ok = [], True
    for prefix, minimum in gate["min_branch_percent"].items():
        total = covered = stmts = run_stmts = 0
        matched = False
        pref_boundary = prefix.rstrip("/") + "/"
        for name, data in files.items():
            if name == prefix or name.startswith(pref_boundary):
                matched = True
                total += data["branches_total"]
                covered += data["branches_covered"]
                stmts += data["lines_total"]
                run_stmts += data["lines_covered"]
        if not matched:
            ok = False
            lines.append(f"{prefix}: 측정 기록 없음 (파일이 없거나 import 되지 않음) → 실패")
            continue
        if total == 0:
            # 분기가 없는 파일(Enum, 예외 정의 등)은 줄 커버리지로 판정한다. 0줄이면 실패
            if stmts == 0:
                ok = False
                lines.append(f"{prefix}: 분기 0개 · 문장 0개 → 실패")
                continue
            pct = 100.0 * run_stmts / stmts
            passed = pct >= minimum
            ok &= passed
            lines.append(f"{prefix}: 분기 없음, 줄 {pct:.1f}% (기준 {minimum}%) {'통과' if passed else '실패'}")
            continue
        pct = 100.0 * covered / total
        passed = pct >= minimum
        ok &= passed
        lines.append(f"{prefix}: {pct:.1f}% (기준 {minimum}%) {'통과' if passed else '실패'}")
    if "min_function_percent" in gate:
        for prefix, minimum in gate["min_function_percent"].items():
            pref_boundary = prefix.rstrip("/") + "/"
            matched = [(n, d) for n, d in files.items() if n == prefix or n.startswith(pref_boundary)]
            if not matched:
                ok = False
                lines.append(f"{prefix}: 측정 기록 없음 (파일이 없거나 import 되지 않음) → 실패")
                continue
            missing = [n for n, d in matched if "functions_total" not in d]
            if missing:
                ok = False
                lines.append(f"{prefix}: 함수 측정 없음 ({', '.join(missing)}) → 실패")
                continue
            funcs = [d["functions_total"] for _, d in matched]
            if sum(funcs) == 0:
                ok = False
                lines.append(f"{prefix}: 함수 측정 없음 → 실패")
                continue
            f_total = sum(funcs)
            f_cov = sum(d.get("functions_covered", 0) for _, d in matched)
            pct = 100.0 * f_cov / f_total
            passed = pct >= minimum
            ok &= passed
            lines.append(f"{prefix}: 함수 {pct:.1f}% (기준 {minimum}%) {'통과' if passed else '실패'}")
            excluded_fns = []
            for _, d in matched:
                for fn_name in d.get("functions_excluded", []):
                    if fn_name not in excluded_fns:
                        excluded_fns.append(fn_name)
            if excluded_fns:
                lines.append(f"제외로 측정 안 된 함수: {', '.join(excluded_fns)}")
    return ok, "\n".join(lines)


def architecture_gate(gate: dict) -> tuple[bool, str]:
    """K8: 어댑터가 소스에서 사실(import · 사용 이름 · 동적 접근)을 추출하고, Core 가 설정의 레이어 규칙을 적용한다.
    게이트 설정의 키(only_layers, never_import_from, checks 등)는 architecture.toml 의 값을 덮어쓴다 (슬라이스 범위)."""
    if not ARCHITECTURE.exists():
        return False, f"아키텍처 설정 없음: {ARCHITECTURE.relative_to(ROOT).as_posix()}"
    cfg = tomllib.loads(ARCHITECTURE.read_text(encoding="utf-8"))["architecture"]
    cfg = {**cfg, **{k: v for k, v in gate.items() if k in {"only_layers", "never_import_from", "checks"}}}
    src = ROOT / gate.get("src", "src")
    pkg = src / cfg["package"]
    if not pkg.is_dir():
        return False, f"소스 패키지 없음: {pkg.relative_to(ROOT).as_posix()} (Red 상태라면 정상)"
    tops = {d.name for d in pkg.iterdir() if d.is_dir() and not d.name.startswith(("_", "."))}
    ok, bad = check_architecture(cfg, ADAPTER.import_graph(src), tops)
    return ok, (f"위반 {len(bad)}건" + ("\n" + "\n".join(bad[:30]) if bad else ""))


def traceability_gate(gate: dict) -> tuple[bool, str]:
    """SDD 통합: 명세의 요구 ID ↔ 테스트 머리말 guards 양방향 대조, 가정 승격 검사. 설정은 게이트 키 (슬라이스가 in_scope 로 범위를 고른다)."""
    ok, bad, notes = check_traceability(gate, ROOT)
    lines = bad[:40] + notes
    return ok, (f"위반 {len(bad)}건" + ("\n" + "\n".join(lines) if lines else ""))


def mutation_gate(gate: dict) -> tuple[bool, str]:
    if gate.get("expected_tool_config"):
        mismatch = ADAPTER.mutation_config_mismatch(gate["expected_tool_config"])
        if mismatch:
            return False, mismatch
    result = ADAPTER.run_mutation(gate, run, GATE_ENV)
    if "error" in result:
        return False, result["error"]
    s = result["counts"]
    detected = s.get("killed", 0) + s.get("timeout", 0)
    undetected = s.get("survived", 0) + s.get("no_tests", 0)
    excluded = equivalent_survivors(gate.get("equivalent_rules", []))
    undetected -= len(excluded)
    denom = detected + undetected
    total = s.get("total", denom)
    if total > 0 and s.get("killed", 0) == 0 and s.get("survived", 0) == 0 and detected == 0:
        return False, "변이 전 테스트가 실패한다 — G2 를 먼저 통과시킬 것"
    if denom == 0:
        return False, "변이가 0개다. 대상 경로 설정을 확인할 것"
    score = detected / denom
    passed = score >= gate["min_score"]
    lines = [
        f"점수 {score:.3f} (기준 {gate['min_score']}), "
        f"잡힘 {detected}, 살아남음 {s.get('survived', 0) - len(excluded)}, 테스트 없음 {s.get('no_tests', 0)}, "
        f"규칙으로 제외 {len(excluded)}",
        "살아남은 변이 목록은 어댑터의 변이 도구로 보고, 테스트 작성자에게 넘긴다 (구현자 아님)",
    ]
    lines += [f"제외 ({rule}): {mid}" for mid, rule in excluded]
    return passed, "\n".join(lines)


def equivalent_survivors(rules: list[str]) -> list[tuple[str, str]]:
    """사람이 결정한 등가 규칙에 맞는 생존 변이를 찾는다. 규칙이 없으면 아무것도 빼지 않는다.

    규칙의 의미(어떤 diff 가 등가인가)는 어댑터가 판단한다. Core 는 규칙 이름과 제외 집계만 다룬다.
    """
    if not rules:
        return []
    out: list[tuple[str, str]] = []
    for mid in ADAPTER.survivors(run_full, GATE_ENV):
        removed, added = ADAPTER.survivor_diff(mid, run_full, GATE_ENV)
        rule = next((r for r in rules if ADAPTER.equivalent(r, removed, added)), None)
        if rule:
            out.append((mid, rule))
    return out


def test_report_check(gate: dict) -> tuple[bool, str]:
    """C4: 종료 코드만 믿지 않는다. JUnit XML(언어 간 표준 형식)로 테스트 수 · 실패 · 건너뜀을 확인한다.
    테스트 0개, 건너뛴 테스트, 실패가 있으면 실패다 (도구가 종료 코드 0 을 내도)."""
    path = ROOT / gate["test_report"]
    if not path.exists():
        return False, f"테스트 보고서 없음: {gate['test_report']}"
    root = ET.parse(path).getroot()
    # testcase 요소를 직접 센다. 도구마다 testsuite 속성 · 중첩 방식이 달라(도구마다 다름) 속성 합계는 틀릴 수 있다
    cases = list(root.iter("testcase"))
    count = {"tests": len(cases)}
    for tag, key in (("failure", "failures"), ("error", "errors"), ("skipped", "skipped")):
        count[key] = sum(1 for c in cases if c.find(tag) is not None)
    ran = count["tests"] - count["skipped"]
    problems = []
    if count["failures"] or count["errors"]:
        problems.append(f"실패 {count['failures']} · 오류 {count['errors']}")
    if count["skipped"] > gate.get("allow_skipped", 0):
        problems.append(f"건너뛴 테스트 {count['skipped']}개")
    if ran < gate.get("min_tests", 1):
        problems.append(f"실행된 테스트 {ran}개 < 최소 {gate.get('min_tests', 1)}개")
    summary = f"테스트 보고서: 실행 {ran}, 실패 {count['failures']}, 오류 {count['errors']}, 건너뜀 {count['skipped']}"
    return not problems, summary + (" → " + "; ".join(problems) if problems else "")


def _g0_json(g0: dict | None) -> dict:
    # C3: 출력 텍스트(잘릴 수 있음) 대신 G0 결과 파일을 읽는다. 파일이 없으면 예전 방식으로 읽는다
    g0_file = ROOT / "reports" / "g0_report.json"
    if g0 and g0_file.exists():
        try:
            return json.loads(g0_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    if not g0:
        return {}
    text = g0["detail"]
    start, end = text.find("{"), text.rfind("}")
    try:
        return json.loads(text[start : end + 1]) if start >= 0 else {}
    except json.JSONDecodeError:
        return {}


def g0_violation_count(g0: dict | None) -> int | None:
    data = _g0_json(g0)
    # 잠금 미존재는 구현자의 위반이 아니라 사람 단계 미완료이므로 우회 시도 수에서 뺀다
    return len([v for v in data.get("violations", []) if not v.startswith("[잠금] 잠금 파일이 없다")]) if data else None


def g0_review_required(g0: dict | None) -> list[str]:
    return _g0_json(g0).get("review_required", [])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--role", required=True)
    ap.add_argument("--base", required=True)
    ap.add_argument("--only", default="")
    ap.add_argument("--feedback", action="store_true", help="판정 전 피드백 모드 (HD-24)")
    ap.add_argument("--keep-going", action="store_true")
    ap.add_argument("--slice", default="", help="config/slices.toml 의 슬라이스 ID. 없으면 전체 게이트")
    ap.add_argument("--wo", default="", help="작업 지시서 ID (예: WO-02). KPI 를 지시서별로 묶는다")
    ap.add_argument("--cause", choices=["implementer", "test", "harness"], default=None, help="판정 실행 원인 (implementer | test | harness)")
    args = ap.parse_args()
    if args.cause is None:
        args.cause = "implementer" if args.role == "implementer" else "test"

    started = now_utc()
    gates_doc = tomllib.loads(GATES.read_text(encoding="utf-8"))
    gates = gates_doc["gate"]

    sl: dict = {}
    if args.slice:
        slices = tomllib.loads(SLICES.read_text(encoding="utf-8")).get("slice", {})
        if args.slice not in slices:
            print(f"슬라이스 없음: {args.slice}. 정의된 슬라이스: {sorted(slices)}")
            return 2
        sl = slices[args.slice]

    overrides = sl.get("override", {})
    if isinstance(overrides, dict):
        for gid, ovr in overrides.items():
            if isinstance(ovr, dict) and "id" in ovr and ovr["id"] != gid:
                print(f"슬라이스 override 가 게이트 ID 를 바꿀 수 없습니다: {gid} -> {ovr['id']}", file=sys.stderr)
                return 2

    merged_gates = []
    for g in gates:
        gid = g["id"]
        if isinstance(overrides, dict) and gid in overrides and isinstance(overrides[gid], dict):
            merged_gates.append({**g, **overrides[gid]})
        else:
            merged_gates.append(dict(g))
    gates = merged_gates

    if args.feedback:
        feedback_gates = gates_doc.get("harness", {}).get("feedback_gates")
        if not isinstance(feedback_gates, list) or not feedback_gates:
            print("feedback_gates 설정이 없거나 비어 있습니다.", file=sys.stderr)
            return 2

        kinds_by_id = {g["id"]: g.get("kind", "") for g in gates}
        for gid in feedback_gates:
            if gid not in kinds_by_id:
                print(f"feedback_gates 의 게이트 '{gid}' 가 gates.toml 에 정의되지 않았습니다.", file=sys.stderr)
                return 2
            if kinds_by_id[gid] == "coverage":
                print(f"feedback_gates 에 커버리지 종류 게이트 '{gid}' 를 포함할 수 없습니다.", file=sys.stderr)
                return 2

        if args.only:
            only = {g.strip() for g in args.only.split(",") if g.strip()}
            diff_gates = only - set(feedback_gates)
            if diff_gates:
                print(f"--only 에 feedback_gates 밖의 게이트가 포함되어 있습니다: {diff_gates}", file=sys.stderr)
                return 2

    GATE_ENV.update({k: str(v) for k, v in gates_doc.get("env", {}).items()})
    only = {g.strip() for g in args.only.split(",") if g.strip()}
    target_only = only if only else (set(feedback_gates) if args.feedback else set())

    # 이전 실행의 산출물을 지운다. 남겨 두면 G3 · G6 이 지난 결과를 읽고 통과할 수 있다
    # (소스가 없는데 G3 · G6 이 통과한 것을 dry run 에서 확인함)
    if args.feedback:
        for g in gates:
            if g["id"] in target_only and "test_report" in g:
                if _is_protected_report(g["test_report"]):
                    print(f"피드백 게이트의 test_report 가 판정 산출물 경로와 겹칩니다: {g['test_report']}", file=sys.stderr)
                    return 2
        stale_paths = [ROOT / g["test_report"] for g in gates if g["id"] in target_only and "test_report" in g]
    else:
        REPORT.parent.mkdir(exist_ok=True)  # C5: 도구가 보고서를 쓰기 전에 폴더가 있어야 한다
        stale_paths = [ROOT / g["report"] for g in gates if g.get("kind") == "coverage" and "report" in g]
        stale_paths += [ROOT / g["test_report"] for g in gates if "test_report" in g]
        stale_paths += [ROOT / "reports" / "g0_report.json"]
        stale_paths += [ROOT / p for p in ADAPTER.stale_artifacts()]
    for stale in stale_paths:
        if stale.is_dir():
            shutil.rmtree(stale, ignore_errors=True)
        else:
            stale.unlink(missing_ok=True)
    results = []
    all_passed = True

    for gate in gates:
        if target_only and gate["id"] not in target_only:
            continue
        t0 = time.monotonic()
        if gate["id"] in sl.get("skip", {}):
            # 범위 밖은 '통과'가 아니라 SKIP 으로 남긴다. 전체 판정에서는 세지 않는다
            results.append({"id": gate["id"], "name": gate["name"], "passed": None,
                            "seconds": 0.0, "detail": f"SKIP (슬라이스 {args.slice}): {sl['skip'][gate['id']]}"})
            print(f"{gate['id']} SKIP  {gate['name']}")
            continue
        gate = {**gate, **sl.get("override", {}).get(gate["id"], {})}
        if gate.get("kind") == "mutation" and "mutation_config" in sl:
            gate["expected_tool_config"] = sl["mutation_config"]
        missing = [e for e in gate.get("needs_env", []) if not os.environ.get(e)]
        if missing:
            # 환경이 없으면 '통과'가 아니라 '실패'. 조용한 건너뛰기를 허용하지 않는다
            passed, detail = False, f"필요한 환경변수 없음: {missing}"
        elif gate.get("kind") == "coverage":
            passed, detail = coverage_gate(gate)
        elif gate.get("kind") == "mutation":
            passed, detail = mutation_gate(gate)
        elif gate.get("kind") == "architecture":
            passed, detail = architecture_gate(gate)
        elif gate.get("kind") == "traceability":
            passed, detail = traceability_gate(gate)
        else:
            cmds = gate.get("cmds") or [gate["cmd"]]
            passed, details = True, []
            for cmd in cmds:
                cmd = [c.format(base=args.base, role=args.role) for c in cmd]
                code, out = run(cmd)
                passed &= code == 0
                details.append(f"$ {' '.join(cmd)}  → exit {code}\n{out}")
            if passed and gate.get("test_report"):
                ok_report, summary = test_report_check(gate)
                passed &= ok_report
                details.append(summary)
            detail = "\n".join(details)

        results.append({
            "id": gate["id"],
            "name": gate["name"],
            "passed": passed,
            "seconds": round(time.monotonic() - t0, 1),
            "detail": detail,
        })
        print(f"{gate['id']} {'PASS' if passed else 'FAIL'}  {gate['name']}")
        all_passed &= passed
        if not passed and not args.keep_going:
            break

    if args.feedback:
        for g in gates:
            if g.get("kind") == "coverage" and "report" in g:
                cov_rep = ROOT / g["report"]
                if cov_rep.is_dir():
                    shutil.rmtree(cov_rep, ignore_errors=True)
                else:
                    cov_rep.unlink(missing_ok=True)

        fb_report = {
            "wo": args.wo,
            "passed": all_passed,
            "gates": [
                {
                    "id": r["id"],
                    "passed": r["passed"],
                    "detail": r["detail"],
                }
                for r in results
            ],
        }
        fb_path = ROOT / "reports" / "feedback_report.json"
        fb_path.parent.mkdir(exist_ok=True)
        fb_path.write_text(json.dumps(fb_report, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
        log_event(
            "feedback_run",
            wo=args.wo,
            slice=args.slice,
            role=args.role,
            passed=all_passed,
            gates={r["id"]: r["passed"] for r in results},
        )
        verdict = "FEEDBACK PASS" if all_passed else "FEEDBACK FAIL"
        print(f"\n피드백 보고서: {fb_path.relative_to(ROOT)}  →  {verdict}")
        return 0 if all_passed else 1

    report = json.dumps(
        {"wo": args.wo, "slice": args.slice, "role": args.role, "cause": args.cause, "base": args.base, "started": started,
         "passed": all_passed, "gates": results},
        indent=2,
        ensure_ascii=False,
    )
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(report, encoding="utf-8", newline="\n")
    RUNS.mkdir(exist_ok=True)
    run_file = RUNS / f"{started.replace(':', '')}_{args.wo or 'none'}_{args.role}.json"
    run_file.write_text(report, encoding="utf-8", newline="\n")
    g0 = next((r for r in results if r["id"] == "G0"), None)
    log_event(
        "gate_run",
        wo=args.wo,
        slice=args.slice,
        role=args.role,
        cause=args.cause,
        base=args.base,
        only=sorted(only),
        passed=all_passed,
        gates={r["id"]: r["passed"] for r in results},
        first_failed=next((r["id"] for r in results if r["passed"] is False), None),
        g0_violations=g0_violation_count(g0),
        review_required=g0_review_required(g0),
        skipped=[r["id"] for r in results if r["passed"] is None],
        seconds=round(sum(r["seconds"] for r in results), 1),
        run_file=run_file.relative_to(ROOT).as_posix(),
    )
    skipped = [r["id"] for r in results if r["passed"] is None]
    verdict = ("SLICE PASS" if args.slice else "ALL PASS") if all_passed else "FAIL"
    if skipped:
        verdict += f"  (SKIP: {', '.join(skipped)})"
    print(f"\n보고서: {REPORT.relative_to(ROOT)}  →  {verdict}")
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
