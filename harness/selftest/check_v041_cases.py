"""하네스 자기 검사: core-v0.4.1 (HD-26 제안, WO-H22 준비).

M01 ~ M08: GT 경계표의 복수 결정 번호. U08 ~ U12: 업그레이드 태그와 실패 시 무변경.
고치기 전 Red 5개 / 전체 16개 (2026-10-14, Windows · Python 3.11.9,
claude/jolly-hamilton-klev4y@35828dd): M01 · M02a · M02b · U08 · U09.
사례 ID로는 4개 Red이며, 나머지 11개는 OK (회귀 · 거부 규칙 유지).
NEXT §2의 예상과 달리 U10 · U11은 이미 OK: 없는 ref는 fetch 실패로 종료 1,
삭제 출력 0 · 작업 트리/인덱스/UPSTREAM 무변경을 직접 확인했다.
기대값은 문서대로 유지한다. 구현 · 최종 판정은 아직 수행하지 않았다.
사용: python harness/selftest/check_v041_cases.py
"""

from __future__ import annotations

import hashlib
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
from check_upgrade_cases import PY, make_project, make_upstream, sh  # noqa: E402
from check_v04_cases import CFG, DEFAULTS, SPEC_HEAD, TEST, boundary_bad  # noqa: E402
from traceability import check_traceability  # noqa: E402


def gt(cell: str) -> tuple[bool, list[str], list[str]]:
    root = Path(tempfile.mkdtemp(prefix="v041-gt-"))
    try:
        files = {
            "specs/001-input/spec.md": SPEC_HEAD + f"| n | 0 ~ | 0 포함 | {cell} |\n",
            "reports/decisions.md": "# 결정 기록\n\n## D-01 n\n\n정수.\n\n## D-02 n\n\n정수.\n",
            "docs/DEFAULTS.md": DEFAULTS,
            "tests/unit/test_input.py": TEST,
        }
        for rel, content in files.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
        return check_traceability(CFG, root)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def m_cases(results: list[tuple[str, bool, str]]) -> None:
    def case(cid: str, cell: str, expect_ok: bool, need: str = "") -> None:
        ok, bad, notes = gt(cell)
        bb = boundary_bad(bad)
        if expect_ok:
            good = ok and not bb and "계약 안 1" in " ".join(notes)
        else:
            good = not ok and bool(bb) and need in " ".join(bb)
        results.append((cid, good, f"{cell!r}: " + ("; ".join(bad) or "; ".join(notes))))

    case("M01 복수 결정 (정의됨), 한 행은 계약 안 1", "D-01, D-02", True)
    case("M02a 공백 없음", "D-01,D-02", True)
    case("M02b 항목 앞뒤 공백", "D-01 , D-02", True)
    case("M03 미정의 D-99, 메시지에 D-99", "D-01, D-99", False, "D-99")
    case("M04 정의된 DEF와 혼합", "D-01, DEF-01", False, "승격 근거 올바르지 않음")
    case("M05a 끝 빈 항목", "D-01,", False, "승격 근거 올바르지 않음")
    case("M05b 첫 빈 항목", ", D-01", False, "승격 근거 올바르지 않음")
    case("M05c 중간 빈 항목", "D-01,, D-02", False, "승격 근거 올바르지 않음")
    case("M06 쉼표 없음", "D-01 D-02", False, "승격 근거 올바르지 않음")
    case("M07 단일 결정 회귀", "D-01", True)
    case("M08 결정 대기 혼합", "[결정 대기], D-01", False, "승격 근거 올바르지 않음")


def upgrade(proj: Path, ref: str, *extra: str):
    return sh(proj, PY, "harness/upgrade.py", "--remote", "origin", "--ref", ref, *extra, check=False)


def snapshot(proj: Path) -> tuple[str, str, dict[str, str], bytes]:
    status = sh(proj, "git", "status", "--porcelain").stdout
    index = sh(proj, "git", "ls-files", "--stage").stdout
    paths = sh(proj, "git", "ls-files", "-z").stdout.rstrip("\0").split("\0")
    files = {rel: hashlib.sha256((proj / rel).read_bytes()).hexdigest()
             for rel in paths if (proj / rel).is_file()}
    return status, index, files, (proj / "harness/UPSTREAM").read_bytes()


def u_cases(results: list[tuple[str, bool, str]]) -> None:
    base = Path(tempfile.mkdtemp(prefix="v041-upgrade-"))
    try:
        up = make_upstream(base)
        projects = {key: make_project(base, up, key) for key in ("ann", "light", "dry", "missing", "branch")}
        marker = "harness/v041-n.txt"
        (up / marker).write_text("n\n", encoding="utf-8", newline="\n")
        sh(up, "git", "add", "--", marker)
        sh(up, "git", "commit", "-qm", "n")
        target = sh(up, "git", "rev-parse", "HEAD").stdout.strip()
        sh(up, "git", "tag", "-a", "t-ann", "-m", "n")
        sh(up, "git", "tag", "t-light")
        tag_object = sh(up, "git", "rev-parse", "t-ann").stdout.strip()

        for cid, key, ref in (("U08", "ann", "t-ann"), ("U09", "light", "t-light")):
            proj = projects[key]
            r = upgrade(proj, ref)
            record = (proj / "harness/UPSTREAM").read_text(encoding="utf-8").strip()
            new_file = proj / marker
            good = r.returncode == 0 and new_file.is_file() and new_file.read_bytes() == b"n\n" and record == target
            if cid == "U08":
                good = good and target != tag_object and record != tag_object
            results.append((f"{cid} {ref}는 새 파일과 커밋 해시 반영", good,
                            f"exit={r.returncode}, new={new_file.exists()}, UPSTREAM={record}, target={target}"))

        proj = projects["dry"]
        before = snapshot(proj)
        r = upgrade(proj, "no-such-ref", "--dry-run")
        output = r.stdout + r.stderr
        results.append(("U10 없는 ref dry-run은 실패, 삭제 출력 없음, 무변경",
                        r.returncode != 0 and not any(line.startswith("D\t") for line in output.splitlines())
                        and before[0] == "" and snapshot(proj) == before,
                        f"exit={r.returncode}, D lines={sum(line.startswith('D' + chr(9)) for line in output.splitlines())}"))

        proj = projects["missing"]
        before = snapshot(proj)
        r = upgrade(proj, "no-such-ref")
        after = snapshot(proj)
        results.append(("U11 없는 ref 실제 실행은 실패, 작업 트리·인덱스·UPSTREAM 무변경",
                        r.returncode != 0 and before[0] == "" and after == before and after[0] == "",
                        f"exit={r.returncode}, unchanged={after == before}, status={after[0]!r}"))

        proj = projects["branch"]
        config_before = (proj / "config/gates.toml").read_bytes()
        legacy_before = (proj / "harness/legacy_project_setting.toml").read_bytes()
        r = upgrade(proj, "main")
        new_file = proj / marker
        record = (proj / "harness/UPSTREAM").read_text(encoding="utf-8").strip()
        results.append(("U12 main 브랜치 회귀 (반영·기록·프로젝트 설정 보존)",
                        r.returncode == 0 and new_file.is_file() and new_file.read_bytes() == b"n\n" and record == target
                        and (proj / "config/gates.toml").read_bytes() == config_before
                        and (proj / "harness/legacy_project_setting.toml").read_bytes() == legacy_before,
                        f"exit={r.returncode}, new={new_file.exists()}, UPSTREAM={record}"))
    finally:
        shutil.rmtree(base, ignore_errors=True)


def main() -> int:
    results: list[tuple[str, bool, str]] = []
    m_cases(results)
    u_cases(results)
    for name, good, detail in results:
        print(f"{'OK ' if good else 'BAD'} {name}  — {detail}")
    bad_count = sum(not good for _, good, _ in results)
    print(f"결과: OK {len(results) - bad_count} / BAD {bad_count} / 합계 {len(results)}")
    return 1 if bad_count else 0


if __name__ == "__main__":
    sys.exit(main())
