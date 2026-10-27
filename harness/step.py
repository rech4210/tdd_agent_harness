"""상태 판정기 · 위임/종료 강제 (WO-H45).

슬라이스의 생명주기 상태(stage)를 git 태그 · 이벤트 · 설정에서 결정하고,
위임(--check delegate) 및 종료(--check close) 전 필수 증거를 검증한다.

사용:
  python harness/step.py --slice <ID> [--json]
  python harness/step.py --slice <ID> --check delegate [--json]
  python harness/step.py --slice <ID> --check close [--json]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from typing import Any

import auto_approval
from events import ROOT, read_events
from paths import GATES, SLICES


def git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def parse_ts(val: Any) -> datetime | None:
    if not val or not isinstance(val, str):
        return None
    try:
        dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
    except Exception:
        return None
    # datetime.min(naive) 과 비교하므로 UTC naive 로 맞춘다 (WO-H45 리드 보정, C13)
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def get_harness_settings() -> dict:
    if GATES.is_file():
        try:
            return tomllib.loads(GATES.read_text(encoding="utf-8")).get("harness", {})
        except Exception:
            return {}
    return {}


VALID_TIERS = {"T1", "T2", "T3"}
VALID_RISKS = {"low", "medium", "high"}
VALID_BASES = {"prospective", "retrospective"}


def get_wo_class(events: list[dict], wo: str) -> dict[str, str] | None:
    last_prospective = None
    last_retrospective = None
    for e in events:
        if not isinstance(e, dict) or e.get("kind") != "wo_class" or e.get("wo") != wo:
            continue
        by = e.get("confirmed_by")
        if not isinstance(by, str) or not by.strip():
            continue
        tier = e.get("tier")
        risk = e.get("risk")
        basis = e.get("basis")
        if tier not in VALID_TIERS or risk not in VALID_RISKS or basis not in VALID_BASES:
            continue
        item = {"tier": str(tier), "risk": str(risk), "basis": str(basis)}
        if basis == "prospective":
            last_prospective = item
        elif basis == "retrospective":
            last_retrospective = item
    if last_prospective is not None:
        return last_prospective
    return last_retrospective


def is_verdict_run(e: dict, wo: str, slice_id: str) -> bool:
    if not isinstance(e, dict) or e.get("kind") != "gate_run":
        return False
    if e.get("role") != "implementer":
        return False
    if e.get("wo") != wo:
        return False
    if "slice" in e and e.get("slice") is not None and e.get("slice") != slice_id:
        return False
    if "purpose" in e:
        return e.get("purpose") == "verdict"
    return not e.get("only")


def get_human_minutes(events: list[dict], wo: str) -> float | None:
    open_: dict[str, datetime] = {}
    total = 0.0
    pairs = 0
    for e in events:
        if not isinstance(e, dict) or e.get("wo") != wo:
            continue
        kind = e.get("kind")
        stage = e.get("stage")
        if not stage:
            continue
        if kind == "human_start":
            dt = parse_ts(e.get("ts"))
            if dt:
                open_[stage] = dt
        elif kind == "human_end" and stage in open_:
            start_t = open_.pop(stage)
            dt = parse_ts(e.get("ts"))
            if dt:
                total += (dt - start_t).total_seconds() / 60
                pairs += 1
    if pairs == 0:
        return None
    return round(total, 1)


def main() -> int:
    ap = argparse.ArgumentParser(description="상태 판정기 · 위임/종료 강제")
    ap.add_argument("--slice", required=True, help="슬라이스 ID")
    ap.add_argument("--json", action="store_true", help="JSON 형식 출력")
    ap.add_argument("--check", choices=["delegate", "close"], default=None, help="강제 검사 (delegate|close)")
    ap.add_argument("--closure-draft", action="store_true", help="종료 기록 초안 출력")
    args = ap.parse_args()

    if args.closure_draft:
        if args.json or args.check is not None:
            print("오류: --closure-draft 는 --json 또는 --check 와 함께 지정할 수 없습니다.", file=sys.stderr)
            return 2
        template_path = ROOT / "templates" / "slice-closure.md"
        if not template_path.is_file():
            print(f"오류: 슬라이스 종료 기록 템플릿이 없습니다: {template_path}", file=sys.stderr)
            return 2

    slice_id = args.slice
    if not SLICES.is_file():
        print(f"설정 파일이 없습니다: {SLICES}", file=sys.stderr)
        return 2

    try:
        slices_doc = tomllib.loads(SLICES.read_text(encoding="utf-8"))
        slices_dict = slices_doc.get("slice", {})
    except Exception as err:
        print(f"슬라이스 설정을 읽을 수 없습니다: {err}", file=sys.stderr)
        return 2

    if slice_id not in slices_dict:
        print(f"slices.toml에 없는 슬라이스입니다: {slice_id}", file=sys.stderr)
        return 2

    slice_cfg = slices_dict[slice_id]
    wo = slice_cfg.get("wo", slice_id) if isinstance(slice_cfg, dict) else slice_id

    try:
        events = read_events()
    except Exception as err:
        print(f"reports/events.jsonl 손상: {err}", file=sys.stderr)
        return 2
    wo_cls = get_wo_class(events, wo)
    risk = wo_cls.get("risk") if wo_cls else "low"

    compact = wo.lower().replace("-", "")
    hsettings = get_harness_settings()
    base_pattern = hsettings.get("base_tag", "{compact}-base")
    closed_pattern = hsettings.get("closed_tag", "{compact}-closed")
    base_tag = base_pattern.format(compact=compact, wo=wo)
    closed_tag = closed_pattern.format(compact=compact, wo=wo)

    p_closed = git("rev-parse", "-q", "--verify", f"refs/tags/{closed_tag}^{{commit}}")
    closed_tag_exists = (p_closed.returncode == 0)

    p_base = git("rev-parse", "-q", "--verify", f"refs/tags/{base_tag}^{{commit}}")
    base_tag_exists = (p_base.returncode == 0)
    base_tag_commit = p_base.stdout.strip() if base_tag_exists else ""

    verdict_runs = [(idx, e) for idx, e in enumerate(events) if is_verdict_run(e, wo, slice_id)]
    last_verdict_idx, last_verdict = verdict_runs[-1] if verdict_runs else (-1, None)

    if args.closure_draft:
        template_raw = template_path.read_text(encoding="utf-8")
        marker = "아래부터 복사해서 쓴다."
        if marker not in template_raw:
            print("오류: 템플릿 형식이 올바르지 않습니다.", file=sys.stderr)
            return 2
        draft = template_raw.split(marker, 1)[1].lstrip("\r\n")

        title = slice_cfg.get("title", "") if isinstance(slice_cfg, dict) else ""
        draft = draft.replace("# <슬라이스 ID> 슬라이스 종료 기록 (<슬라이스 이름>)", f"# {slice_id} 슬라이스 종료 기록 ({title})")
        draft = draft.replace("<슬라이스 ID>-closed", closed_tag)
        draft = draft.replace("<슬라이스 ID>", slice_id)
        draft = draft.replace("<슬라이스 이름>", title)

        passed = (last_verdict is not None and last_verdict.get("passed") is True)
        if passed:
            t_sha = last_verdict.get("target_sha") or ""
            sha_text = t_sha[:7] if t_sha else "—"
            draft = draft.replace("<실행 커밋>", sha_text)
        else:
            draft = draft.replace("SLICE PASS", "미통과" if last_verdict else "미판정")
            draft = draft.replace("<실행 커밋>", "미실행")

        verdicts_for_wo = [e for _, e in verdict_runs]
        draft_lines = []
        for line in draft.splitlines():
            if line.startswith("| 생존 변이 판정 (H9) |"):
                draft_lines.append("| 생존 변이 판정 (H9) | 미확인 |")
            elif line.startswith("| 열린 문제 |"):
                draft_lines.append("| 열린 문제 | 미확인 |")
            elif line.startswith("| 판정 실행 수 |"):
                draft_lines.append(f"| 판정 실행 수 | {len(verdicts_for_wo)} | 하네스 · 환경 원인 실행은 구현자 KPI에서 제외 |")
            elif line.startswith("| 첫 실행 통과 |"):
                first_passed = "예" if (verdicts_for_wo and verdicts_for_wo[0].get("passed") is True and not verdicts_for_wo[0].get("only")) else "아니오"
                draft_lines.append(f"| 첫 실행 통과 | {first_passed} | |")
            elif line.startswith("| 사람 시간 (H 구간) |"):
                hm_val = get_human_minutes(events, wo)
                hm_str = "기록 없음" if hm_val is None else f"{hm_val:.1f}"
                draft_lines.append(f"| 사람 시간 (H 구간) | {hm_str} | `harness mark` 기록 |")
            else:
                draft_lines.append(line)
        draft = "\n".join(draft_lines) + "\n"
        draft = f"> **미판정 초안**\n\n{draft}"
        print(draft, end="")
        return 0

    # stage 결정 (위에서부터 첫 일치)
    if closed_tag_exists:
        stage = "closed"
        next_action = None
        commands: list[str] = []
    elif last_verdict is not None:
        if last_verdict.get("passed") is True:
            stage = "S10"
            next_action = "종료 태그 발행 및 병합 대기"
            commands = [
                f"python harness/step.py --slice {slice_id} --check close",
                f'git tag -a {closed_tag} -m "<종료>"',
            ]
        else:
            stage = "S8"
            next_action = "구현 수정 후 판정 재실행"
            commands = [
                f"python harness/run_gates.py --role implementer --base {base_tag} --slice {slice_id} --wo {wo}",
            ]
    elif base_tag_exists:
        stage = "S7"
        next_action = "구현 및 판정 실행"
        commands = [
            f"python harness/step.py --slice {slice_id} --check delegate",
            f"python harness/run_gates.py --feedback --role implementer --base {base_tag} --wo {wo}",
            f"python harness/run_gates.py --role implementer --base {base_tag} --slice {slice_id} --wo {wo}",
        ]
    elif any(isinstance(e, dict) and e.get("kind") == "review_run" and e.get("wo") == wo for e in events):
        stage = "S6"
        next_action = "H3 승인 · 잠금 · 기준 태그 발행"
        commands = [
            "python harness/check_protected_paths.py --write-lock",
            f'git tag -a {base_tag} -m "<잠금 + 지시서 확정>"',
            f"python harness/step.py --slice {slice_id} --check delegate",
        ]
    else:
        stage = "S1-S5"
        next_action = "명세 · 테스트 작성 및 검토"
        commands = [
            f"python harness/run_gates.py --role test_author --base HEAD --only GT --wo {wo} --slice {slice_id}",
            f"python harness/mark.py review <검토자> --wo {wo} --round <N> --defects <K> --new-defects <M>",
        ]

    # H3 증거 확인 (D4, C4 공통)
    has_human_h3 = any(
        isinstance(e, dict)
        and e.get("kind") == "human_wait_end"
        and e.get("wo") == wo
        and e.get("stage") == "H3"
        for e in events
    )

    has_valid_auto = any(
        isinstance(e, dict)
        and e.get("wo") == wo
        and auto_approval.is_valid_auto(e, risk)
        for e in events
    )

    has_h3 = has_human_h3 or has_valid_auto

    blockers: list[str] = []

    if args.check == "delegate":
        # D2: 작업 전 분류 없음
        has_prospective = any(
            isinstance(e, dict)
            and e.get("kind") == "wo_class"
            and e.get("wo") == wo
            and e.get("basis") == "prospective"
            and isinstance(e.get("confirmed_by"), str)
            and bool(e.get("confirmed_by").strip())
            for e in events
        )
        if not has_prospective:
            blockers.append("D2: 작업 전 분류(wo_class prospective)가 없습니다. python harness/mark.py classify 로 prospective 분류를 기록하세요.")

        # D3: S5 검토 기록 없음
        review_runs = [e for e in events if isinstance(e, dict) and e.get("kind") == "review_run" and e.get("wo") == wo]
        valid_reviews = [
            e for e in review_runs
            if e.get("contract_defects") is not None and isinstance(e.get("contract_defects"), (int, float))
        ]
        if not valid_reviews:
            blockers.append("D3: S5 검토 기록(review_run contract_defects)이 없습니다. python harness/mark.py review 로 검토를 기록하세요.")

        # D4: H3 증거 없음
        if not has_h3:
            blockers.append("D4: H3 증거(human_wait_end H3 또는 유효한 auto_approval)가 없습니다. 승인을 받거나 유효한 auto-approve를 기록하세요.")

        # D5: 잠금 없음
        last_review_idx = -1
        for idx, e in enumerate(events):
            if isinstance(e, dict) and e.get("kind") == "review_run" and e.get("wo") == wo:
                last_review_idx = idx

        if last_review_idx == -1:
            has_valid_lock = False
        else:
            has_valid_lock = any(
                isinstance(e, dict)
                and e.get("kind") == "test_lock"
                and ("wo" not in e or e.get("wo") is None or e.get("wo") == wo)
                for e in events[last_review_idx + 1:]
            )
        if not has_valid_lock:
            blockers.append("D5: 마지막 검토 이후 잠금(test_lock)이 없습니다. 검토 반영 뒤 잠금을 다시 실행하세요.")

        # D6: 기준 태그 없음 또는 기준 태그 커밋에 config/test_lock.json 없음
        if not base_tag_exists or not base_tag_commit:
            blockers.append(f"D6: 기준 태그({base_tag})가 없습니다. 잠금 파일을 커밋하고 기준 태그를 생성하세요.")
        else:
            p_lock_file = git("cat-file", "-e", f"{base_tag_commit}:config/test_lock.json")
            if p_lock_file.returncode != 0:
                blockers.append(f"D6: 기준 태그({base_tag}) 커밋에 config/test_lock.json 파일이 없습니다. 잠금 파일을 기준 태그 커밋에 포함하세요.")

    elif args.check == "close":
        # C1: 마지막 판정 실행이 통과가 아니거나 target_sha 가 없거나 HEAD 의 조상(같음 포함)이 아님
        c1_fail = False
        target_sha_val: str | None = None
        is_target_ancestor = False
        if not last_verdict:
            c1_fail = True
        else:
            if last_verdict.get("passed") is not True:
                c1_fail = True
            t_sha = last_verdict.get("target_sha")
            if not (isinstance(t_sha, str) and t_sha.strip()):
                c1_fail = True
            else:
                target_sha_val = t_sha.strip()
                p_anc = git("merge-base", "--is-ancestor", target_sha_val, "HEAD")
                if p_anc.returncode == 0:
                    is_target_ancestor = True
                else:
                    c1_fail = True
        if c1_fail:
            blockers.append("C1: 마지막 판정 실행이 통과가 아니거나 target_sha가 없거나 HEAD의 조상이 아닙니다. 판정을 다시 실행하세요.")

        # C2: 그 실행의 failed_cases > 0
        if last_verdict:
            failed_cases = last_verdict.get("failed_cases")
            if failed_cases is not None and isinstance(failed_cases, (int, float)) and failed_cases > 0:
                blockers.append("C2: 마지막 판정 실행에 실패한 사례(failed_cases > 0)가 있습니다. 테스트 실패를 해결하세요.")

        # C3: 그 실행의 g0_violations > 0 또는 review_required 비지 않음
        if last_verdict:
            g0_viol = last_verdict.get("g0_violations")
            has_g0 = g0_viol is not None and isinstance(g0_viol, (int, float)) and g0_viol > 0
            review_req = last_verdict.get("review_required")
            has_review = bool(review_req)
            if has_g0 or has_review:
                blockers.append("C3: 마지막 판정 실행에 G0 위반이 있거나 review_required가 남아있습니다. 보호 경로 위반을 해결하세요.")

        # C4: H3 증거 없음
        if not has_h3:
            blockers.append("C4: H3 증거(human_wait_end H3 또는 유효한 auto_approval)가 없습니다. 승인을 받거나 유효한 auto-approve를 기록하세요.")

        # C5: 지표 누락: prospective 분류 · role implementer 의 model_usage · review_run 중 하나라도 없음
        has_prospective = any(
            isinstance(e, dict)
            and e.get("kind") == "wo_class"
            and e.get("wo") == wo
            and e.get("basis") == "prospective"
            and isinstance(e.get("confirmed_by"), str)
            and bool(e.get("confirmed_by").strip())
            for e in events
        )
        has_impl_usage = any(
            isinstance(e, dict)
            and e.get("kind") == "model_usage"
            and e.get("wo") == wo
            and e.get("role") == "implementer"
            for e in events
        )
        has_any_review = any(
            isinstance(e, dict)
            and e.get("kind") == "review_run"
            and e.get("wo") == wo
            for e in events
        )
        if not (has_prospective and has_impl_usage and has_any_review):
            blockers.append("C5: 필수 지표(prospective 분류, implementer model_usage, review_run)가 누락되었습니다. 누락된 이벤트를 기록하세요.")

        # C6: target_sha..HEAD 변경 경로가 reports/** · docs/HANDOFF.md 밖에 있음, 또는 종료 태그가 이미 있음
        outside_changes = False
        if is_target_ancestor and target_sha_val:
            # 이름 바꾸기 감지를 끈다: 켜 두면 src → reports 이동이 새 이름만 보여 삭제가 숨는다 (리드 보정, C12)
            p_diff = git("-c", "diff.renames=false", "diff", "--no-renames", "--name-only", f"{target_sha_val}..HEAD")
            if p_diff.returncode == 0:
                for line in p_diff.stdout.splitlines():
                    path_str = line.strip().replace("\\", "/")
                    if not path_str:
                        continue
                    if not (path_str.startswith("reports/") or path_str == "docs/HANDOFF.md"):
                        outside_changes = True
                        break

        if outside_changes or closed_tag_exists:
            blockers.append("C6: 판정 이후 변경 경로가 reports/** 또는 docs/HANDOFF.md 밖에 있거나 종료 태그가 이미 존재합니다.")

        # C7: 분류 위험도가 medium · high 인데 마지막 판정 실행 이후 review_run 이 없거나, 그 마지막 것의 contract_defects > 0
        if risk in ("medium", "high"):
            c7_fail = False
            if last_verdict_idx == -1:
                c7_fail = True
            else:
                post_reviews = [
                    e for e in events[last_verdict_idx + 1:]
                    if isinstance(e, dict)
                    and e.get("kind") == "review_run"
                    and e.get("wo") == wo
                ]
                if not post_reviews:
                    c7_fail = True
                else:
                    last_post = post_reviews[-1]
                    defects = last_post.get("contract_defects")
                    if defects is None or isinstance(defects, bool) or not isinstance(defects, (int, float)) or defects > 0:
                        c7_fail = True
            if c7_fail:
                blockers.append("C7: 위험도 medium/high 작업에서 판정 이후 S9 검토 기록이 없거나 결함(contract_defects > 0)이 남아있습니다. 판정 후 검토를 기록하세요.")

    evidence = {
        "wo": wo,
        "base_tag": base_tag,
        "base_tag_exists": base_tag_exists,
        "closed_tag": closed_tag,
        "closed_tag_exists": closed_tag_exists,
        "wo_class": wo_cls,
        "risk": risk,
        "has_h3": has_h3,
        "last_verdict": {
            "passed": last_verdict.get("passed"),
            "target_sha": last_verdict.get("target_sha"),
            "failed_cases": last_verdict.get("failed_cases"),
            "g0_violations": last_verdict.get("g0_violations"),
        } if last_verdict else None,
    }

    doc = {
        "slice": slice_id,
        "stage": stage,
        "next": next_action,
        "commands": commands,
        "blockers": blockers,
        "evidence": evidence,
    }

    if args.json:
        print(json.dumps(doc, ensure_ascii=False, indent=2))
    else:
        print(f"상태: {stage}")
        if next_action:
            print(f"다음: {next_action}")
        else:
            print("다음: 없음")
        for cmd in commands:
            print(f"  $ {cmd}")
        if blockers:
            print("막힘:")
            for b in blockers:
                print(f"  - {b}")

    if args.check is not None:
        return 1 if blockers else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
