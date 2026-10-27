"""하네스 자기 검사: step.py 안내의 정확한 명령 (WO-H52, 독립 일감 F4).

배경: 안내 출력의 “다음” 은 짧은 문장뿐이라 리드가 명령을 다시 찾아야 한다(JUDGE-wo-h45 관찰, 지시서 §4.3 “정확한 실행 명령”).

계약 (harness/step.py, 안내 출력만 — --check 의 막힘 · 종료 코드는 그대로)
- --json 출력에 "commands": [문자열 목록] 추가(기존 키 불변). <slice> · <wo> · <base_tag> · <closed_tag> 는 실제 값으로 채운다.
  "S1-S5":  "python harness/run_gates.py --role test_author --base HEAD --only GT --wo <wo> --slice <slice>",
            "python harness/mark.py review <검토자> --wo <wo> --round <N> --defects <K> --new-defects <M>"
  "S6":     "python harness/check_protected_paths.py --write-lock",
            "git tag -a <base_tag> -m \"<잠금 + 지시서 확정>\"",
            "python harness/step.py --slice <slice> --check delegate"
  "S7":     "python harness/step.py --slice <slice> --check delegate",
            "python harness/run_gates.py --feedback --role implementer --base <base_tag> --wo <wo>",
            "python harness/run_gates.py --role implementer --base <base_tag> --slice <slice> --wo <wo>"
  "S8":     "python harness/run_gates.py --role implementer --base <base_tag> --slice <slice> --wo <wo>"
  "S10":    "python harness/step.py --slice <slice> --check close",
            "git tag -a <closed_tag> -m \"<종료>\""
  "closed": []
  `<검토자>` · `<N>` · `<K>` · `<M>` · 따옴표 안 설명은 사람이 채우는 자리 그대로 둔다.
- --json 없는 출력에는 “다음:” 줄 뒤에 명령마다 “  $ <명령>” 줄.

사용: python harness/selftest/check_step_next_cases.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_kpi_cases import PY, make_project  # noqa: E402
from check_step_cases import H3, LOCK, SLICES, T1, T4, cls, commit, git, put, review, step, verdict  # noqa: E402

RG = "python harness/run_gates.py"


def main() -> int:
    rows: list[tuple[str, bool, str]] = []

    def case(name: str, good: bool, detail: str = "") -> None:
        rows.append((name, bool(good), detail[:300]))

    base = Path(tempfile.mkdtemp(prefix="stepnext-"))
    try:
        p = make_project(base, "p")
        (p / "config" / "slices.toml").write_text(SLICES, encoding="utf-8", newline="\n")
        commit(p, "config/slices.toml", SLICES, "slices")
        put(p, [cls()])
        _, d, out = step(p)
        want = [f"{RG} --role test_author --base HEAD --only GT --wo WO-51 --slice WO-51",
                "python harness/mark.py review <검토자> --wo WO-51 --round <N> --defects <K> --new-defects <M>"]
        case("N01 S1-S5 명령 2개 (GT 자체 점검 · 검토 기록)", d.get("commands") == want, str(d.get("commands")) + out)
        case("N02 기존 키 불변 (slice · stage · next · blockers · evidence)",
             all(k in d for k in ("slice", "stage", "next", "blockers", "evidence")), str(list(d)))
        put(p, [cls(), review(T1)])
        _, d, _ = step(p)
        case("N03 S6 명령: 잠금 · 기준 태그 wo51-base · 위임 검사",
             d.get("commands") == ["python harness/check_protected_paths.py --write-lock",
                                   'git tag -a wo51-base -m "<잠금 + 지시서 확정>"',
                                   "python harness/step.py --slice WO-51 --check delegate"], str(d.get("commands")))
        sha = commit(p, "config/test_lock.json", "{}", "lock")
        git(p, "tag", "-a", "wo51-base", "-m", "b", sha)
        put(p, [cls(), review(T1), H3, LOCK])
        _, d, _ = step(p)
        case("N04 S7 명령: 위임 검사 · 피드백 · 판정",
             d.get("commands") == ["python harness/step.py --slice WO-51 --check delegate",
                                   f"{RG} --feedback --role implementer --base wo51-base --wo WO-51",
                                   f"{RG} --role implementer --base wo51-base --slice WO-51 --wo WO-51"], str(d.get("commands")))
        impl = commit(p, "src/a.py", "x = 1\n", "impl")
        put(p, [cls(), review(T1), H3, LOCK, verdict(T4, impl, passed=False)])
        _, d, _ = step(p)
        case("N05 S8 명령: 판정 다시", d.get("commands") == [f"{RG} --role implementer --base wo51-base --slice WO-51 --wo WO-51"],
             str(d.get("commands")))
        put(p, [cls(), review(T1), H3, LOCK, verdict(T4, impl)])
        _, d, _ = step(p)
        case("N06 S10 명령: 종료 검사 · 종료 태그 wo51-closed",
             d.get("commands") == ["python harness/step.py --slice WO-51 --check close", 'git tag -a wo51-closed -m "<종료>"'],
             str(d.get("commands")))
        r = subprocess.run([PY, "harness/step.py", "--slice", "WO-51"], cwd=p, capture_output=True, text=True, encoding="utf-8")
        case("N07 사람용 출력: 명령마다 “  $ ” 줄", "  $ python harness/step.py --slice WO-51 --check close" in r.stdout, r.stdout[-200:])
        rc, d, _ = step(p, "--check", "close")
        case("N08 --check 결과는 그대로 (명령 목록이 막힘에 섞이지 않음)", all(str(b)[:1] in "DC" for b in d.get("blockers", [])), str(d.get("blockers")))
        git(p, "tag", "-a", "wo51-closed", "-m", "c")
        _, d, _ = step(p)
        case("N09 closed → 명령 없음 (빈 목록)", d.get("commands") == [], str(d.get("commands")))
    finally:
        shutil.rmtree(base, ignore_errors=True)
    ok = all(g for _, g, _ in rows)
    for n, g, d in rows:
        print(f"{'OK ' if g else 'BAD'} {n}{('  — ' + d) if d and not g else ''}")
    print("결과: 모든 사례가 기대와 일치" if ok else "결과: 기대와 다른 사례가 있다")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
