"""사람 이벤트 보조 기록. KPI 의 핵심 지표는 이 기록 없이도 자동 이벤트로 계산된다.

사람이 시각을 직접 적지 않는다. 명령을 친 순간의 시각을 스크립트가 찍는다.
기억에 의존한 사후 기록을 막기 위해, 지난 시각은 받지 않는다.

사용:
  python harness/mark.py start H3 --wo WO-02           # 사람 단계 시작
  python harness/mark.py end H3 --wo WO-02             # 사람 단계 끝
  python harness/mark.py wait-start DEC --wo WO-AK01   # 자동 대기 시작 (lead)
  python harness/mark.py wait-end DEC --wo WO-AK01 --decisions 4 # 자동 대기 끝 (lead)
  python harness/mark.py note "..." --wo WO-02         # 메모
  python harness/mark.py review codex --round 1 --defects 2 --wo WO-02  # 검토 기록
  python harness/mark.py budget-extend "이유" --wo WO-02   # 예산 연장
  python harness/mark.py budget-stop "이유" --wo WO-02     # 예산 중단
  python harness/mark.py classify "근거" --wo WO-02 --tier T2 --risk low --basis prospective --by user  # 작업 지시서 분류
  python harness/mark.py auto-approve D-28 --wo WO-02 --gt pass --lazy-caught 10/10 --residual-defects 0 --base-types unbounded,reject-preserve --by lead  # 자동 승인 기록
  python harness/mark.py auto-approve additive --wo WO-02 --kind mutant-kill --base <sha> --review-note "..." --by lead  # 추가·변이 제거 테스트 자동 승인
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from events import ROOT, log_event, read_events

STAGES = {
    "H1", "H2", "H3", "H4", "H5", "H6", "H7", "H8", "H9", "H10", "H11", "QA",
    "DEC", "MERGE",
}


def _handle_auto_approve_additive(args: argparse.Namespace) -> int:
    # additive 형식 검사 (종료 2)
    if not args.wo or not args.wo.strip():
        print("--wo가 필요하다")
        return 2
    if not args.by or not args.by.strip():
        print("--by(확인자)가 필요하다")
        return 2
    if not args.review_note or not args.review_note.strip():
        print("--review-note가 필요하다")
        return 2
    if args.kind not in ("mutant-kill", "regression"):
        print(f"--kind는 mutant-kill 또는 regression이어야 한다: {args.kind}")
        return 2
    if not args.base or not re.fullmatch(r"[0-9a-f]{40}", args.base):
        print(f"--base는 40자 소문자 16진이어야 한다: {args.base}")
        return 2

    # 1. 그 WO 의 분류 위험도가 high 가 아니다. (kpi.wo_class_for 재사용, 없으면 거짓)
    try:
        from kpi import wo_class_for

        evs = read_events()
        wc = wo_class_for(evs, args.wo)
        if not wc or wc.get("risk") == "high":
            print("조건 미달: 사람 H3 승인이 필요하다")
            print("이유: 작업 지시서 분류가 없거나 위험도가 high이다")
            return 1
    except Exception as ex:
        print("조건 미달: 사람 H3 승인이 필요하다")
        print(f"이유: 작업 지시서 분류 확인 실패: {ex}")
        return 1

    # 2. 작업 트리가 깨끗하다 (evidence.check_dirty 재사용, reports/ 제외)
    try:
        from evidence import check_dirty

        if check_dirty(ROOT):
            print("조건 미달: 사람 H3 승인이 필요하다")
            print("이유: 작업 트리가 깨끗하지 않다")
            return 1
    except Exception as ex:
        print("조건 미달: 사람 H3 승인이 필요하다")
        print(f"이유: 작업 트리 상태 확인 실패: {ex}")
        return 1

    # 3. --base 가 커밋으로 해석되고 HEAD 의 조상이며 HEAD 와 다르다.
    try:
        r_rev = subprocess.run(
            ["git", "rev-parse", "--verify", f"{args.base}^{{commit}}"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if r_rev.returncode != 0:
            print("조건 미달: 사람 H3 승인이 필요하다")
            print(f"이유: 기준 커밋을 찾을 수 없다: {args.base}")
            return 1

        r_head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if r_head.returncode != 0:
            print("조건 미달: 사람 H3 승인이 필요하다")
            print("이유: HEAD 커밋을 해석할 수 없다")
            return 1
        head_sha = r_head.stdout.strip()

        if head_sha == args.base:
            print("조건 미달: 사람 H3 승인이 필요하다")
            print("이유: 기준 커밋이 HEAD 와 같다")
            return 1

        r_anc = subprocess.run(
            ["git", "merge-base", "--is-ancestor", args.base, "HEAD"],
            cwd=ROOT,
            capture_output=True,
        )
        if r_anc.returncode != 0:
            print("조건 미달: 사람 H3 승인이 필요하다")
            print("이유: 기준 커밋이 HEAD 의 조상이 아니다")
            return 1
    except Exception as ex:
        print("조건 미달: 사람 H3 승인이 필요하다")
        print(f"이유: git 커밋 계통 확인 실패: {ex}")
        return 1

    # 4. git diff --raw --no-renames -z <base> HEAD
    try:
        r_diff = subprocess.run(
            ["git", "diff", "--raw", "--no-renames", "-z", args.base, "HEAD"],
            cwd=ROOT,
            capture_output=True,
        )
        if r_diff.returncode != 0:
            print("조건 미달: 사람 H3 승인이 필요하다")
            print("이유: git diff 실행 실패")
            return 1

        diff_raw = r_diff.stdout
        tokens = [t.decode("utf-8", errors="replace") for t in diff_raw.split(b"\0") if t]
        if not tokens or len(tokens) % 2 != 0:
            print("조건 미달: 사람 H3 승인이 필요하다")
            print("이유: 변경된 파일이 없거나 diff 출력이 올바르지 않다")
            return 1

        added_paths: list[str] = []
        for i in range(0, len(tokens), 2):
            meta = tokens[i].strip()
            path = tokens[i + 1]
            parts = meta.split()
            if len(parts) < 5:
                print("조건 미달: 사람 H3 승인이 필요하다")
                print(f"이유: diff 메타데이터 형식 오류: {meta}")
                return 1
            newmode = parts[1]
            status = parts[4]
            if status != "A" or newmode not in ("100644", "100755"):
                print("조건 미달: 사람 H3 승인이 필요하다")
                print(f"이유: 일반 새 파일 추가(A)가 아닌 변경이 있다 ({path}: {status} {newmode})")
                return 1
            added_paths.append(path)

        if not added_paths:
            print("조건 미달: 사람 H3 승인이 필요하다")
            print("이유: 추가된 파일이 없다")
            return 1

        roles_file = ROOT / "config" / "roles.toml"
        if not roles_file.is_file():
            print("조건 미달: 사람 H3 승인이 필요하다")
            print("이유: config/roles.toml 파일이 없다")
            return 1
        raw_roles = roles_file.read_bytes().decode("utf-8")
        if raw_roles.startswith("\ufeff"):
            print("조건 미달: 사람 H3 승인이 필요하다")
            print("이유: config/roles.toml 에 UTF-8 BOM 이 있다")
            return 1
        roles_data = tomllib.loads(raw_roles)
        allow = roles_data.get("roles", {}).get("test_author", {}).get("allow")
        if not isinstance(allow, list) or not all(isinstance(x, str) for x in allow):
            print("조건 미달: 사람 H3 승인이 필요하다")
            print("이유: config/roles.toml 에 test_author allow 목록이 없다")
            return 1

        from check_protected_paths import locked_globs, match_any

        for p in added_paths:
            if not match_any(p, allow):
                print("조건 미달: 사람 H3 승인이 필요하다")
                print(f"이유: 추가된 경로가 test_author allow 범위 밖이다: {p}")
                return 1

        locked = locked_globs()
        if not any(match_any(p, locked) for p in added_paths):
            print("조건 미달: 사람 H3 승인이 필요하다")
            print("이유: 잠금 범위(locked)에 속하는 테스트 추가가 없다")
            return 1
    except Exception as ex:
        print("조건 미달: 사람 H3 승인이 필요하다")
        print(f"이유: 변경 파일 및 권한 검사 실패: {ex}")
        return 1

    # 5. config/test_lock.json 바이트 일치 및 내용/해시 검증
    try:
        r_base_lock = subprocess.run(
            ["git", "show", f"{args.base}:config/test_lock.json"],
            cwd=ROOT,
            capture_output=True,
        )
        if r_base_lock.returncode != 0:
            print("조건 미달: 사람 H3 승인이 필요하다")
            print("이유: 기준 커밋에 config/test_lock.json 이 없다")
            return 1

        r_head_lock = subprocess.run(
            ["git", "show", "HEAD:config/test_lock.json"],
            cwd=ROOT,
            capture_output=True,
        )
        if r_head_lock.returncode != 0:
            print("조건 미달: 사람 H3 승인이 필요하다")
            print("이유: HEAD 에 config/test_lock.json 이 없다")
            return 1

        if r_base_lock.stdout != r_head_lock.stdout:
            print("조건 미달: 사람 H3 승인이 필요하다")
            print("이유: 기준 커밋과 HEAD 의 config/test_lock.json 바이트가 일치하지 않는다")
            return 1

        lock_path = ROOT / "config" / "test_lock.json"
        if not lock_path.is_file():
            print("조건 미달: 사람 H3 승인이 필요하다")
            print("이유: 작업 트리에 config/test_lock.json 이 없다")
            return 1
        raw_lock = lock_path.read_bytes().decode("utf-8")
        if raw_lock.startswith("\ufeff"):
            print("조건 미달: 사람 H3 승인이 필요하다")
            print("이유: config/test_lock.json 에 UTF-8 BOM 이 있다")
            return 1
        lock_doc = json.loads(raw_lock)
        if not isinstance(lock_doc, dict):
            print("조건 미달: 사람 H3 승인이 필요하다")
            print("이유: config/test_lock.json 최상위가 객체가 아니다")
            return 1
        if not all(isinstance(k, str) and isinstance(v, str) for k, v in lock_doc.items()):
            print("조건 미달: 사람 H3 승인이 필요하다")
            print("이유: config/test_lock.json 항목이 문자열 경로와 문자열 해시의 맵이 아니다")
            return 1
        root_resolved = ROOT.resolve()
        for rel_p, exp_h in lock_doc.items():
            f_path = (ROOT / rel_p).resolve()
            # 잠금 경로는 프로젝트 안 상대 경로여야 한다 — 절대 경로 · '..' 로 밖을 가리키면 거부 (WO-H80 리드 보정, 경계 사례 L01)
            if Path(rel_p).is_absolute() or ".." in Path(rel_p).parts or not f_path.is_relative_to(root_resolved):
                print("조건 미달: 사람 H3 승인이 필요하다")
                print(f"이유: 잠금 경로가 프로젝트 안 상대 경로가 아니다: {rel_p}")
                return 1
            if not f_path.is_file():
                print("조건 미달: 사람 H3 승인이 필요하다")
                print(f"이유: 잠금 대상 파일이 존재하지 않는다: {rel_p}")
                return 1
            content = f_path.read_bytes().replace(b"\r\n", b"\n")
            actual_h = hashlib.sha256(content).hexdigest()
            if actual_h != exp_h:
                print("조건 미달: 사람 H3 승인이 필요하다")
                print(f"이유: 잠금 대상 파일의 해시가 일치하지 않는다: {rel_p}")
                return 1
    except Exception as ex:
        print("조건 미달: 사람 H3 승인이 필요하다")
        print(f"이유: 잠금 파일(config/test_lock.json) 검증 실패: {ex}")
        return 1

    # 6. reports/gate_report.json 검증
    try:
        from evidence import verify_evidence

        v_code, v_msg = verify_evidence("HEAD")
        if v_code != 0:
            print("조건 미달: 사람 H3 승인이 필요하다")
            print(f"이유: gate_report.json 판정 증거 검증 실패: {v_msg}")
            return 1

        rep_file = ROOT / "reports" / "gate_report.json"
        if not rep_file.is_file():
            print("조건 미달: 사람 H3 승인이 필요하다")
            print("이유: reports/gate_report.json 파일이 없다")
            return 1
        raw_rep = rep_file.read_bytes().decode("utf-8")
        if raw_rep.startswith("\ufeff"):
            print("조건 미달: 사람 H3 승인이 필요하다")
            print("이유: reports/gate_report.json 에 UTF-8 BOM 이 있다")
            return 1
        rep = json.loads(raw_rep)
        if not isinstance(rep, dict):
            print("조건 미달: 사람 H3 승인이 필요하다")
            print("이유: reports/gate_report.json 최상위가 객체가 아니다")
            return 1
        if rep.get("wo") != args.wo:
            print("조건 미달: 사람 H3 승인이 필요하다")
            print(f"이유: 보고서 wo 불일치: {rep.get('wo')} != {args.wo}")
            return 1
        if rep.get("role") != "test_author":
            print("조건 미달: 사람 H3 승인이 필요하다")
            print(f"이유: 보고서 role 이 test_author 가 아니다: {rep.get('role')}")
            return 1
        if rep.get("passed") is not True:
            print("조건 미달: 사람 H3 승인이 필요하다")
            print(f"이유: 보고서 passed 가 True 가 아니다: {rep.get('passed')}")
            return 1
        ev = rep.get("evidence")
        if not isinstance(ev, dict):
            print("조건 미달: 사람 H3 승인이 필요하다")
            print("이유: 보고서에 evidence 객체가 없다")
            return 1
        if ev.get("base_sha") != args.base:
            print("조건 미달: 사람 H3 승인이 필요하다")
            print(f"이유: 보고서 evidence.base_sha 불일치: {ev.get('base_sha')} != {args.base}")
            return 1
    except Exception as ex:
        print("조건 미달: 사람 H3 승인이 필요하다")
        print(f"이유: reports/gate_report.json 검증 실패: {ex}")
        return 1

    # 7. --kind mutant-kill 변이 집계 검사
    best_base_mut = None
    head_mut = None
    if args.kind == "mutant-kill":
        try:
            from auto_approval import is_valid_mutation_dict, undetected

            gates = rep.get("gates")
            if isinstance(gates, list):
                for g in gates:
                    if isinstance(g, dict) and isinstance(g.get("mutation"), dict):
                        head_mut = g["mutation"]
                        break

            if head_mut is None or not is_valid_mutation_dict(head_mut):
                print("조건 미달: 사람 H3 승인이 필요하다")
                print("이유: HEAD 보고서 gates 에 유효한 mutation 집계가 없다")
                return 1

            candidates: list[tuple[tuple[str, str], dict]] = []
            runs_dir = ROOT / "reports" / "runs"
            if runs_dir.is_dir():
                for f in runs_dir.glob("*.json"):
                    try:
                        raw = f.read_bytes().decode("utf-8")
                        if raw.startswith("\ufeff"):
                            continue
                        doc = json.loads(raw)
                        if not isinstance(doc, dict):
                            continue
                        ev_doc = doc.get("evidence")
                        if not isinstance(ev_doc, dict):
                            continue
                        if ev_doc.get("target_sha") != args.base:
                            continue
                        if ev_doc.get("scope") != "full":
                            continue
                        if ev_doc.get("dirty") is not False:
                            continue
                        run_gates = doc.get("gates")
                        if not isinstance(run_gates, list):
                            continue
                        base_cand = None
                        for g in run_gates:
                            if isinstance(g, dict) and isinstance(g.get("mutation"), dict):
                                m = g["mutation"]
                                if is_valid_mutation_dict(m):
                                    base_cand = m
                                    break
                        if base_cand is None:
                            continue
                        started = doc.get("started")
                        if not isinstance(started, str):
                            continue
                        # 문자열이 아니라 시각으로 비교한다(시간대 · 밀리초 표기 혼용). 해석 못 하거나 시간대 없으면 적격 아님
                        # (WO-H80 리드 보정, 경계 사례 E01)
                        try:
                            started_at = datetime.fromisoformat(started.replace("Z", "+00:00"))
                        except ValueError:
                            continue
                        if started_at.tzinfo is None:
                            continue
                        candidates.append(((started_at.astimezone(timezone.utc), f.name), base_cand))
                    except Exception:
                        continue

            if not candidates:
                print("조건 미달: 사람 H3 승인이 필요하다")
                print("이유: 기준 커밋의 적격 변이 실행 기록(reports/runs)이 없다")
                return 1

            best_base_mut = max(candidates, key=lambda c: c[0])[1]

            if head_mut["total"] != best_base_mut["total"]:
                print("조건 미달: 사람 H3 승인이 필요하다")
                print(f"이유: 변이 총수(total) 불일치: 기준={best_base_mut['total']}, HEAD={head_mut['total']}")
                return 1

            head_und = undetected(head_mut)
            base_und = undetected(best_base_mut)
            if head_und >= base_und:
                print("조건 미달: 사람 H3 승인이 필요하다")
                print(f"이유: 미검출 변이 수가 줄지 않았다: 기준={base_und}, HEAD={head_und}")
                return 1
        except Exception as ex:
            print("조건 미달: 사람 H3 승인이 필요하다")
            print(f"이유: 변이 집계 검사 실패: {ex}")
            return 1

    event_kwargs: dict[str, Any] = {
        "wo": args.wo,
        "policy": "additive",
        "additive_kind": args.kind,
        "base_sha": args.base,
        "head_sha": head_sha,
        "files": sorted(added_paths),
        "review_note": args.review_note.strip(),
        "by": args.by.strip(),
    }
    if args.kind == "mutant-kill":
        event_kwargs["mutation"] = {"base": best_base_mut, "head": head_mut}

    e = log_event("auto_approval", **event_kwargs)
    print(f"기록: {e['ts']} {e['kind']} {args.wo} policy=additive kind={args.kind} by={args.by.strip()}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["start", "end", "note", "wait-start", "wait-end", "review", "budget-extend", "budget-stop", "classify", "amend-cause", "auto-approve"])
    ap.add_argument("value", help="단계(H1~H11, QA, DEC, MERGE), 검토자, 메모 또는 이유")
    ap.add_argument("--wo", default="")
    ap.add_argument("--decisions", type=int, default=0, help="결정 수 (0 이상)")
    ap.add_argument("--round", type=int, default=None, help="검토 회차 (0 이상)")
    ap.add_argument("--defects", type=int, default=None, help="계약 안 결함 수 (0 이상)")
    ap.add_argument("--new-defects", type=int, default=None, help="새로 찾은 결함 수 (0 이상)")
    ap.add_argument("--tier", default=None, help="티어 (T1|T2|T3)")
    ap.add_argument("--risk", default=None, help="위험도 (low|medium|high)")
    ap.add_argument("--basis", default=None, help="근거 (prospective|retrospective)")
    ap.add_argument("--by", default=None, help="확인자")
    ap.add_argument("--run", default=None, help="게이트 실행 파일 경로 (amend-cause 전용)")
    ap.add_argument("--cause", default=None, help="정정할 원인 (amend-cause 전용)")
    ap.add_argument("--gt", default=None, help="게이트 통과 여부 (auto-approve 전용)")
    ap.add_argument("--lazy-caught", default=None, help="게으른 구현 감지 비율 N/M (auto-approve 전용)")
    ap.add_argument("--residual-defects", type=int, default=None, help="잔여 결함 수 (auto-approve 전용)")
    ap.add_argument("--base-types", default=None, help="기본 유형 목록 (쉼표 구분, auto-approve 전용)")
    ap.add_argument("--kind", default=None, help="추가 테스트 종류 (mutant-kill|regression, auto-approve additive 전용)")
    ap.add_argument("--base", default=None, help="기준 커밋 40자 (auto-approve additive 전용)")
    ap.add_argument("--review-note", default=None, help="리드 검토 내용 (auto-approve additive 전용)")
    args = ap.parse_args()

    if args.action != "classify" and any(x is not None for x in (args.tier, args.risk, args.basis)):
        print("--tier, --risk, --basis는 classify에서만 쓸 수 있다")
        return 2
    if args.action not in ("classify", "amend-cause", "auto-approve") and args.by is not None:
        print("--by는 classify, amend-cause 또는 auto-approve에서만 쓸 수 있다")
        return 2
    if args.action != "amend-cause" and any(x is not None for x in (args.run, args.cause)):
        print("--run, --cause는 amend-cause에서만 쓸 수 있다")
        return 2
    if args.action != "auto-approve" and any(x is not None for x in (args.gt, args.lazy_caught, args.residual_defects, args.base_types, args.kind, args.base, args.review_note)):
        print("--gt, --lazy-caught, --residual-defects, --base-types, --kind, --base, --review-note는 auto-approve에서만 쓸 수 있다")
        return 2

    if args.action in ("start", "end", "wait-start", "wait-end") and args.value not in STAGES:
        print(f"알 수 없는 단계: {args.value}. 가능한 값: {sorted(STAGES)}")
        return 2
    if args.wo and not re.fullmatch(r"([A-Za-z][A-Za-z0-9]*-[A-Za-z]*\d+[a-z]?|(?:S\d+|\d{2,})-[a-z0-9-]+)", args.wo):
        print(f"작업 ID 또는 슬라이스 ID 형식이 아니다: {args.wo} (예: WO-02, WO-01b, JS-01, WO-AK01, WO-H11, S01-app-state)")
        return 2
    if args.decisions < 0:
        print(f"--decisions는 0 이상의 정수여야 한다: {args.decisions}")
        return 2
    if args.action != "wait-end" and args.decisions != 0:
        print("--decisions는 wait-end에서만 쓸 수 있다")
        return 2
    if args.action != "review" and args.defects is not None:
        print("--defects는 review에서만 쓸 수 있다")
        return 2
    if args.action != "review" and args.new_defects is not None:
        print("--new-defects는 review에서만 쓸 수 있다")
        return 2

    if args.action == "classify":
        if not args.value or not args.value.strip():
            print("근거가 필요하다")
            return 2
        if not args.wo or not args.wo.strip():
            print("--wo가 필요하다")
            return 2
        if args.tier not in ("T1", "T2", "T3"):
            print(f"--tier는 T1, T2, T3 중 하나여야 한다: {args.tier}")
            return 2
        if args.risk not in ("low", "medium", "high"):
            print(f"--risk는 low, medium, high 중 하나여야 한다: {args.risk}")
            return 2
        if args.basis not in ("prospective", "retrospective"):
            print(f"--basis는 prospective, retrospective 중 하나여야 한다: {args.basis}")
            return 2
        if not args.by or not args.by.strip():
            print("--by(확인자)가 필요하다")
            return 2
        e = log_event(
            "wo_class",
            wo=args.wo,
            tier=args.tier,
            risk=args.risk,
            reason=args.value.strip(),
            basis=args.basis,
            confirmed_by=args.by.strip(),
        )
        print(f"기록: {e['ts']} {e['kind']} {args.wo} tier={args.tier} risk={args.risk} basis={args.basis} by={args.by}")
        return 0

    if args.action == "amend-cause":
        if not args.value or not args.value.strip():
            print("근거가 필요하다")
            return 2
        if not args.wo or not args.wo.strip():
            print("--wo가 필요하다")
            return 2
        if not args.run or not args.run.strip():
            print("--run이 필요하다")
            return 2
        if args.cause not in ("implementer", "test", "harness"):
            print(f"--cause는 implementer, test, harness 중 하나여야 한다: {args.cause}")
            return 2
        if not args.by or not args.by.strip():
            print("--by(확인자)가 필요하다")
            return 2
        matching_runs = [
            e for e in read_events()
            if e.get("kind") == "gate_run"
            and e.get("wo") == args.wo
            and e.get("run_file") == args.run
        ]
        if not matching_runs:
            print(f"--run 이 같은 WO ({args.wo})의 gate_run 에 없습니다: {args.run}")
            return 2
        e = log_event(
            "cause_amend",
            wo=args.wo,
            run_file=args.run,
            cause=args.cause,
            reason=args.value.strip(),
            by=args.by.strip(),
        )
        print(f"기록: {e['ts']} {e['kind']} {args.wo} run_file={args.run} cause={args.cause} by={args.by}")
        return 0

    if args.action == "review":
        if not args.value or not args.value.strip():
            print("검토자 이름이 필요하다")
            return 2
        if args.round is None or args.round < 0:
            print(f"--round는 0 이상의 정수여야 한다: {args.round}")
            return 2
        if args.defects is None or args.defects < 0:
            print(f"--defects는 0 이상의 정수여야 한다: {args.defects}")
            return 2
        if args.new_defects is not None and (args.new_defects < 0 or args.new_defects > args.defects):
            print(f"--new-defects는 0 이상 {args.defects} 이하의 정수여야 한다: {args.new_defects}")
            return 2
        new_defects = args.new_defects if args.new_defects is not None else (args.defects if args.round <= 1 else None)
        e = log_event(
            "review_run",
            reviewer=args.value.strip(),
            round=args.round,
            contract_defects=args.defects,
            new_defects=new_defects,
            wo=args.wo,
        )
        print(f"기록: {e['ts']} {e['kind']} {args.value} round={args.round} defects={args.defects} {args.wo}")
        return 0

    if args.action == "auto-approve":
        policy = args.value.strip() if args.value else ""
        if policy == "additive":
            if any(x is not None for x in (args.gt, args.lazy_caught, args.residual_defects, args.base_types)):
                print("--gt, --lazy-caught, --residual-defects, --base-types는 additive 정책에서 쓸 수 없다")
                return 2
            return _handle_auto_approve_additive(args)

        if any(x is not None for x in (args.kind, args.base, args.review_note)):
            print("--kind, --base, --review-note는 additive 정책에서만 쓸 수 있다")
            return 2

        if not re.fullmatch(r"D-\d+", policy):
            print(f"정책 ID 형식이 아니다: {args.value} (예: D-28)")
            return 2
        if not args.wo or not args.wo.strip():
            print("--wo가 필요하다")
            return 2
        if not args.by or not args.by.strip():
            print("--by(확인자)가 필요하다")
            return 2
        if args.lazy_caught is None:
            print("--lazy-caught가 필요하다")
            return 2
        m = re.fullmatch(r"(\d+)/(\d+)", args.lazy_caught.strip())
        if not m:
            print(f"--lazy-caught는 N/M 형식이어야 한다: {args.lazy_caught}")
            return 2
        lazy_caught = int(m.group(1))
        lazy_total = int(m.group(2))
        if args.gt is None or not args.gt.strip():
            print("--gt가 필요하다")
            return 2
        if args.residual_defects is None or args.residual_defects < 0:
            print("--residual-defects는 0 이상의 정수여야 한다")
            return 2
        if args.base_types is None:
            print("--base-types가 필요하다")
            return 2
        base_types = [t.strip() for t in args.base_types.split(",") if t.strip()]

        cond_gt = (args.gt == "pass")
        cond_lazy = (lazy_caught == lazy_total and lazy_caught > 0)
        cond_residual = (args.residual_defects == 0)
        cond_types = ("unbounded" in base_types and "reject-preserve" in base_types)

        if not (cond_gt and cond_lazy and cond_residual and cond_types):
            print("조건 미달: 사람 H3 승인이 필요하다")
            return 1

        e = log_event(
            "auto_approval",
            wo=args.wo,
            policy=policy,
            gt=args.gt.strip(),
            lazy_caught=lazy_caught,
            lazy_total=lazy_total,
            residual_defects=args.residual_defects,
            base_types=base_types,
            by=args.by.strip(),
        )
        print(f"기록: {e['ts']} {e['kind']} {args.wo} policy={policy} by={args.by}")
        return 0

    if args.action in ("budget-extend", "budget-stop"):
        if not args.value or not args.value.strip():
            print("이유가 필요하다")
            return 2
        kind = "budget_extend" if args.action == "budget-extend" else "budget_stop"
        e = log_event(kind, reason=args.value.strip(), wo=args.wo)
        print(f"기록: {e['ts']} {e['kind']} {args.value} {args.wo}")
        return 0

    if args.action in ("wait-start", "wait-end"):
        open_count = 0
        for e in read_events():
            if e.get("stage") == args.value and e.get("wo", "") == args.wo:
                if e.get("kind") == "human_wait_start":
                    open_count += 1
                elif e.get("kind") == "human_wait_end":
                    open_count = max(0, open_count - 1)
        if args.action == "wait-end" and open_count <= 0:
            print(f"열린 wait-start가 없다: 구간={args.value}, wo={args.wo}")
            return 2
        if args.action == "wait-start" and open_count > 0:
            print(f"이미 열린 wait-start가 있다: 구간={args.value}, wo={args.wo}")
            return 2

    fields: dict[str, object] = {
        "stage": args.value if args.action != "note" else None,
        "note": args.value if args.action == "note" else None,
        "wo": args.wo,
    }
    if args.action == "wait-end":
        fields["decisions"] = args.decisions

    e = log_event(f"human_{args.action.replace('-', '_')}", **fields)
    print(f"기록: {e['ts']} {e['kind']} {args.value} {args.wo}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
