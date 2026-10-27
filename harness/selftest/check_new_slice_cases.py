"""하네스 자기 검사: 슬라이스 초안 생성 new_slice.py (WO-H46, 계획 문서 §3.6).

배경: 슬라이스마다 리드가 slices.toml · assignments.toml 블록, 지시서, 자동 승인 근거를 손으로 만든다(시험 저장소 WO-01 ~ 03b).
설정 파일은 사람 소유(H10)라 도구는 **초안만** 만들고 config/ 를 쓰지 않는다.

계약
사용: `python harness/new_slice.py --slice <ID> --title <제목> --files <a,b> --tests <t1,t2> --risk low|medium|high
       [--depends <ID,ID>] [--policy D-##] [--write]`
N 초안 (기본 = 쓰지 않음): 종료 0, stdout 에 4 절. 각 절은 정확히 `### <이름>` 한 줄로 시작한다:
  `### config/slices.toml` · `### config/assignments.toml` · `### work_orders/<ID>.md` · `### 자동 승인 참조`.
  같은 입력 → 같은 출력. 어떤 파일도 만들거나 바꾸지 않는다.
  - slices 절: 기존 config/slices.toml 뒤에 붙여도 TOML 로 읽히는 블록. [slice.<ID>] title · files(목록) · risk ·
    depends(목록, 없으면 빈 목록) · [slice.<ID>.mutation_config] = 어댑터 함수 `mutation_config_draft(files, tests) -> dict`
    의 결과(Core 는 도구 키 이름을 모른다). python 어댑터는 source_paths = files ·
    pytest_add_cli_args_test_selection = tests 를 돌려준다(다른 키를 더해도 된다).
  - assignments 절: 기존 config/assignments.toml 뒤에 붙여도 TOML 로 읽히는 블록. [slice.<ID>] 는 그 파일의 **마지막
    슬라이스와 같은 역할 키 · 값**, status = "open", reason 에 "new_slice" 포함. 기존 슬라이스가 없으면 TOML 블록 대신
    `python harness/roles.py --preset` 이 든 주석 줄.
  - 지시서 절: `id: <ID>` · `slice: <ID>` · `base: <compact>-base`(kpi 태그 규칙) · `agent: <assignments 초안의 implementer>`
    줄, 모든 files · tests 경로, 대화 규약 `QUESTION:` 과 `DONE`.
  - 자동 승인 참조 절: `python harness/mark.py auto-approve <policy> --wo <ID>` 로 시작하는 명령 줄(기본 policy D-28).
    risk high 이면 명령 줄 없이 "자동 승인 범위 밖" 문구.
E 오류 → 종료 2 · 아무것도 쓰지 않음: ID 형식 밖(mark.py 와 같은 규칙), 이미 slices.toml 에 있는 ID, slices.toml 에 없는
  depends, risk 목록 밖, --files · --tests 빈칸, --policy 형식 밖(D-##).
W 쓰기 (`--write`): work_orders/<ID>.md(지시서 절 본문과 같음) · reports/<ID>-config-draft.toml(slices 절 + assignments 절)
  · reports/<ID>-h3-auto-ref.md(자동 승인 참조 절)를 만든다. config/ 는 바이트 그대로. 셋 중 하나라도 이미 있으면 종료 2 ·
  아무것도 쓰지 않음(덮어쓰기 없음).

사용: python harness/selftest/check_new_slice_cases.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_kpi_cases import PY, make_project  # noqa: E402

SLICES = '[slice.WO-01]\ntitle = "money"\nfiles = ["src/p/money.py"]\n'
ASSIGN = ('[slice.WO-01]\nstatus = "closed"\nspec_author = "claude"\ntest_author = "claude"\nimplementer = "agy"\n'
          'adversary = "codex"\nreason = "역할 프리셋 claude-lead"\n')
ARGS = ["--slice", "WO-61", "--title", "지출 기록", "--files", "src/p/expense.py,src/p/ledger.py",
        "--tests", "tests/unit/test_expense.py", "--risk", "low", "--depends", "WO-01"]


def run(p: Path, *a: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([PY, "harness/new_slice.py", *a], cwd=p, capture_output=True, text=True, encoding="utf-8", errors="replace")


def sections(out: str) -> dict[str, str]:
    res: dict[str, str] = {}
    cur = None
    for line in out.splitlines(keepends=True):
        if line.startswith("### "):
            cur = line[4:].strip()
            res[cur] = ""
        elif cur is not None:
            res[cur] += line
    return res


def snapshot(p: Path) -> dict[str, bytes]:
    return {str(f.relative_to(p)): f.read_bytes() for f in p.rglob("*")
            if f.is_file() and ".git" not in f.parts and "__pycache__" not in f.parts}


def main() -> int:
    results: list[tuple[str, bool, str]] = []

    def case(name: str, good: bool, detail: str = "") -> None:
        results.append((name, good, detail))

    base = Path(tempfile.mkdtemp(prefix="newslice-"))
    try:
        p = make_project(base, "proj")
        (p / "config" / "slices.toml").write_text(SLICES, encoding="utf-8", newline="\n")
        (p / "config" / "assignments.toml").write_text(ASSIGN, encoding="utf-8", newline="\n")
        before = snapshot(p)
        r = run(p, *ARGS)
        s = sections(r.stdout)
        want = {"config/slices.toml", "config/assignments.toml", "work_orders/WO-61.md", "자동 승인 참조"}
        case("N01 초안: 종료 0 · 4 절 · 파일 변경 없음", r.returncode == 0 and set(s) == want and snapshot(p) == before,
             f"{r.returncode} {sorted(s)} {(r.stderr)[-200:]}")
        try:
            sl = tomllib.loads(SLICES + "\n" + s.get("config/slices.toml", "")).get("slice", {}).get("WO-61", {})
        except Exception as err:
            sl = {"_err": str(err)}
        mc = sl.get("mutation_config", {})
        case("N02 slices 절: 붙여도 TOML · title · files · risk · depends · mutation_config 일치",
             sl.get("title") == "지출 기록" and sl.get("files") == ["src/p/expense.py", "src/p/ledger.py"] and sl.get("risk") == "low"
             and sl.get("depends") == ["WO-01"] and mc.get("source_paths") == sl.get("files")
             and mc.get("pytest_add_cli_args_test_selection") == ["tests/unit/test_expense.py"], str(sl))
        try:
            asg = tomllib.loads(ASSIGN + "\n" + s.get("config/assignments.toml", "")).get("slice", {}).get("WO-61", {})
        except Exception as err:
            asg = {"_err": str(err)}
        case("N03 assignments 절: 마지막 슬라이스 역할 복사 · status open · reason 에 new_slice",
             all(asg.get(k) == v for k, v in (("spec_author", "claude"), ("test_author", "claude"), ("implementer", "agy"),
                                               ("adversary", "codex"))) and asg.get("status") == "open"
             and "new_slice" in str(asg.get("reason", "")) and "mutation_auditor" not in asg, str(asg))
        wo = s.get("work_orders/WO-61.md", "")
        case("N04 지시서 절: id · slice · base(wo61-base) · agent(agy) · 경로 전부 · QUESTION: · DONE",
             all(x in wo for x in ("id: WO-61", "slice: WO-61", "base: wo61-base", "agent: agy", "src/p/expense.py",
                                   "src/p/ledger.py", "tests/unit/test_expense.py", "QUESTION:", "DONE")), wo[:200])
        ref = s.get("자동 승인 참조", "")
        case("N05 자동 승인 참조: auto-approve D-28 --wo WO-61 명령 줄",
             any(l.strip().startswith("python harness/mark.py auto-approve D-28 --wo WO-61") for l in ref.splitlines()), ref[:200])
        rh = run(p, *[("high" if a == "low" else a) for a in ARGS])
        ref_h = sections(rh.stdout).get("자동 승인 참조", "")
        case("N06 risk high → 명령 줄 없음 · '자동 승인 범위 밖'",
             rh.returncode == 0 and "auto-approve" not in ref_h and "자동 승인 범위 밖" in ref_h, ref_h[:200])
        rp = run(p, *ARGS, "--policy", "D-40")
        case("N07 --policy D-40 → 명령 줄의 정책 번호", "auto-approve D-40 --wo WO-61" in rp.stdout, "")
        case("N08 같은 입력 두 번 → 같은 출력", run(p, *ARGS).stdout == r.stdout, "")
        p2 = make_project(base, "empty")
        (p2 / "config" / "slices.toml").write_text(SLICES, encoding="utf-8", newline="\n")
        (p2 / "config" / "assignments.toml").write_text("# 비어 있음\n", encoding="utf-8", newline="\n")
        r2 = run(p2, *ARGS)
        a2 = sections(r2.stdout).get("config/assignments.toml", "")
        case("N09 기존 배정 없음 → assignments 절은 roles.py --preset 주석",
             r2.returncode == 0 and "python harness/roles.py --preset" in a2 and "[slice." not in a2, a2[:200])

        # N10 (리드 보정): 참조 명령에 실제 값처럼 보이는 숫자를 미리 채우지 않는다 (확인 안 한 근거 기록 방지)
        cmd = next((l for l in ref.splitlines() if "auto-approve" in l), "")
        case("N10 자동 승인 참조의 --lazy-caught 는 자리표시자 (숫자 N/M 아님)",
             "--lazy-caught" in cmd and not __import__("re").search(r"--lazy-caught \d+/\d+", cmd), cmd)
        p3 = make_project(base, "broken")
        (p3 / "config" / "slices.toml").write_text("[slice.WO-01\n", encoding="utf-8", newline="\n")
        (p3 / "config" / "assignments.toml").write_text(ASSIGN, encoding="utf-8", newline="\n")
        r3 = run(p3, *[a for a in ARGS if a not in ("--depends", "WO-01")])
        case("E02 (리드 보정) slices.toml 이 TOML 로 안 읽히면 종료 2 (빈 설정으로 보지 않음)", r3.returncode == 2, str(r3.returncode))

        # E: 오류
        def with_(k: str, v: str) -> list[str]:
            a = list(ARGS)
            a[a.index(k) + 1] = v
            return a
        bad = {"ID 형식": with_("--slice", "wo 61"), "이미 있음": with_("--slice", "WO-01"), "depends 없음": with_("--depends", "WO-09"),
               "risk": with_("--risk", "low-ish"), "files 빈칸": with_("--files", " "), "tests 빈칸": with_("--tests", ""),
               "policy": [*ARGS, "--policy", "lead"]}
        before = snapshot(p)
        codes = {n: run(p, *a, "--write").returncode for n, a in bad.items()}
        case("E01 오류 7가지 → 종료 2 · 아무것도 쓰지 않음 (--write 여도)", all(c == 2 for c in codes.values()) and snapshot(p) == before, str(codes))

        # W: 쓰기
        cfg_before = {k: v for k, v in snapshot(p).items() if k.startswith("config")}
        rw = run(p, *ARGS, "--write")
        wo_f, cd_f, rf_f = p / "work_orders" / "WO-61.md", p / "reports" / "WO-61-config-draft.toml", p / "reports" / "WO-61-h3-auto-ref.md"
        ok_files = wo_f.is_file() and cd_f.is_file() and rf_f.is_file()
        case("W01 --write → 지시서 · 설정 초안 · 자동 승인 참조 3 파일, config/ 바이트 그대로",
             rw.returncode == 0 and ok_files and {k: v for k, v in snapshot(p).items() if k.startswith("config")} == cfg_before,
             f"{rw.returncode} {ok_files} {rw.stderr[-150:]}")
        if ok_files:
            draft = cd_f.read_text(encoding="utf-8")
            try:
                parsed = tomllib.loads(draft).get("slice", {}).get("WO-61", {})
            except Exception as err:
                parsed = {"_err": str(err)}
            case("W02 파일 내용 = 초안 절 (지시서 본문 동일, 설정 초안은 TOML 로 읽힘 · title 일치)",
                 wo_f.read_text(encoding="utf-8").strip() == wo.strip() and parsed.get("title") == "지출 기록"
                 and rf_f.read_text(encoding="utf-8").strip() == ref.strip(), str(parsed)[:150])
        else:
            case("W02 파일 내용 = 초안 절", False, "파일 없음")
        # W03: 지시서는 없고 자동 승인 참조만 사람이 고쳐 둔 상태 → 아무것도 쓰지 않음
        for f in (wo_f, cd_f):
            f.unlink(missing_ok=True)
        rf_f.parent.mkdir(parents=True, exist_ok=True)
        rf_f.write_text("사람이 고친 내용\n", encoding="utf-8")
        snap = snapshot(p)
        rw2 = run(p, *ARGS, "--write")
        case("W03 셋 중 하나라도 있으면 → 종료 2 · 아무것도 쓰지 않음 (덮어쓰기 없음)",
             rw2.returncode == 2 and snapshot(p) == snap and not wo_f.exists(), str(rw2.returncode))
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
