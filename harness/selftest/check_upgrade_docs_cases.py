"""하네스 자기 검사: upgrade 가 하네스 소유 문서 · 역할 구간도 갱신 (WO-H40).

계약 (harness/upgrade.py, 기존 harness · adapters · templates 규칙에 더해)
- 하네스 소유 문서 = 대상 상류 커밋의 docs/ 에 있는 파일. 프로젝트의 그 파일이
    · 없으면 추가한다.
    · 지난 상류 커밋(harness/UPSTREAM)의 내용과 같으면(프로젝트가 안 고침) 대상 내용으로 바꾼다.
    · 다르면(프로젝트가 고침, 또는 지난 기록이 없음) 바꾸지 않고 “건너뜀” 줄에 경로를 적는다.
  상류 docs/ 에 없는 프로젝트 문서(HANDOFF · PRODUCT 등)는 건드리지 않는다.
- 역할 구간 = 루트 CLAUDE.md · AGENTS.md 의 <!-- harness:roles:begin --> … <!-- harness:roles:end --> 사이.
  지난 상류 커밋의 templates/roles/<프리셋>/<같은 파일 이름> 내용(앞뒤 공백 제거)과 구간 안이 정확히 같은 프리셋이 있으면
  그 프리셋의 대상 커밋 템플릿으로 구간 안만 바꾼다(구간 밖은 그대로). 맞는 프리셋이 없으면(프로젝트가 고침) 바꾸지 않고
  “건너뜀” 줄에 파일 이름을 적는다.
- 바뀐 문서 · 구간은 git 인덱스에 올린다(커밋은 하지 않는다). --dry-run 은 같은 판단을 출력만 하고 아무 파일도 바꾸지 않는다.
- 하네스 소유 경로 · 문서 · 구간 모두 바뀐 것이 없어도 harness/UPSTREAM 은 대상 커밋으로 기록한다.

사용: python harness/selftest/check_upgrade_docs_cases.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable
BEGIN, END = "<!-- harness:roles:begin -->", "<!-- harness:roles:end -->"
sys.path.insert(0, str(Path(__file__).resolve().parent))


def git(repo: Path, *a: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *a], cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace")


def block(text: str) -> str:
    return text.split(BEGIN, 1)[1].split(END, 1)[0].strip() if BEGIN in text and END in text else "<없음>"


def append(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(text)


def files(root: Path) -> dict[str, bytes]:
    out = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if p.is_file() and not rel.startswith(".git/") and "__pycache__" not in rel:
            out[rel] = p.read_bytes()
    return out


def main() -> int:
    rows: list[tuple[str, bool, str]] = []

    def case(name: str, good: bool, detail: str = "") -> None:
        rows.append((name, bool(good), detail.replace("\n", " | ")[:220]))

    for k, v in (("GIT_AUTHOR_NAME", "t"), ("GIT_AUTHOR_EMAIL", "t@example.invalid"),
                 ("GIT_COMMITTER_NAME", "t"), ("GIT_COMMITTER_EMAIL", "t@example.invalid")):
        os.environ.setdefault(k, v)
    base = Path(tempfile.mkdtemp(prefix="upgrade-docs-"))
    try:
        from check_project_cases import make_upstream, new_project
        up = make_upstream(base)
        proj = base / "proj"
        r = new_project(up, proj, "--package", "demo_app", "--roles", "claude-lead", "--upstream", str(up), "--commit")
        if r.returncode != 0:
            case("U00 준비: new_project", False, r.stdout + r.stderr)
            raise SystemExit
        # 프로젝트 쪽 수정: 역할 구간 밖 메모, AGENTS.md 구간 안 수정, threat-model 수정
        append(proj / "CLAUDE.md", "\n## 프로젝트 메모\n- 구간 밖은 그대로\n")
        a = (proj / "AGENTS.md").read_text(encoding="utf-8")
        (proj / "AGENTS.md").write_text(a.replace(END, "- 프로젝트가 구간 안에 쓴 줄\n" + END), encoding="utf-8", newline="\n")
        append(proj / "docs" / "threat-model.md", "\n<!-- 프로젝트 수정 -->\n")
        git(proj, "add", "-A")
        git(proj, "commit", "-qm", "project edits")
        handoff_before = (proj / "docs" / "HANDOFF.md").read_bytes() if (proj / "docs" / "HANDOFF.md").exists() else None
        # 상류 변경
        append(up / "docs" / "human-zones.md", "\n<!-- U01 상류 변경 -->\n")
        append(up / "docs" / "threat-model.md", "\n<!-- U02 상류 변경 -->\n")
        append(up / "templates" / "roles" / "claude-lead" / "CLAUDE.md", "\n- U03 새 템플릿 줄\n")
        append(up / "templates" / "roles" / "claude-lead" / "AGENTS.md", "\n- U04 새 템플릿 줄\n")
        append(up / "docs" / "new-guide.md", "# 새 안내\n")
        git(up, "add", "-A")
        git(up, "commit", "-qm", "docs and role templates")
        target = git(up, "rev-parse", "HEAD").stdout.strip()

        before = files(proj)
        d = subprocess.run([PY, "harness/upgrade.py", "--remote", "upstream", "--ref", "main", "--dry-run"], cwd=proj,
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        case("U07 --dry-run: 문서 · 역할 구간 판단을 출력하고 파일은 그대로",
             d.returncode == 0 and files(proj) == before and "docs/human-zones.md" in d.stdout and "CLAUDE.md" in d.stdout,
             d.stdout + d.stderr)

        u = subprocess.run([PY, "harness/upgrade.py", "--remote", "upstream", "--ref", "main"], cwd=proj,
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        out = u.stdout + u.stderr
        case("U01 고치지 않은 하네스 문서 → 상류 내용으로",
             u.returncode == 0 and (proj / "docs" / "human-zones.md").read_bytes() == (up / "docs" / "human-zones.md").read_bytes(), out)
        tm = (proj / "docs" / "threat-model.md").read_text(encoding="utf-8")
        skip_lines = [l for l in out.splitlines() if "건너뜀" in l]
        case("U02 프로젝트가 고친 하네스 문서 → 그대로, “건너뜀” 줄에 경로",
             "프로젝트 수정" in tm and "U02 상류 변경" not in tm and any("docs/threat-model.md" in l for l in skip_lines), out)
        c = (proj / "CLAUDE.md").read_text(encoding="utf-8")
        new_tpl = (up / "templates" / "roles" / "claude-lead" / "CLAUDE.md").read_text(encoding="utf-8").strip()
        case("U03 고치지 않은 역할 구간 → 새 템플릿, 구간 밖 메모는 그대로",
             block(c) == new_tpl and "구간 밖은 그대로" in c and c.count(BEGIN) == 1, c[-200:])
        ag = (proj / "AGENTS.md").read_text(encoding="utf-8")
        case("U04 프로젝트가 고친 역할 구간 → 그대로, “건너뜀” 줄에 AGENTS.md",
             "프로젝트가 구간 안에 쓴 줄" in ag and "U04 새 템플릿 줄" not in ag
             and any("AGENTS.md" in l for l in skip_lines), out)
        case("U05 상류 docs/ 에 없는 프로젝트 문서는 그대로",
             handoff_before is None or (proj / "docs" / "HANDOFF.md").read_bytes() == handoff_before, "")
        case("U08 상류에 새로 생긴 문서 → 추가, 인덱스에 올라감",
             (proj / "docs" / "new-guide.md").exists()
             and "docs/new-guide.md" in git(proj, "diff", "--cached", "--name-only").stdout, out)
        staged = git(proj, "diff", "--cached", "--name-only").stdout
        case("U09 바뀐 문서 · 구간은 인덱스에 올리고 커밋하지 않음",
             "docs/human-zones.md" in staged and "CLAUDE.md" in staged
             and git(proj, "rev-list", "--count", "HEAD").stdout.strip() == "2", staged)

        git(proj, "commit", "-qm", "upgrade")
        append(up / "dev" / "note-u06.md", "x\n")
        git(up, "add", "-A")
        git(up, "commit", "-qm", "non-owned change")
        target2 = git(up, "rev-parse", "HEAD").stdout.strip()
        u2 = subprocess.run([PY, "harness/upgrade.py", "--remote", "upstream", "--ref", "main"], cwd=proj,
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
        rec = (proj / "harness" / "UPSTREAM").read_text(encoding="utf-8").strip()
        case("U06 바뀐 것이 없어도 UPSTREAM 은 대상 커밋으로",
             u2.returncode == 0 and rec == target2 and rec != target, f"rec={rec[:10]} target2={target2[:10]} {u2.stdout[-120:]}")
    except SystemExit:
        pass
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
