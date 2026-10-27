"""하네스 자기 검사: 단계 시간 보고 · 종료 초안 · 자동 승인 KPI · 문서 역할 사례 재현성 (WO-H61, HD-60).

Red 작성 = Claude lead. 구현자는 이 파일을 고치지 않는다(RED-PRE-REVIEW §7).

계약
T 단계 시간 보고: `python harness/kpi.py --timing` → 종료 0. reports/tdd-timing.md 를 쓰고 같은 본문을 stdout 에 낸다
  (앞뒤 공백을 뺀 stdout = 파일 본문). reports/kpi.md 는 만들지도 바꾸지도 않는다(기존 바이트 그대로). reports/ 의 다른 파일도 그대로. 기본 `kpi.py`(옵션 없음)는 tdd-timing.md 를 쓰지 않는다.
  두 모드 모두 reports/events.jsonl 바이트를 바꾸지 않는다. 이벤트 파일이 없어도 종료 0.
  (리드 보정 T17) events.jsonl 이 손상되면 두 모드 모두 종료 0 이 아니고 기존 보고서 파일을 바꾸지 않는다.
  절 "## 단계별 시각": 표 열 = WO | 종류 | 구간 | 시작(UTC) | 끝(UTC) | 시간(초) | 출처. 이벤트 원본 순서.
    - 종류 "사람 대기": human_wait_start/end 를 (wo, stage) 별 먼저 연 것부터 짝짓는다. 구간 = stage.
      다른 WO 의 같은 stage 와 짝짓지 않는다. 짝 없는 start → 끝 "진행 중", 시간 "진행 중".
      짝 없는 end → 시작 "unknown", 시간 "unknown". 짝이면 출처 "기록".
    - 종류 "사람 활동": human_start/human_end 를 같은 규칙으로(구간 = stage).
    - 종류 "모델": model_usage 를 (agent, run_id) 로 한 번만. 구간 = "<role>·<agent>·<time_scope>"(없으면 "unknown").
      started · ended 가 있으면 그 문자열 그대로, 출처 "기록". started 가 없고 seconds 가 있으면 시작을 역산
      (ended 가 있으면 ended − seconds, 없으면 ts 를 끝으로 보고 ts − seconds), 출처 "계산".
      seconds 를 모르면 시간 "unknown".
    - 종류 "게이트": gate_run. 구간 = "verdict" | "self_check"(purpose, 없으면 only 가 비지 않으면 self_check).
      끝 = ts, 시작 = ts − seconds, 출처 "계산". seconds 를 모르면 시간 · 시작 "unknown".
    - 종류 "피드백": feedback_run, 구간 "feedback", 게이트와 같은 시간 규칙.
    - 값이 bool · 음수 · 숫자가 아님인 seconds 는 모르는 값(unknown)이다. 깨진 ts 는 그 칸 "unknown", 오류로 끝나지 않는다.
    - 초 표기는 소수 첫째 자리(예: 300.0). 계산한 시각은 %Y-%m-%dT%H:%M:%SZ.
  절 "## WO별 요약": 표 열 = WO | 사람 대기(초) | 진행 중 대기 | 모델 활동 합(초) | 모델 벽시계(초).
    - 사람 대기(초) = 짝지은 대기 시간 합, 짝이 하나도 없으면 "기록 없음". 진행 중 대기 = 짝 없는 start 수.
    - 모델 활동 합(초) = time_scope "model_activity" 인 run 의 seconds 합. 모르는 run 이 있으면
      "<합> 부분 (확인 a/b)", 하나도 모르면 "unknown". delegation_total 등 다른 범위는 더하지 않는다.
    - 모델 벽시계(초) = model_activity run 중 started · ended 가 모두 있는 구간의 합집합 길이. 없으면 "unknown".
C 종료 초안: `python harness/step.py --slice <ID> --closure-draft` → 종료 0, stdout 에만 초안.
  파일 · 이벤트 · 태그를 만들거나 바꾸지 않는다(git status · events.jsonl 바이트 · 태그 목록 그대로).
  - 초안 = templates/slice-closure.md 의 "아래부터 복사해서 쓴다." 다음 부분. 작성 규칙 부분은 넣지 않는다.
    "미판정 초안" 을 표시한다. <슬라이스 ID> · <슬라이스 이름> 은 slices.toml 의 ID · title 로 채운다.
    체크포인트 태그는 설정의 closed_tag 이름(기본 {compact}-closed)으로 쓴다.
  - 마지막 판정 실행(step.py 와 같은 규칙)이 없거나 실패면 "SLICE PASS" 문자열이 어디에도 없다.
    통과면 그 target_sha(앞 7자 이상)를 적는다.
  - 생존 변이 판정 (H9) · 열린 문제 칸 = "미확인". KPI 표: 판정 실행 수 = 그 WO 의 판정 실행 수,
    첫 실행 통과 = 예/아니오, 사람 시간 (H 구간) = human_start/end 짝이 없으면 "기록 없음".
  - --closure-draft 와 --json 또는 --check 를 같이 주면 종료 2. 템플릿이 없으면 종료 2, 초안 출력 없음.
    거부(종료 2)에서도 파일 · 이벤트 · 태그 그대로.
A 자동 승인 KPI: 기본 kpi.py 의 절 "## 자동 승인 적용 현황".
  - 대상 WO = H3 관련 기록(human_wait_start/end stage H3 · auto_approval)이 있는 WO 만. 다른 WO 는 세지도 적지도 않는다.
  - H3 결정 = 원본 순서에서 마지막 유효 결정: human_wait_end stage H3 → "사람", 유효 auto_approval → "자동".
    유효 = step.py D4 규칙(policy D-## · gt "pass" · lazy_caught == lazy_total > 0 이고 둘 다 bool 아닌 int ·
    residual_defects 가 bool 아닌 int 0 · base_types 에 unbounded · reject-preserve) 이고 WO 위험도가 high 아님.
    결정이 없으면 "미확인".
  - 줄: "적용률: <자동>/<자동+사람> (<백분율 소수1>%)", 분모 0 이면 "적용률: N/A". "유효 자동 승인: n" ·
    "사람 승인: n" · "결정 미확인: n" (WO 단위, 같은 WO 반복 기록은 한 번).
  - 표 열 = WO | H3 결정 | 미적용 사유. 자동이면 사유 "—". 사람이면: 위험도 high 이고 auto_approval 기록이 있으면
    "high 범위 밖", 아니면 auto_approval 기록이 있으나 모두 무효면 "승인 근거 불충분", 아니면 "사람 H3 처리·사유 미기록".
    미확인이면 "H3 결정 미확인". 근거 없는 사유(GT 실패 · 후보 미포착 · 사람 거부)를 만들지 않는다.
X 문서 역할 사례 재현성: 바이트코드 쓰기를 끄지 않은 기본 파이썬 환경에서 check_doc_roles_cases.py 가 종료 0,
  R01 ~ R04 모두 OK. R 기대값(ok_paths · bad_paths · R01 ~ R04 case 줄)과 g0 함수 본문은 바꾸지 않는다.

사용: python harness/selftest/check_h61_cases.py
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_kpi_cases import PY, ROOT, make_project  # noqa: E402

SLICES = '[slice.WO-51]\ntitle = "t"\nfiles = ["src/a.py"]\n'


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


def run(p: Path, *a: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([PY, *a], cwd=p, capture_output=True, text=True, encoding="utf-8", errors="replace")


def section(text: str, title: str) -> str | None:
    if f"## {title}" not in text:
        return None
    return text.split(f"## {title}", 1)[1].split("\n## ", 1)[0]


def rows(text: str, title: str) -> list[dict[str, str]]:
    sec = section(text, title)
    if sec is None:
        return []
    lines = [l for l in sec.splitlines() if l.startswith("|")]
    if len(lines) < 2:
        return []
    head = [c.strip() for c in lines[0].strip().strip("|").split("|")]
    return [dict(zip(head, [c.strip() for c in l.strip().strip("|").split("|")])) for l in lines[2:]]


def find(rs: list[dict[str, str]], **kv: str) -> list[dict[str, str]]:
    return [r for r in rs if all(r.get(k) == v for k, v in kv.items())]


def timing(p: Path) -> tuple[int, str, str]:
    r = run(p, "harness/kpi.py", "--timing")
    f = p / "reports" / "tdd-timing.md"
    return r.returncode, (f.read_text(encoding="utf-8") if f.exists() else ""), r.stdout


def mu(run_id: str, wo: str = "WO-A", **over: object) -> dict:
    e = {"ts": "2026-10-20T10:20:00Z", "kind": "model_usage", "wo": wo, "role": "implementer", "agent": "agy",
         "run_id": run_id, "started": "2026-10-20T10:00:00Z", "ended": "2026-10-20T10:10:00Z", "seconds": 600.0,
         "time_scope": "model_activity", "time_source": "measured_activity_intervals"}
    e.update(over)
    return {k: v for k, v in e.items() if v is not None}


def ws(t: str, wo: str, stage: str = "H3") -> dict:
    return {"ts": t, "kind": "human_wait_start", "wo": wo, "stage": stage}


def we(t: str, wo: str, stage: str = "H3") -> dict:
    return {"ts": t, "kind": "human_wait_end", "wo": wo, "stage": stage, "decisions": 1}


def auto(wo: str, **over: object) -> dict:
    e = {"ts": "2026-10-20T11:00:00Z", "kind": "auto_approval", "wo": wo, "policy": "D-28", "gt": "pass",
         "lazy_caught": 4, "lazy_total": 4, "residual_defects": 0, "base_types": ["unbounded", "reject-preserve"],
         "by": "lead"}
    e.update(over)
    return e


def cls(wo: str, risk: str = "low") -> dict:
    return {"ts": "2026-10-20T09:00:00Z", "kind": "wo_class", "wo": wo, "tier": "T2", "risk": risk, "reason": "r",
            "basis": "prospective", "confirmed_by": "user"}


def verdict(t: str, sha: str, passed: bool) -> dict:
    return {"ts": t, "kind": "gate_run", "wo": "WO-51", "slice": "WO-51", "role": "implementer",
            "cause": "implementer", "only": [], "purpose": "verdict", "passed": passed, "failed_cases": 0,
            "g0_violations": 0, "review_required": [], "target_sha": sha, "run_file": f"reports/runs/{t}.json"}


def kpi_text(p: Path) -> tuple[int, str]:
    r = run(p, "harness/kpi.py")
    f = p / "reports" / "kpi.md"
    return r.returncode, (f.read_text(encoding="utf-8") if f.exists() else "")


def line_with(sec: str | None, prefix: str) -> str:
    if not sec:
        return ""
    return next((l.strip() for l in sec.splitlines() if l.strip().startswith(prefix)), "")


# X02: 구현 전 기준의 R 기대값 · g0 본문 지문 (Red 작성 시 고정)
DOC_ROLES_GUARD_SHA = "8ab53b7aa0f775fa9d756eb43194fdc3"  # sha256 앞 32자 (40자 이상은 공개본 누출 검사가 토큰으로 봄)


def doc_roles_guard_text() -> str:
    src = (ROOT / "harness" / "selftest" / "check_doc_roles_cases.py").read_text(encoding="utf-8")
    keep = []
    g0 = re.search(r"^def g0\(.*?(?=^def )", src, re.S | re.M)
    keep.append(g0.group(0) if g0 else "<g0 없음>")
    for l in src.splitlines():
        s = l.strip()
        if s.startswith(("ok_paths =", "bad_paths =", 'case("R01', 'case("R02', 'case("R03', 'case("R04')) \
                or s.startswith('"config/gates.toml"]'):
            keep.append(s)
    return "\n".join(keep)


def main() -> int:
    results: list[tuple[str, bool, str]] = []

    def case(name: str, good: bool, detail: str = "") -> None:
        results.append((name, good, detail))

    base = Path(tempfile.mkdtemp(prefix="h61-"))
    try:
        # ── T: 단계 시간 보고 ──
        p = make_project(base, "timing")
        rc, rep, out = timing(p)
        case("T01 이벤트 파일 없음 → --timing 종료 0 · tdd-timing.md 생성", rc == 0 and bool(rep), out[-200:])

        evs = [
            ws("2026-10-20T10:00:00Z", "WO-A"), we("2026-10-20T10:05:00Z", "WO-A"),
            ws("2026-10-20T10:30:00Z", "WO-A", "H10"),
            ws("2026-10-20T10:00:00Z", "WO-B", "H1"), we("2026-10-20T10:02:00Z", "WO-C", "H1"),
            mu("r1"), mu("r2", started="2026-10-20T10:05:00Z", ended="2026-10-20T10:15:00Z"), mu("r1"),
            mu("r3", time_scope="delegation_total", seconds=1000.0, started=None, ended=None),
            mu("r4", wo="WO-B", seconds=60.0, started=None, ended=None, ts="2026-10-20T12:00:00Z"),
            mu("r5", wo="WO-C", seconds=None, started=None, ended=None),
            mu("r6", wo="WO-C", seconds=120.0, started=None, ended=None),
            mu("r7", wo="WO-D", seconds=True, started=None, ended=None),
            mu("r8", wo="WO-D", seconds=-5, started=None, ended=None, agent="codex"),
            {"ts": "2026-10-20T10:40:00Z", "kind": "gate_run", "wo": "WO-A", "role": "implementer",
             "purpose": "verdict", "only": [], "passed": True, "seconds": 13.0},
            {"ts": "2026-10-20T10:41:00Z", "kind": "gate_run", "wo": "WO-A", "role": "implementer",
             "purpose": "self_check", "only": ["G1"], "passed": True},
            ws("2026-10-20T10:00:00Z", "WO-F", "H2"), ws("2026-10-20T10:10:00Z", "WO-F", "H2"),
            we("2026-10-20T10:20:00Z", "WO-F", "H2"), we("2026-10-20T10:40:00Z", "WO-F", "H2"),
            {"ts": "bad", "kind": "human_wait_start", "wo": "WO-E", "stage": "H3"},
        ]
        put(p, evs)
        before = (p / "reports" / "events.jsonl").read_bytes()
        (p / "reports" / "kpi.md").write_text("KEEP\n", encoding="utf-8")
        (p / "reports" / "other.md").write_text("OTHER\n", encoding="utf-8")
        rc, rep, out = timing(p)
        st = rows(rep, "단계별 시각")
        sm = rows(rep, "WO별 요약")
        case("T02 --timing: 종료 0 · 두 절 표 있음 · stdout 본문 = tdd-timing.md 본문 (검토 H61-02)",
             rc == 0 and "## 단계별 시각" in rep and "## WO별 요약" in rep and bool(st) and bool(sm)
             and out.strip() == rep.strip(), out[-300:])
        case("T03 --timing 은 kpi.md · events.jsonl · 다른 산출물 바이트를 바꾸지 않음 (검토 H61-01)",
             (p / "reports" / "kpi.md").read_text(encoding="utf-8") == "KEEP\n"
             and (p / "reports" / "other.md").read_text(encoding="utf-8") == "OTHER\n"
             and (p / "reports" / "events.jsonl").read_bytes() == before, "")
        w = find(st, WO="WO-A", 종류="사람 대기", 구간="H3")
        case("T04 대기 짝: 시작 · 끝 원본 시각, 300.0초, 출처 기록",
             len(w) == 1 and w[0].get("시작(UTC)") == "2026-10-20T10:00:00Z"
             and w[0].get("끝(UTC)") == "2026-10-20T10:05:00Z" and w[0].get("시간(초)") == "300.0"
             and w[0].get("출처") == "기록", str(w))
        w = find(st, WO="WO-A", 종류="사람 대기", 구간="H10")
        case("T05 짝 없는 대기 시작 → 끝 · 시간 '진행 중' (0초 아님)",
             len(w) == 1 and w[0].get("끝(UTC)") == "진행 중" and w[0].get("시간(초)") == "진행 중", str(w))
        wb = find(st, WO="WO-B", 종류="사람 대기", 구간="H1")
        wc = find(st, WO="WO-C", 종류="사람 대기", 구간="H1")
        case("T06 다른 WO 의 같은 구간은 짝짓지 않음 (B 진행 중 · C 시작 unknown)",
             len(wb) == 1 and wb[0].get("시간(초)") == "진행 중" and len(wc) == 1
             and wc[0].get("시작(UTC)") == "unknown" and wc[0].get("시간(초)") == "unknown", f"{wb} {wc}")
        m1 = find(st, WO="WO-A", 종류="모델", 구간="implementer·agy·model_activity")
        case("T07 모델 run 중복(같은 agent · run_id) 한 번 · started/ended 그대로 · 출처 기록",
             len(m1) == 2 and all(r.get("출처") == "기록" for r in m1)
             and {r.get("시작(UTC)") for r in m1} == {"2026-10-20T10:00:00Z", "2026-10-20T10:05:00Z"}, str(m1))
        m4 = find(st, WO="WO-B", 종류="모델")
        case("T08 started 없음 · seconds 60 → 시작 = ts − 60초 계산 · 출처 계산",
             len(m4) == 1 and m4[0].get("시작(UTC)") == "2026-10-20T11:59:00Z" and m4[0].get("출처") == "계산"
             and m4[0].get("시간(초)") == "60.0", str(m4))
        m3 = find(st, WO="WO-A", 종류="모델", 구간="implementer·agy·delegation_total")
        case("T09 delegation_total run 은 행으로 따로 표시", len(m3) == 1 and m3[0].get("시간(초)") == "1000.0", str(m3))
        a = find(sm, WO="WO-A")
        case("T10 요약: 사람 대기 300.0 · 진행 중 1 · 모델 활동 합 1200.0(위임 총시간 제외) · 벽시계 900.0",
             len(a) == 1 and a[0].get("사람 대기(초)") == "300.0" and a[0].get("진행 중 대기") == "1"
             and a[0].get("모델 활동 합(초)") == "1200.0" and a[0].get("모델 벽시계(초)") == "900.0", str(a))
        c = find(sm, WO="WO-C")
        case("T11 seconds 모르는 run 섞임 → '120.0 부분 (확인 1/2)' · 짝 없으면 사람 대기 '기록 없음' · 벽시계 unknown",
             len(c) == 1 and c[0].get("모델 활동 합(초)") == "120.0 부분 (확인 1/2)"
             and c[0].get("사람 대기(초)") == "기록 없음" and c[0].get("모델 벽시계(초)") == "unknown", str(c))
        d = find(sm, WO="WO-D")
        dm = find(st, WO="WO-D", 종류="모델")
        case("T12 seconds 가 bool · 음수 → unknown (1초 · 음수로 세지 않음)",
             len(d) == 1 and d[0].get("모델 활동 합(초)") == "unknown" and len(dm) == 2
             and all(r.get("시간(초)") == "unknown" for r in dm), f"{d} {dm}")
        g = find(st, WO="WO-A", 종류="게이트", 구간="verdict")
        gs = find(st, WO="WO-A", 종류="게이트", 구간="self_check")
        case("T13 게이트: verdict 13.0초 시작 역산(출처 계산) · seconds 없는 self_check 는 unknown",
             len(g) == 1 and g[0].get("시간(초)") == "13.0" and g[0].get("시작(UTC)") == "2026-10-20T10:39:47Z"
             and g[0].get("출처") == "계산" and len(gs) == 1 and gs[0].get("시간(초)") == "unknown", f"{g} {gs}")
        e_ = find(st, WO="WO-E", 종류="사람 대기")
        case("T14 깨진 ts → 시작 unknown, 오류로 끝나지 않음", len(e_) == 1 and e_[0].get("시작(UTC)") == "unknown", str(e_))
        # T17 (리드 보정): 손상된 events.jsonl 을 빈 목록으로 삼켜 근거 없는 빈 보고서로 기존 보고서를 덮지 않는다
        bad_p = make_project(base, "corrupt")
        (bad_p / "reports" / "kpi.md").write_text("REAL\n", encoding="utf-8")
        (bad_p / "reports" / "tdd-timing.md").write_text("REAL\n", encoding="utf-8")
        (bad_p / "reports" / "events.jsonl").write_text('{"ts": "2026-10-20T10:00:00Z", "kind": "x"}\n{broken\n',
                                                        encoding="utf-8")
        rd = run(bad_p, "harness/kpi.py")
        rt = run(bad_p, "harness/kpi.py", "--timing")
        case("T17 (리드 보정) 손상된 events → 두 모드 모두 종료 0 아님 · kpi.md · tdd-timing.md 그대로",
             rd.returncode != 0 and rt.returncode != 0
             and (bad_p / "reports" / "kpi.md").read_text(encoding="utf-8") == "REAL\n"
             and (bad_p / "reports" / "tdd-timing.md").read_text(encoding="utf-8") == "REAL\n",
             f"{rd.returncode} {rt.returncode}")
        f_ = find(st, WO="WO-F", 종류="사람 대기", 구간="H2")
        case("T16 같은 (wo, stage) 반복 대기는 먼저 연 것부터 짝 (1200.0 · 1800.0, 검토 기본 후보)",
             sorted(r.get("시간(초)") for r in f_) == ["1200.0", "1800.0"], str(f_))
        (p / "reports" / "tdd-timing.md").unlink(missing_ok=True)
        (p / "reports" / "kpi.md").unlink(missing_ok=True)
        put(p, evs[:-1])  # 깨진 ts 행은 기본 kpi.py 계약 밖이라 뺀다
        before = (p / "reports" / "events.jsonl").read_bytes()
        rc = run(p, "harness/kpi.py").returncode
        kt = (p / "reports" / "kpi.md").read_text(encoding="utf-8") if (p / "reports" / "kpi.md").exists() else ""
        case("T15 기본 kpi.py 는 tdd-timing.md 를 쓰지 않고 기존 kpi.md 절을 유지 · events 불변",
             rc == 0 and not (p / "reports" / "tdd-timing.md").exists() and "## 작업 지시서별" in kt
             and "## 단계별 시각" not in kt and (p / "reports" / "events.jsonl").read_bytes() == before, "")

        # ── A: 자동 승인 KPI ──
        q = make_project(base, "auto")
        put(q, [
            cls("WO-1"), ws("2026-10-20T10:00:00Z", "WO-1"), auto("WO-1"), auto("WO-1"),
            cls("WO-2"), auto("WO-2", lazy_caught=True, lazy_total=True), we("2026-10-20T11:30:00Z", "WO-2"),
            cls("WO-3", "high"), auto("WO-3"), we("2026-10-20T11:30:00Z", "WO-3"),
            cls("WO-4"), we("2026-10-20T11:30:00Z", "WO-4"),
            cls("WO-5"), ws("2026-10-20T10:00:00Z", "WO-5"),
            cls("WO-6"), auto("WO-6", residual_defects=False),
            cls("WO-7"), we("2026-10-20T10:30:00Z", "WO-7"), auto("WO-7"),
            cls("WO-8"), auto("WO-8"), we("2026-10-20T11:30:00Z", "WO-8"),
            cls("WO-9"), {"ts": "2026-10-20T10:00:00Z", "kind": "human_note", "wo": "WO-9", "note": "x"},
            mu("u9", wo="WO-9"),
        ])
        before = (q / "reports" / "events.jsonl").read_bytes()
        rc, kt = kpi_text(q)
        sec = section(kt, "자동 승인 적용 현황")
        ar = rows(kt, "자동 승인 적용 현황")
        by = {r.get("WO"): r for r in ar}
        case("A01 기본 kpi.py 에 '자동 승인 적용 현황' 절 · events 불변",
             rc == 0 and sec is not None and (q / "reports" / "events.jsonl").read_bytes() == before, kt[-200:])
        case("A02 유효 auto 2번 기록된 WO 는 자동 1건 · 사유 —",
             by.get("WO-1", {}).get("H3 결정") == "자동" and by.get("WO-1", {}).get("미적용 사유") == "—", str(by.get("WO-1")))
        case("A03 bool 값 auto(무효) 뒤 사람 H3 → 사람 · '승인 근거 불충분'",
             by.get("WO-2", {}).get("H3 결정") == "사람" and by.get("WO-2", {}).get("미적용 사유") == "승인 근거 불충분",
             str(by.get("WO-2")))
        case("A04 high 위험도의 auto 는 무효 · 사람 H3 → 'high 범위 밖'",
             by.get("WO-3", {}).get("H3 결정") == "사람" and by.get("WO-3", {}).get("미적용 사유") == "high 범위 밖",
             str(by.get("WO-3")))
        case("A05 사람 H3 만 → '사람 H3 처리·사유 미기록'",
             by.get("WO-4", {}).get("미적용 사유") == "사람 H3 처리·사유 미기록", str(by.get("WO-4")))
        case("A06 대기 시작만 · 무효 auto 만 → 미확인 · 'H3 결정 미확인'",
             all(by.get(w, {}).get("H3 결정") == "미확인" and by.get(w, {}).get("미적용 사유") == "H3 결정 미확인"
                 for w in ("WO-5", "WO-6")), f"{by.get('WO-5')} {by.get('WO-6')}")
        case("A07 마지막 유효 결정 사용: 사람→자동 = 자동, 자동→사람 = 사람",
             by.get("WO-7", {}).get("H3 결정") == "자동" and by.get("WO-8", {}).get("H3 결정") == "사람",
             f"{by.get('WO-7')} {by.get('WO-8')}")
        case("A08 H3 기록이 없는 WO 는 표에 없음", "WO-9" not in by, str(sorted(by)))
        case("A09 적용률 2/6 (33.3%) · 자동 2 · 사람 4 · 미확인 2",
             line_with(sec, "적용률:") == "적용률: 2/6 (33.3%)" and line_with(sec, "유효 자동 승인:") == "유효 자동 승인: 2"
             and line_with(sec, "사람 승인:") == "사람 승인: 4" and line_with(sec, "결정 미확인:") == "결정 미확인: 2",
             f"{line_with(sec, '적용률:')} | {line_with(sec, '유효 자동 승인:')} | {line_with(sec, '사람 승인:')} | "
             f"{line_with(sec, '결정 미확인:')}")
        bad_reasons = [r for r in ar if any(x in r.get("미적용 사유", "") for x in ("GT 실패", "후보 미포착", "사람 거부"))]
        case("A10 근거 없는 사유(GT 실패 · 후보 미포착 · 사람 거부) 없음", not bad_reasons, str(bad_reasons))
        q2 = make_project(base, "auto-na")
        put(q2, [cls("WO-5"), ws("2026-10-20T10:00:00Z", "WO-5")])
        rc, kt2 = kpi_text(q2)
        case("A11 분모 0 → '적용률: N/A'", line_with(section(kt2, "자동 승인 적용 현황"), "적용률:") == "적용률: N/A",
             line_with(section(kt2, "자동 승인 적용 현황"), "적용률:"))

        # ── C: 종료 초안 ──
        s = make_project(base, "closure")
        (s / "templates").mkdir(exist_ok=True)
        shutil.copy(ROOT / "templates" / "slice-closure.md", s / "templates" / "slice-closure.md")
        # 기본 환경에서 생기는 바이트코드가 git status 를 더럽히지 않게 한다 (R01 과 같은 함정)
        (s / ".gitignore").write_text("__pycache__/\n*.pyc\n", encoding="utf-8")
        commit(s, "config/slices.toml", SLICES, "slices")
        git(s, "tag", "-a", "wo51-base", "-m", "base")
        impl = commit(s, "src/a.py", "x = 1\n", "impl")
        put(s, [cls("WO-51"), verdict("2026-10-20T10:00:00Z", impl, False), verdict("2026-10-20T10:30:00Z", impl, True)])
        git(s, "add", "-A")
        git(s, "commit", "-qm", "events")
        ev_before = (s / "reports" / "events.jsonl").read_bytes()
        tags_before = git(s, "tag", "-l")
        r = run(s, "harness/step.py", "--slice", "WO-51", "--closure-draft")
        o = r.stdout
        case("C01 --closure-draft: 종료 0 · '미판정 초안' · 제목 줄 채움 · 작성 규칙 없음",
             r.returncode == 0 and "미판정 초안" in o and "# WO-51 슬라이스 종료 기록 (t)" in o
             and "아래부터 복사해서 쓴다" not in o and "<슬라이스 ID>" not in o and "<슬라이스 이름>" not in o,
             (r.stdout + r.stderr)[-300:])
        case("C02 파일 · 이벤트 · 태그 그대로",
             git(s, "status", "--porcelain") == "" and (s / "reports" / "events.jsonl").read_bytes() == ev_before
             and git(s, "tag", "-l") == tags_before, git(s, "status", "--porcelain"))
        case("C03 통과 판정 → target_sha 표시 · 체크포인트 태그 = wo51-closed",
             impl[:7] in o and "wo51-closed" in o, "")
        h9 = next((l for l in o.splitlines() if l.startswith("| 생존 변이 판정 (H9) |")), "")
        op = next((l for l in o.splitlines() if l.startswith("| 열린 문제 |")), "")
        case("C04 H9 · 열린 문제 = 미확인", "미확인" in h9 and "미확인" in op, f"{h9} / {op}")
        n = next((l for l in o.splitlines() if l.startswith("| 판정 실행 수 |")), "")
        f1 = next((l for l in o.splitlines() if l.startswith("| 첫 실행 통과 |")), "")
        hm = next((l for l in o.splitlines() if l.startswith("| 사람 시간 (H 구간) |")), "")
        case("C05 KPI: 판정 실행 수 2 · 첫 실행 통과 아니오 · 사람 시간 기록 없음",
             re.match(r"\| 판정 실행 수 \| 2 \|", n) is not None and re.match(r"\| 첫 실행 통과 \| 아니오 \|", f1) is not None
             and re.match(r"\| 사람 시간 \(H 구간\) \| 기록 없음 \|", hm) is not None, f"{n} / {f1} / {hm}")
        put(s, [cls("WO-51"), verdict("2026-10-20T10:00:00Z", impl, False)])
        r = run(s, "harness/step.py", "--slice", "WO-51", "--closure-draft")
        case("C06 마지막 판정 실패 → 'SLICE PASS' 없음", r.returncode == 0 and "SLICE PASS" not in r.stdout,
             r.stdout[-200:])
        put(s, [cls("WO-51")])
        r = run(s, "harness/step.py", "--slice", "WO-51", "--closure-draft")
        case("C07 판정 기록 없음 → 'SLICE PASS' 없음", r.returncode == 0 and "SLICE PASS" not in r.stdout,
             r.stdout[-200:])
        def snap() -> tuple[str, bytes, str]:
            return git(s, "status", "--porcelain"), (s / "reports" / "events.jsonl").read_bytes(), git(s, "tag", "-l")

        s0 = snap()
        r1 = run(s, "harness/step.py", "--slice", "WO-51", "--closure-draft", "--json")
        r2 = run(s, "harness/step.py", "--slice", "WO-51", "--closure-draft", "--check", "close")
        case("C08 --json · --check 와 함께 → 종료 2 · 파일 · 이벤트 · 태그 그대로 (검토 H61-03)",
             r1.returncode == 2 and r2.returncode == 2 and snap() == s0, f"{r1.returncode} {r2.returncode}")
        (s / "templates" / "slice-closure.md").unlink()
        s0 = snap()
        r = run(s, "harness/step.py", "--slice", "WO-51", "--closure-draft")
        case("C09 템플릿 없음 → 종료 2 · 초안 없음 · 파일 · 이벤트 · 태그 그대로 (검토 H61-03)",
             r.returncode == 2 and "슬라이스 종료 기록" not in r.stdout and "SLICE PASS" not in r.stdout
             and snap() == s0, (r.stdout + r.stderr)[-200:])

        # ── X: 문서 역할 사례 재현성 ──
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONDONTWRITEBYTECODE", "PYTHONPYCACHEPREFIX")}
        r = subprocess.run([PY, "harness/selftest/check_doc_roles_cases.py"], cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=env, timeout=600)
        ok_r = all(re.search(rf"^OK\s+R0{i}\b", r.stdout, re.M) for i in range(1, 5))
        case("X01 바이트코드 쓰기 켠 기본 환경: doc_roles 종료 0 · R01 ~ R04 OK", r.returncode == 0 and ok_r,
             "\n".join(l for l in r.stdout.splitlines() if l.startswith("BAD"))[-300:])
        got = hashlib.sha256(doc_roles_guard_text().encode("utf-8")).hexdigest()
        case("X02 R 기대값 · g0 본문 불변 (권한을 넓히거나 사례를 지워 통과시키지 않음)",
             got[:32] == DOC_ROLES_GUARD_SHA, got[:32])
    finally:
        shutil.rmtree(base, ignore_errors=True)

    bad = [r for r in results if not r[1]]
    for name, good, detail in results:
        print(f"{'OK ' if good else 'BAD'} {name}" + ("" if good else f"  — {detail}"))
    print("결과: " + ("모든 사례가 기대와 일치" if not bad else "기대와 다른 사례가 있다"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
