"""하네스 자기 검사: 작업 지시서 분류 기록 wo_class (HD-43 §4, WO-H35).

계약
W 기록 (mark.py):
  `classify <근거> --wo <WO> --tier T1|T2|T3 --risk low|medium|high --basis prospective|retrospective --by <확인자>`
  → wo_class 이벤트 {wo, tier, risk, reason, basis, confirmed_by}. 근거 · --wo · --by 는 비어 있으면 안 되고, 값이 목록 밖이면
  종료 2 · 기록 없음. --tier · --risk · --basis · --by 는 classify 에서만 쓸 수 있다(다른 동작에 주면 종료 2).
  추가만 한다: 같은 WO 를 다시 분류하면 이벤트가 하나 더 생긴다(덮어쓰기 · 중복 제거 없음).
X 해석 (kpi.py):
  - wo_class_for(이벤트, wo) → {"tier", "risk", "basis"} 또는 None. prospective 기록이 있으면 그중 가장 나중 것,
    없으면 retrospective 중 가장 나중 것. 소급 기록은 작업 전 배정을 덮지 못한다. confirmed_by 가 비었거나 tier · risk ·
    basis 가 목록 밖인 기록은 무시한다. “나중” = 이벤트 순서(파일 순서).
  - 채우기: model_usage 의 tier · risk 가 None 인 필드만 그 WO 의 분류 값으로 채운다. 값이 있는 필드는 덮어쓰지 않는다.
  - time_observations(이벤트, prospective_only=False): 기존 계약에 채우기를 더한다. prospective_only=True 이면
    wo_class_for(...)["basis"] == "prospective" 인 WO 의 기록만 남긴다(model_usage 에 값이 직접 있어도 분류가 없으면 제외).
  - “## 기본값 보정 제안 (적용은 사용자 확인)”: 관측은 prospective_only=True. 기존 “티어·위험도 미상: N건 제외” 줄
    (채운 뒤에도 tier 나 risk 가 없는 기록 수)에 더해 “작업 전 배정 없음: M건 제외” 줄(채운 뒤 값은 있으나 WO 에
    prospective 분류가 없는 기록 수, 중복 run 한 번).
  - “## 작업 지시서별” 표에 “분류” 열: wo_class_for 결과를 “T2 · low · 작업 전” / “T2 · low · 소급”, 없으면 “—”.
  - kpi.py 는 이벤트 파일을 바꾸지 않는다.

사용: python harness/selftest/check_wo_class_cases.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable
sys.path.insert(0, str(ROOT / "harness"))

PROPOSAL = "기본값 보정 제안 (적용은 사용자 확인)"


def make_project(base: Path, name: str) -> Path:
    proj = base / name
    shutil.copytree(ROOT / "harness", proj / "harness", ignore=shutil.ignore_patterns("__pycache__", "selftest"))
    shutil.copytree(ROOT / "adapters", proj / "adapters", ignore=shutil.ignore_patterns("__pycache__"))
    (proj / "config").mkdir()
    (proj / "config" / "gates.toml").write_text('[harness]\nadapter = "python"\n', encoding="utf-8", newline="\n")
    (proj / "reports").mkdir()
    for cmd in (["git", "init", "-q", "-b", "main"], ["git", "config", "user.email", "t@example.invalid"],
                ["git", "config", "user.name", "t"], ["git", "add", "-A"], ["git", "commit", "-qm", "base"]):
        subprocess.run(cmd, cwd=proj, capture_output=True, text=True, check=True)
    return proj


def run(proj: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([PY, *args], cwd=proj, capture_output=True, text=True, encoding="utf-8", errors="replace")


def events(proj: Path) -> list[dict]:
    p = proj / "reports" / "events.jsonl"
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()] if p.exists() else []


def write_events(proj: Path, evs: list[dict]) -> None:
    lines = [json.dumps({"ts": "2026-10-18T10:00:00Z", "head": "abc1234", "branch": "main", **e}, ensure_ascii=False)
             for e in evs]
    (proj / "reports" / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def kpi_section(proj: Path, title: str) -> tuple[list[dict[str, str]], str]:
    r = run(proj, "harness/kpi.py")
    if r.returncode != 0:
        return [], f"kpi.py 실패: {(r.stdout + r.stderr)[-200:]}"
    text = (proj / "reports" / "kpi.md").read_text(encoding="utf-8")
    if f"## {title}" not in text:
        return [], f"절 없음: {title}"
    section = text.split(f"## {title}", 1)[1].split("\n## ", 1)[0]
    rows = [l for l in section.splitlines() if l.startswith("|")]
    if len(rows) < 2:
        return [], section
    head = [c.strip() for c in rows[0].strip("|").split("|")]
    return [dict(zip(head, [c.strip() for c in l.strip("|").split("|")])) for l in rows[2:]], section


def usage(run_id: str, wo: str = "WO-X1", **f: object) -> dict:
    e = {"kind": "model_usage", "wo": wo, "task": "T1", "role": "implementer", "agent": "a1", "model": "m1",
         "run_id": run_id, "round": 1, "started": "2026-10-18T10:00:00Z", "seconds": 300.0,
         "time_scope": "model_activity", "time_source": "measured_activity_intervals", "cap": None, "stop": None, "tier": None, "risk": None,
         "condition": {"exec_path": "p1", "permission": "edit"}, "source": "fixture"}
    e.update(f)
    return e


def cls(wo: str, tier: str = "T2", risk: str = "low", basis: str = "prospective", by: str | None = "user") -> dict:
    return {"kind": "wo_class", "wo": wo, "tier": tier, "risk": risk, "reason": "근거", "basis": basis,
            "confirmed_by": by}


def main() -> int:
    base = Path(tempfile.mkdtemp(prefix="wo-class-"))
    rows: list[tuple[str, bool, str]] = []

    def case(name: str, good: bool, detail: str = "") -> None:
        rows.append((name, good, detail.replace("\n", " | ")[:170]))

    try:
        import kpi  # noqa: E402

        # ── W 기록 ──
        p = make_project(base, "w")
        ok_args = ["--wo", "WO-X1", "--tier", "T2", "--risk", "low", "--basis", "prospective", "--by", "user"]
        r = run(p, "harness/mark.py", "classify", "예상 피해 작음 · 두 파일 · revert 가능", *ok_args)
        e = events(p)
        case("W01 classify → wo_class (wo · tier · risk · reason · basis · confirmed_by)", r.returncode == 0 and len(e) == 1
             and e[0].get("kind") == "wo_class" and e[0].get("wo") == "WO-X1" and e[0].get("tier") == "T2"
             and e[0].get("risk") == "low" and e[0].get("basis") == "prospective" and e[0].get("confirmed_by") == "user"
             and e[0].get("reason") == "예상 피해 작음 · 두 파일 · revert 가능", f"{r.returncode} {e} {r.stdout[-80:]}")

        def bad(*replace: str) -> list[str]:
            args = list(ok_args)
            for i in range(0, len(replace), 2):
                flag, val = replace[i], replace[i + 1]
                j = args.index(flag)
                if val == "":
                    del args[j:j + 2]
                else:
                    args[j + 1] = val
            return args
        variants = {
            "tier T4": bad("--tier", "T4"), "risk mid": bad("--risk", "mid"), "basis guess": bad("--basis", "guess"),
            "--by 없음": bad("--by", ""), "--wo 없음": bad("--wo", ""), "--tier 없음": bad("--tier", ""),
            "--risk 없음": bad("--risk", ""), "--basis 없음": bad("--basis", ""), "--by 공백": bad("--by", " "),
        }
        codes = {k: run(p, "harness/mark.py", "classify", "근거", *v).returncode for k, v in variants.items()}
        r_empty = run(p, "harness/mark.py", "classify", "  ", *ok_args)
        codes["근거 공백"] = r_empty.returncode
        case("W02 classify 입력 오류(목록 밖 · 누락 · 공백) → 2, 기록 없음",
             all(c == 2 for c in codes.values()) and len(events(p)) == 1, f"{codes} n={len(events(p))}")
        rn = run(p, "harness/mark.py", "note", "메모", "--wo", "WO-X1", "--tier", "T2")
        rb = run(p, "harness/mark.py", "budget-stop", "이유", "--wo", "WO-X1", "--by", "user")
        case("W03 --tier · --by 는 classify 전용 (다른 동작 → 2, 기록 없음)",
             rn.returncode == 2 and rb.returncode == 2 and len(events(p)) == 1, f"{rn.returncode},{rb.returncode}")
        r2 = run(p, "harness/mark.py", "classify", "범위 변경으로 재배정", *bad("--risk", "medium"))
        e = events(p)
        case("W04 같은 WO 재분류는 추가 (덮어쓰기 · 중복 제거 없음)", r2.returncode == 0 and len(e) == 2
             and [x.get("risk") for x in e] == ["low", "medium"], f"{r2.returncode} {[x.get('risk') for x in e]}")

        # ── X 해석 ──
        def resolve(evs: list[dict], wo: str = "WO-X1") -> object:
            try:
                return kpi.wo_class_for(evs, wo)
            except Exception as ex:
                return repr(ex)
        got = {
            "p1→r": resolve([cls("WO-X1", risk="low"), cls("WO-X1", risk="high", basis="retrospective")]),
            "r→p1": resolve([cls("WO-X1", risk="high", basis="retrospective"), cls("WO-X1", risk="low")]),
            "p1→p2": resolve([cls("WO-X1", risk="low"), cls("WO-X1", risk="medium")]),
            "r1→r2": resolve([cls("WO-X1", risk="low", basis="retrospective"),
                              cls("WO-X1", risk="high", basis="retrospective")]),
            "없음": resolve([cls("WO-X2")]),
        }
        want = {"p1→r": ("low", "prospective"), "r→p1": ("low", "prospective"), "p1→p2": ("medium", "prospective"),
                "r1→r2": ("high", "retrospective"), "없음": None}
        norm = {k: ((v.get("risk"), v.get("basis")) if isinstance(v, dict) else v) for k, v in got.items()}
        case("X01 wo_class_for: prospective 중 최근 > retrospective 중 최근, 소급은 작업 전을 덮지 못함",
             norm == want, str(norm))
        got = {
            "확인자 없음": resolve([cls("WO-X1", by=None)]), "확인자 공백": resolve([cls("WO-X1", by=" ")]),
            "목록 밖": resolve([cls("WO-X1", risk="mid")]),
            "무효 뒤 유효": resolve([cls("WO-X1", risk="low"), cls("WO-X1", risk="high", by="")]),
        }
        norm = {k: ((v.get("risk"), v.get("basis")) if isinstance(v, dict) else v) for k, v in got.items()}
        case("X02 확인자 없음 · 값이 목록 밖인 기록은 무시",
             norm == {"확인자 없음": None, "확인자 공백": None, "목록 밖": None, "무효 뒤 유효": ("low", "prospective")},
             str(norm))

        def keys(evs: list[dict], prospective_only: bool = False) -> object:
            try:
                g = kpi.time_observations(evs, prospective_only=prospective_only)
            except Exception as ex:
                return repr(ex)
            return sorted((k[0], k[1], len(v)) for k, v in g.items())
        got = keys([cls("WO-X1", tier="T2", risk="low"), usage("f1"),
                    usage("f2", task="T2", tier="T1"), usage("f3", task="T3", risk="high")])
        case("X03 채우기: 빈 필드만 분류 값으로 (T2·low ×1, T1·low ×1, T2·high ×1)",
             got == [("T1", "low", 1), ("T2", "high", 1), ("T2", "low", 1)], str(got))
        evs = [cls("WO-P"), usage("g1", wo="WO-P"),
               cls("WO-R", basis="retrospective"), usage("g2", wo="WO-R"),
               usage("g3", wo="WO-D", tier="T2", risk="low"),
               cls("WO-PR"), cls("WO-PR", risk="high", basis="retrospective"), usage("g4", wo="WO-PR")]
        all_ = keys(evs)
        pro = keys(evs, prospective_only=True)
        case("X04 prospective_only: 작업 전 분류가 있는 WO 만 (소급 · 직접 값만 있는 WO 제외), 기본값은 모두",
             all_ == [("T2", "low", 4)] and pro == [("T2", "low", 2)], f"all={all_} pro={pro}")

        # 제안 절: 작업 전 분류 WO 5개 → 제안, 직접 값만 2건 · 소급 1건 제외, 미상 1건
        evs = []
        for i in range(5):
            evs += [cls(f"WO-A{i}"), usage(f"h{i}", wo=f"WO-A{i}", seconds=60.0 * (i + 1))]
        evs += [usage("d1", wo="WO-D1", tier="T2", risk="low"), usage("d2", wo="WO-D2", tier="T2", risk="low"),
                usage("d2", wo="WO-D2", tier="T2", risk="low"),  # 중복 run 은 한 번
                cls("WO-R1", basis="retrospective"), usage("r1", wo="WO-R1"),
                usage("u1", wo="WO-U1")]
        p2 = make_project(base, "x")
        write_events(p2, evs)
        before = (p2 / "reports" / "events.jsonl").read_bytes()
        prop_rows, raw = kpi_section(p2, PROPOSAL)
        val = [r_.get("제안 값(초)") for r_ in prop_rows]
        tail = raw.split("작업 전 배정 없음", 1)[1].split("건", 1)[0] if "작업 전 배정 없음" in raw else ""
        mtail = raw.split("미상", 1)[1].split("건", 1)[0] if "미상" in raw else ""
        case("X05 제안 절: 작업 전 분류 5건으로 240 제안, “작업 전 배정 없음: 3건”, “미상: 1건”",
             val == ["240"] and tail.strip(" :") == "3" and mtail.strip(" :") == "1", raw[:200])
        wo_rows, _ = kpi_section(p2, "작업 지시서별")
        by = {r_.get("WO"): r_.get("분류") for r_ in wo_rows}
        case("X06 작업 지시서별 “분류” 열: 작업 전 · 소급 · 없음(—)",
             by.get("WO-A0") == "T2 · low · 작업 전" and by.get("WO-R1") == "T2 · low · 소급"
             and by.get("WO-D1") == "—", str(by)[:170])
        case("X07 kpi.py 는 이벤트 파일을 바꾸지 않는다", (p2 / "reports" / "events.jsonl").read_bytes() == before)
    finally:
        shutil.rmtree(base, ignore_errors=True)

    all_good = True
    for name, good, detail in rows:
        all_good &= good
        print(f"{'OK ' if good else 'BAD'} {name}  — {detail}")
    print("결과: 모든 사례가 기대와 일치" if all_good else "결과: 기대와 다른 사례가 있다")
    return 0 if all_good else 1


if __name__ == "__main__":
    sys.exit(main())
