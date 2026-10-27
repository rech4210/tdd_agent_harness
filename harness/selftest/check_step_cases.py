"""하네스 자기 검사: 상태 판정기 · 위임/종료 강제 · 자동 승인 기록 (WO-H45, 계획 문서 §3.1 · §3.3 · §3.4).

배경: 상태 기계(docs/cloud-sdd-workflow.md §2)는 문서로만 있었고, 리드가 기억으로 단계를 판단했다. 진실 공급원을
대화가 아니라 git 태그 · reports/events.jsonl · 설정으로 옮기고, 필수 증거가 없는 전이(위임 · 종료)를 막는다.
안내(읽기만)와 강제(--check, 종료 코드)는 분리한다.

계약
S 안내: `python harness/step.py --slice <ID> --json` → 종료 0, stdout 한 개의 JSON 객체
  {"slice", "stage", "next", "blockers": [...], "evidence": {...}}. --json 없으면 사람용 줄(첫 줄 "상태: <stage>",
  "다음: ..." 줄 포함). 슬라이스가 config/slices.toml 에 없으면 종료 2.
  stage 결정 (위에서부터 첫 일치):
    "closed" 종료 태그 있음 → 다음 없음
    "S10"    마지막 판정 실행 통과 (판정 실행 = role implementer, purpose verdict 또는 purpose 없고 only 빈 gate_run)
    "S8"     마지막 판정 실행 실패
    "S7"     기준 태그 있음, 판정 실행 없음
    "S6"     기준 태그 없음, review_run 있음 (H3 · 잠금 · 기준 태그 대기)
    "S1-S5"  그 밖
  태그 이름: gates.toml [harness] base_tag (기본 "{compact}-base") · closed_tag (기본 "{compact}-closed"),
  {compact} = WO 를 소문자로 하고 '-' 를 뺀 것. 같은 입력이면 출력이 같다(대화 · 환경 기억 없음).
D 위임 강제: `--check delegate` → 막힘이 없으면 종료 0, 있으면 종료 1. 막힘은 코드로 시작하는 문자열(JSON blockers ·
  사람용 출력 모두).
    D2 작업 전 분류 없음: 그 WO 의 wo_class 중 basis prospective · confirmed_by 비지 않음이 없음
    D3 S5 검토 기록 없음: review_run 이 없거나 contract_defects 가 없는(None) 기록만 있음
    D4 H3 증거 없음: human_wait_end stage H3 도 유효한 auto_approval 도 없음. auto_approval 은 policy 가 D-## 형식,
       gt "pass", lazy_caught == lazy_total > 0, residual_defects 0, base_types 에 "unbounded" · "reject-preserve"
       둘 다일 때만 유효. 분류 위험도가 high 이면 auto_approval 은 유효하지 않다(D-28 범위 밖)
    D5 잠금 없음: 그 WO 의 마지막 review_run 이후 시각의 test_lock 이벤트가 없음
    D6 기준 태그 없음 또는 기준 태그 커밋에 config/test_lock.json 이 없음
C 종료 강제: `--check close` → 같은 규칙(종료 0/1, 코드).
    C1 마지막 판정 실행이 통과가 아니거나 target_sha 가 없거나 HEAD 의 조상(같음 포함)이 아님
    C2 그 실행의 failed_cases > 0
    C3 그 실행의 g0_violations > 0 또는 review_required 비지 않음
    C4 H3 증거 없음 (D4 와 같은 규칙)
    C5 지표 누락: prospective 분류 · role implementer 의 model_usage · review_run 중 하나라도 없음
    C6 target_sha..HEAD 변경 경로가 reports/** · docs/HANDOFF.md 밖에 있음, 또는 종료 태그가 이미 있음
    C7 분류 위험도가 medium · high 인데 마지막 판정 실행 이후 review_run 이 없거나, 그 마지막 것의 contract_defects > 0
E 자동 승인 기록: `mark.py auto-approve <D-##> --wo <WO> --gt pass --lazy-caught N/M --residual-defects K
  --base-types a,b --by <확인자>` → 조건(gt pass · N == M > 0 · K == 0 · 두 기본 유형)이 모두 참이면 auto_approval
  {wo, policy, gt, lazy_caught, lazy_total, residual_defects, base_types, by} 기록 · 종료 0. 하나라도 거짓이면 종료 1 ·
  기록 없음 · 출력에 "사람 H3". 형식 오류(policy 형식 · --wo · --by 빈칸 · N/M 형식)는 종료 2 · 기록 없음.
  이 옵션들은 auto-approve 에서만 쓸 수 있다.

사용: python harness/selftest/check_step_cases.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_kpi_cases import PY, events, make_project  # noqa: E402

SLICES = '[slice.WO-51]\ntitle = "t"\nfiles = ["src/a.py"]\n'
T0, T1, T2, T3, T4, T5 = (f"2026-10-19T0{i}:00:00Z" for i in range(6))


def git(p: Path, *a: str) -> str:
    return subprocess.run(["git", *a], cwd=p, capture_output=True, text=True, check=True).stdout.strip()


def commit(p: Path, rel: str, text: str, msg: str) -> str:
    f = p / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text, encoding="utf-8")
    git(p, "add", "-A")
    git(p, "commit", "-qm", msg)
    return git(p, "rev-parse", "HEAD")


def put(p: Path, evs: list[dict]) -> None:
    lines = [json.dumps({"head": "x", "branch": "main", **e}, ensure_ascii=False) for e in evs]
    (p / "reports" / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def step(p: Path, *a: str) -> tuple[int, dict, str]:
    r = subprocess.run([PY, "harness/step.py", "--slice", "WO-51", "--json", *a], cwd=p, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    try:
        doc = json.loads(r.stdout)
    except Exception:
        doc = {}
    return r.returncode, doc, (r.stdout + r.stderr)[-300:]


def codes(doc: dict) -> set[str]:
    return {str(b)[:2] for b in doc.get("blockers", [])}


def cls(risk: str = "low", basis: str = "prospective", by: str = "user") -> dict:
    return {"ts": T0, "kind": "wo_class", "wo": "WO-51", "tier": "T2", "risk": risk, "reason": "r", "basis": basis,
            "confirmed_by": by}


def review(t: str, defects: int | None = 0) -> dict:
    return {"ts": t, "kind": "review_run", "wo": "WO-51", "reviewer": "codex", "round": 1, "contract_defects": defects}


def auto(**over: object) -> dict:
    e = {"ts": T2, "kind": "auto_approval", "wo": "WO-51", "policy": "D-28", "gt": "pass", "lazy_caught": 10,
         "lazy_total": 10, "residual_defects": 0, "base_types": ["unbounded", "reject-preserve"], "by": "lead"}
    e.update(over)
    return e


H3 = {"ts": T2, "kind": "human_wait_end", "wo": "WO-51", "stage": "H3", "decisions": 1}
LOCK = {"ts": T2, "kind": "test_lock", "files": 3}
USAGE = {"ts": T3, "kind": "model_usage", "wo": "WO-51", "role": "implementer", "agent": "agy", "run_id": "r1"}


def verdict(t: str, sha: str, passed: bool = True, **over: object) -> dict:
    e = {"ts": t, "kind": "gate_run", "wo": "WO-51", "slice": "WO-51", "role": "implementer", "cause": "implementer",
         "only": [], "purpose": "verdict", "passed": passed, "failed_cases": 0, "g0_violations": 0,
         "review_required": [], "target_sha": sha, "run_file": f"reports/runs/{t}.json"}
    e.update(over)
    return e


def main() -> int:
    results: list[tuple[str, bool, str]] = []

    def case(name: str, good: bool, detail: str = "") -> None:
        results.append((name, good, detail))

    base = Path(tempfile.mkdtemp(prefix="step-"))
    try:
        p = make_project(base, "flow")
        (p / "config" / "slices.toml").write_text(SLICES, encoding="utf-8", newline="\n")
        commit(p, "config/slices.toml", SLICES, "slices")
        # S1-S5 → S6
        put(p, [cls()])
        rc, d, out = step(p)
        case("S01 증거 없음 → stage S1-S5, 종료 0, next 비지 않음", rc == 0 and d.get("stage") == "S1-S5" and bool(d.get("next")), out)
        put(p, [cls(), review(T1)])
        rc, d, out = step(p)
        case("S02 review_run 있음 · 기준 태그 없음 → S6", d.get("stage") == "S6", out)
        # D: 위임 강제
        rc, d, out = step(p, "--check", "delegate")
        case("D01 S6 에서 위임 검사 → 종료 1, 막힘 D4 · D5 · D6", rc == 1 and {"D4", "D5", "D6"} <= codes(d), f"{rc} {codes(d)}")
        put(p, [review(T1)])
        rc, d, out = step(p, "--check", "delegate")
        case("D02 분류 없음 → D2", rc == 1 and "D2" in codes(d), f"{codes(d)}")
        put(p, [cls(basis="retrospective"), review(T1)])
        rc, d, _ = step(p, "--check", "delegate")
        case("D03 소급 분류만 → D2 (작업 전 배정이 아님)", "D2" in codes(d), f"{codes(d)}")
        put(p, [cls(), review(T1, defects=None)])
        rc, d, _ = step(p, "--check", "delegate")
        case("D04 결함 수 없는 review_run 만 → D3 (S5 누락)", "D3" in codes(d), f"{codes(d)}")
        lock_sha = commit(p, "config/test_lock.json", "{}", "lock")
        git(p, "tag", "-a", "wo51-base", "-m", "base", lock_sha)
        put(p, [cls(), review(T1), H3, LOCK])
        rc, d, out = step(p, "--check", "delegate")
        case("D05 분류 · S5 · H3 · 잠금(검토 뒤) · 기준 태그(잠금 파일 포함) → 종료 0, 막힘 없음", rc == 0 and not d.get("blockers"), out)
        put(p, [cls(), {**LOCK, "ts": T0}, review(T1), H3])
        rc, d, _ = step(p, "--check", "delegate")
        case("D06 잠금이 마지막 검토보다 앞 → D5 (검토 반영 뒤 재잠금 없음)", rc == 1 and "D5" in codes(d), f"{codes(d)}")
        bad_autos = {"gt": auto(gt="fail"), "lazy": auto(lazy_caught=9), "zero": auto(lazy_caught=0, lazy_total=0),
                     "residual": auto(residual_defects=1), "types": auto(base_types=["unbounded"]), "policy": auto(policy="lead")}
        got = {}
        for name, a in bad_autos.items():
            put(p, [cls(), review(T1), a, LOCK])
            got[name] = "D4" in codes(step(p, "--check", "delegate")[1])
        case("D07 조건이 하나라도 거짓인 auto_approval 은 H3 증거가 아님 → D4 (6가지)", all(got.values()), str(got))
        put(p, [cls(), review(T1), auto(), LOCK])
        rc, d, _ = step(p, "--check", "delegate")
        case("D08 유효한 auto_approval (medium 이하) → 위임 가능", rc == 0, f"{rc} {codes(d)}")
        put(p, [cls(risk="high"), review(T1), auto(), LOCK])
        rc, d, _ = step(p, "--check", "delegate")
        case("D09 위험도 high 의 auto_approval → D4 (D-28 범위 밖)", rc == 1 and "D4" in codes(d), f"{codes(d)}")
        q = make_project(base, "nolock")
        commit(q, "config/slices.toml", SLICES, "slices")
        git(q, "tag", "-a", "wo51-base", "-m", "base")
        put(q, [cls(), review(T1), H3, LOCK])
        rc, d, _ = step(q, "--check", "delegate")
        case("D10 기준 태그 커밋에 config/test_lock.json 없음 → D6", rc == 1 and "D6" in codes(d), f"{codes(d)}")

        # S7 → S8 → S10 + C: 종료 강제
        put(p, [cls(), review(T1), H3, LOCK])
        case("S03 기준 태그 있음 · 판정 없음 → S7", step(p)[1].get("stage") == "S7", "")
        impl = commit(p, "src/a.py", "x = 1\n", "impl")
        put(p, [cls(), review(T1), H3, LOCK, USAGE, verdict(T4, impl, passed=False), verdict(T4, impl, passed=True, only=["G1"], purpose="self_check")])
        case("S04 마지막 판정 실패 (뒤의 자체 점검 통과는 무시) → S8", step(p)[1].get("stage") == "S8", "")
        good = [cls(), review(T1), H3, LOCK, USAGE, verdict(T4, impl)]
        put(p, good)
        rc, d, out = step(p)
        case("S05 마지막 판정 통과 → S10", d.get("stage") == "S10", out)
        commit(p, "reports/WO-51-closure.md", "closure\n", "closure")
        commit(p, "docs/HANDOFF.md", "state\n", "handoff")
        rc, d, out = step(p, "--check", "close")
        case("C01 판정 통과 · 판정 뒤 reports · HANDOFF 만 · 증거 완비 (low) → 종료 0", rc == 0 and not d.get("blockers"), out)
        r1, d1, _ = step(p)
        r2, d2, _ = step(p)
        case("S06 같은 입력 두 번 → 같은 JSON (리드 · 세션 무관 복원)", d1 == d2 and bool(d1), "")
        put(p, [cls(), review(T1), H3, LOCK, USAGE, verdict(T4, "0" * 40)])
        case("C02 target_sha 가 HEAD 조상이 아님 (다른 커밋 결과 재사용) → C1", "C1" in codes(step(p, "--check", "close")[1]), "")
        put(p, [cls(), review(T1), H3, LOCK, USAGE, verdict(T4, impl, failed_cases=2)])
        case("C03 failed_cases 2 → C2", "C2" in codes(step(p, "--check", "close")[1]), "")
        put(p, [cls(), review(T1), H3, LOCK, USAGE, verdict(T4, impl, review_required=["src/a.py"])])
        case("C04 review_required 남음 → C3", "C3" in codes(step(p, "--check", "close")[1]), "")
        put(p, [cls(), review(T1), LOCK, USAGE, verdict(T4, impl)])
        case("C05 H3 증거 없음 → C4", "C4" in codes(step(p, "--check", "close")[1]), "")
        put(p, [cls(), review(T1), H3, LOCK, verdict(T4, impl)])
        case("C06 구현 사용량 기록 없음 → C5", "C5" in codes(step(p, "--check", "close")[1]), "")
        put(p, [cls(risk="medium"), review(T1), H3, LOCK, USAGE, verdict(T4, impl)])
        rc, d, _ = step(p, "--check", "close")
        case("C07 medium 인데 판정 뒤 검토(S9) 없음 → C7", rc == 1 and "C7" in codes(d), f"{codes(d)}")
        put(p, [cls(risk="medium"), review(T1), H3, LOCK, USAGE, verdict(T4, impl), review(T5, defects=1)])
        case("C08 medium · 판정 뒤 검토 결함 1 → C7", "C7" in codes(step(p, "--check", "close")[1]), "")
        put(p, [cls(risk="medium"), review(T1), H3, LOCK, USAGE, verdict(T4, impl), review(T5, defects=0)])
        case("C09 medium · 판정 뒤 검토 결함 0 → 종료 0", step(p, "--check", "close")[0] == 0, "")
        put(p, good)
        commit(p, "src/a.py", "x = 2\n", "change after verdict")
        rc, d, _ = step(p, "--check", "close")
        case("C10 판정 뒤 src 변경 → C6", rc == 1 and "C6" in codes(d), f"{codes(d)}")
        # C12 (리드 보정): 판정 뒤 소스를 reports/ 로 이름만 바꿔 옮겨도 C6 (이름 바꾸기 감지로 삭제가 숨으면 안 됨)
        r_ = make_project(base, "rename")
        commit(r_, "config/slices.toml", SLICES, "slices")
        impl_r = commit(r_, "src/a.py", "x = 1\n" * 30, "impl")
        (r_ / "reports").mkdir(exist_ok=True)
        git(r_, "mv", "src/a.py", "reports/a.py")
        git(r_, "commit", "-qm", "move")
        put(r_, [cls(), review(T1), H3, LOCK, USAGE, verdict(T4, impl_r)])
        rc, d, _ = step(r_, "--check", "close")
        case("C12 판정 뒤 src 파일을 reports/ 로 이름 바꿔 이동 → C6", rc == 1 and "C6" in codes(d), f"{rc} {codes(d)}")
        put(r_, [cls(), review(T1), H3, {**LOCK, "ts": "bad"}, review(T1)])
        rc, d, out = step(r_, "--check", "delegate")
        case("C13 (리드 보정) 시각이 깨진 이벤트가 있어도 오류 없이 막힘 판정 (D5)", rc == 1 and "D5" in codes(d), out)
        # R: Codex 레드팀 지적 (리드 재현 뒤 수용)
        put(p, [cls(), review(T1), H3, {**LOCK, "wo": "WO-OTHER"}])
        rc, d, _ = step(p, "--check", "delegate")
        case("R01 다른 WO 의 test_lock 은 잠금 증거가 아님 → D5 (wo 없는 옛 기록은 저장소 전체 잠금으로 인정)",
             rc == 1 and "D5" in codes(d), f"{rc} {codes(d)}")
        put(p, [cls(), review(T1), H3, {**LOCK, "ts": T1}])
        rc, d, _ = step(p, "--check", "delegate")
        case("R02 검토와 같은 초의 잠금이라도 파일 순서상 뒤면 잠금 인정 (오탐 제거)", rc == 0, f"{rc} {codes(d)}")
        put(p, [cls(), review(T1), H3, LOCK, USAGE, verdict(T4, impl, wo="WO-OTHER")])
        rc1 = step(p, "--check", "close")[1]
        put(p, [cls(), review(T1), H3, LOCK, USAGE, verdict(T4, impl, slice="OTHER")])
        rc2 = step(p, "--check", "close")[1]
        case("R03 wo 가 다르거나 slice 가 다른 판정 실행은 대상 판정이 아님 → C1 (둘 다)",
             "C1" in codes(rc1) and "C1" in codes(rc2), f"{codes(rc1)} {codes(rc2)}")
        put(p, [cls(), review(T1), auto(residual_defects=False), LOCK])
        rc, d, _ = step(p, "--check", "delegate")
        case("R04 auto_approval.residual_defects 가 bool false → 유효하지 않음 → D4", rc == 1 and "D4" in codes(d), f"{codes(d)}")
        (p / "reports" / "events.jsonl").write_text("{broken\n", encoding="utf-8")
        r = subprocess.run([PY, "harness/step.py", "--slice", "WO-51", "--check", "close", "--json"], cwd=p,
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        case("R05 events.jsonl 손상 → 종료 2 · 'Traceback' 없음 · 손상 안내 (판정하지 않음)",
             r.returncode == 2 and "Traceback" not in r.stderr and "events.jsonl" in (r.stdout + r.stderr), f"{r.returncode} {r.stderr[-120:]}")
        put(p, good)
        git(p, "tag", "-a", "wo51-closed", "-m", "closed")
        rc, d, _ = step(p)
        case("S07 종료 태그 있음 → closed", d.get("stage") == "closed", "")
        rc, d, _ = step(p, "--check", "close")
        case("C11 종료 태그가 이미 있음 → C6 (태그 이동 금지)", "C6" in codes(d), f"{codes(d)}")
        r = subprocess.run([PY, "harness/step.py", "--slice", "WO-99", "--json"], cwd=p, capture_output=True, text=True)
        case("S08 slices.toml 에 없는 슬라이스 → 종료 2", r.returncode == 2, str(r.returncode))
        r = subprocess.run([PY, "harness/step.py", "--slice", "WO-51"], cwd=p, capture_output=True, text=True, encoding="utf-8")
        case("S09 --json 없음 → 첫 줄 '상태: closed'", r.returncode == 0 and r.stdout.splitlines()[:1] == ["상태: closed"], r.stdout[:80])

        # E: 자동 승인 기록
        e = make_project(base, "auto")
        ok = ["D-28", "--wo", "WO-51", "--gt", "pass", "--lazy-caught", "10/10", "--residual-defects", "0",
              "--base-types", "unbounded,reject-preserve", "--by", "lead"]
        r = subprocess.run([PY, "harness/mark.py", "auto-approve", *ok], cwd=e, capture_output=True, text=True, encoding="utf-8")
        a = [x for x in events(e) if x.get("kind") == "auto_approval"]
        case("E01 조건 전부 참 → auto_approval 1건 (필드 일치), 종료 0",
             r.returncode == 0 and len(a) == 1 and a[0].get("lazy_caught") == 10 and a[0].get("lazy_total") == 10
             and a[0].get("policy") == "D-28" and sorted(a[0].get("base_types", [])) == ["reject-preserve", "unbounded"]
             and a[0].get("residual_defects") == 0 and a[0].get("gt") == "pass" and a[0].get("by") == "lead",
             f"{r.returncode} {a} {(r.stdout + r.stderr)[-150:]}")
        unmet = {"gt": ("--gt", "fail"), "lazy": ("--lazy-caught", "9/10"), "residual": ("--residual-defects", "1"),
                 "types": ("--base-types", "unbounded")}
        res = {}
        for n, (k, v) in unmet.items():
            args = list(ok)
            args[args.index(k) + 1] = v
            rr = subprocess.run([PY, "harness/mark.py", "auto-approve", *args], cwd=e, capture_output=True, text=True, encoding="utf-8")
            res[n] = (rr.returncode, "사람 H3" in rr.stdout + rr.stderr)
        n_after = len([x for x in events(e) if x.get("kind") == "auto_approval"])
        case("E02 조건 하나라도 거짓 → 종료 1 · '사람 H3' 안내 · 기록 없음 (4가지)",
             all(v == (1, True) for v in res.values()) and n_after == 1, f"{res} n={n_after}")
        fmt = {"policy": ("D-28", "lead"), "wo": ("--wo", ""), "by": ("--by", " "), "nm": ("--lazy-caught", "ten")}
        res = {}
        for n, (k, v) in fmt.items():
            args = list(ok)
            args[args.index(k) + (0 if n == "policy" else 1)] = v
            res[n] = subprocess.run([PY, "harness/mark.py", "auto-approve", *args], cwd=e, capture_output=True, text=True).returncode
        rr = subprocess.run([PY, "harness/mark.py", "note", "m", "--wo", "WO-51", "--gt", "pass"], cwd=e, capture_output=True, text=True)
        case("E03 형식 오류 4가지 → 종료 2 · 기록 없음, auto-approve 밖에서 --gt → 종료 2",
             all(c == 2 for c in res.values()) and rr.returncode == 2
             and len([x for x in events(e) if x.get("kind") == "auto_approval"]) == 1, f"{res} note={rr.returncode}")
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
