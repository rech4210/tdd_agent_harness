"""하네스 자기 검사: upgrade 의 자기 교체 · 비교 기준 · 역할 구간 (WO-H42, 시험 문제 #18).

계약 (harness/upgrade.py, check_upgrade_docs_cases.py 의 규칙에 더해)
- 비교 기준: 하네스 소유 문서가 **대상 커밋에서 닿는 상류 이력의 그 경로의 어느 판과든** 같으면(줄 끝 정규화)
  프로젝트가 고치지 않은 것으로 보고 대상 내용으로 바꾼다. 어느 판과도 다르면 건너뜀.
  지난 기록(harness/UPSTREAM)이 없거나 그 기록이 문서 사본보다 앞서 있어도 이 비교는 한다.
- 역할 구간: 구간 안이 대상 커밋에서 닿는 이력의 templates/roles/<프리셋>/<파일> 의 어느 판과든 같으면 그 프리셋으로 본다.
  두 프리셋 이상이 맞으면 바꾸지 않고 “건너뜀” 줄에 파일 이름과 “모호” 를 적는다.
- 자기 교체: harness/ 적용으로 harness/upgrade.py 내용이 바뀌면, 새 upgrade.py 를 같은 대상 커밋으로 한 번만 이어 실행해
  문서 · 역할 구간 단계를 새 코드로 처리한다. --dry-run 은 이어 실행하지 않고 “자기 교체” 를 알리며 아무 파일도 바꾸지 않는다.
  upgrade.py 가 그대로면 “자기 교체” 를 출력하지 않는다.
- 커밋하지 않는다(H10).

사용: python harness/selftest/check_upgrade_history_cases.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PY = sys.executable
sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_upgrade_docs_cases import append, block, files, git  # noqa: E402

MARK = "U-NEW-CODE-RAN"
TM = "docs/threat-model.md"
HZ = "docs/human-zones.md"
CL_TPL = "templates/roles/claude-lead/CLAUDE.md"
CX_TPL = "templates/roles/codex-lead/CLAUDE.md"


def commit(repo: Path, msg: str) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", msg)
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def upgrade(proj: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([PY, "harness/upgrade.py", "--remote", "upstream", "--ref", "main", *extra], cwd=proj,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def skips(out: str) -> list[str]:
    return [l for l in out.splitlines() if "건너뜀" in l]


def setup(base: Path, name: str):
    from check_project_cases import make_upstream, new_project
    up = make_upstream(base / name)
    proj = base / name / "proj"
    r = new_project(up, proj, "--package", "demo_app", "--roles", "claude-lead", "--upstream", str(up), "--commit")
    if r.returncode != 0:
        raise RuntimeError(r.stdout + r.stderr)
    return up, proj


def main() -> int:
    rows: list[tuple[str, bool, str]] = []

    def case(name: str, good: bool, detail: str = "") -> None:
        rows.append((name, bool(good), detail.replace("\n", " | ")[:240]))

    for k, v in (("GIT_AUTHOR_NAME", "t"), ("GIT_AUTHOR_EMAIL", "t@example.invalid"),
                 ("GIT_COMMITTER_NAME", "t"), ("GIT_COMMITTER_EMAIL", "t@example.invalid")):
        os.environ.setdefault(k, v)
    base = Path(tempfile.mkdtemp(prefix="upgrade-hist-"))
    try:
        # A: 지난 기록이 문서 사본보다 앞섬 (시험 #18 b · c 재현)
        up, proj = setup(base, "a")
        append(up / TM, "\n<!-- C1 -->\n")
        append(up / CL_TPL, "\n- C1 템플릿 줄\n")
        c1 = commit(up, "C1")
        # 옛 upgrade 가 문서 · 구간을 안 고친 채 기록만 C1 으로 올린 상태
        (proj / "harness" / "UPSTREAM").write_text(c1 + "\n", encoding="utf-8", newline="\n")
        append(proj / HZ, "\n<!-- 프로젝트 수정 -->\n")
        commit(proj, "record ahead of copies, project edits human-zones")
        append(up / TM, "\n<!-- C2 -->\n")
        append(up / HZ, "\n<!-- C2 -->\n")
        append(up / CL_TPL, "\n- C2 템플릿 줄\n")
        commit(up, "C2")
        u = upgrade(proj)
        out = u.stdout + u.stderr
        case("U10 기록보다 오래된 판의 안 고친 문서 → 대상 내용으로, 건너뜀 아님",
             u.returncode == 0 and (proj / TM).read_bytes() == (up / TM).read_bytes()
             and not any(TM in l for l in skips(out)), out)
        hz = (proj / HZ).read_text(encoding="utf-8")
        case("U11 프로젝트가 고친 문서 → 그대로, 건너뜀 줄에 경로",
             "프로젝트 수정" in hz and "C2" not in hz and any(HZ in l for l in skips(out)), out)
        c = (proj / "CLAUDE.md").read_text(encoding="utf-8")
        case("U12 기록보다 오래된 템플릿 판의 역할 구간 → 대상 템플릿으로",
             block(c) == (up / CL_TPL).read_text(encoding="utf-8").strip()
             and not any("CLAUDE.md" in l for l in skips(out)), c[-200:] + " || " + out)
        case("U17 upgrade.py 가 그대로면 “자기 교체” 출력 없음", "자기 교체" not in out and MARK not in out, out)

        # B: 지난 기록 없음 + 두 프리셋이 맞는 역할 구간
        up, proj = setup(base, "b")
        (proj / "harness" / "UPSTREAM").unlink()
        commit(proj, "no record")
        old_cl = (up / CL_TPL).read_text(encoding="utf-8")
        before_block = block((proj / "CLAUDE.md").read_text(encoding="utf-8"))
        (up / CX_TPL).write_text(old_cl, encoding="utf-8", newline="\n")
        append(up / TM, "\n<!-- C1 -->\n")
        commit(up, "C1 codex-lead template equals claude-lead C0")
        append(up / CL_TPL, "\n- C2 claude-lead\n")
        append(up / CX_TPL, "\n- C2 codex-lead\n")
        commit(up, "C2")
        u = upgrade(proj)
        out = u.stdout + u.stderr
        case("U13 지난 기록이 없어도 이력의 판과 같은 문서 → 대상 내용으로",
             u.returncode == 0 and (proj / TM).read_bytes() == (up / TM).read_bytes(), out)
        c = (proj / "CLAUDE.md").read_text(encoding="utf-8")
        case("U14 두 프리셋 이력과 맞는 역할 구간 → 그대로, 건너뜀 줄에 CLAUDE.md · 모호",
             block(c) == before_block and any("CLAUDE.md" in l and "모호" in l for l in skips(out)), out)

        # C: 자기 교체 — 상류가 upgrade.py 를 바꿈
        up, proj = setup(base, "c")
        src = (up / "harness" / "upgrade.py").read_text(encoding="utf-8")
        anchor = "from pathlib import Path\n"
        (up / "harness" / "upgrade.py").write_text(src.replace(anchor, anchor + f'print("{MARK}")\n', 1),
                                                    encoding="utf-8", newline="\n")
        append(up / TM, "\n<!-- C1 -->\n")
        c1 = commit(up, "C1 new upgrade.py")
        count0 = git(proj, "rev-list", "--count", "HEAD").stdout.strip()
        before = files(proj)
        d = upgrade(proj, "--dry-run")
        dout = d.stdout + d.stderr
        case("U15 --dry-run: 자기 교체를 알리고 이어 실행 안 함, 파일 그대로",
             d.returncode == 0 and "자기 교체" in dout and MARK not in dout and files(proj) == before, dout)
        u = upgrade(proj)
        out = u.stdout + u.stderr
        rec = (proj / "harness" / "UPSTREAM").read_text(encoding="utf-8").strip()
        case("U16 적용: 새 upgrade.py 로 한 번 이어 실행해 문서 단계 처리, 기록 = 대상, 커밋 안 함",
             u.returncode == 0 and out.count(MARK) == 1
             and (proj / TM).read_bytes() == (up / TM).read_bytes()
             and (proj / "harness" / "upgrade.py").read_bytes() == (up / "harness" / "upgrade.py").read_bytes()
             and rec == c1 and git(proj, "rev-list", "--count", "HEAD").stdout.strip() == count0, out)
        # 리드 판정에서 추가: 내부 이어 실행 인자의 대상 검증
        rec_before = (proj / "harness" / "UPSTREAM").read_bytes()
        bad = subprocess.run([PY, "harness/upgrade.py", "--continue-from", "0" * 40], cwd=proj,
                             capture_output=True, text=True, encoding="utf-8", errors="replace")
        case("U18 --continue-from 대상이 커밋이 아니면 종료 1, UPSTREAM 그대로",
             bad.returncode == 1 and (proj / "harness" / "UPSTREAM").read_bytes() == rec_before,
             f"rc={bad.returncode} {bad.stdout[-150:]}")
    except RuntimeError as e:
        case("U00 준비: new_project", False, str(e))
    finally:
        shutil.rmtree(base, ignore_errors=True)

    all_good = bool(rows)
    for name, good, detail in rows:
        all_good &= good
        print(f"{'OK ' if good else 'BAD'} {name}  — {detail}")
    print("결과: 모든 사례가 기대와 일치" if all_good else "결과: 기대와 다른 사례가 있다")
    return 0 if all_good else 1


if __name__ == "__main__":
    sys.exit(main())
