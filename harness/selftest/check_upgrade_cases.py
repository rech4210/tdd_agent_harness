"""하네스 자기 검사: 업그레이드(harness/upgrade.py)의 소유권 규칙 사례.

상류 = 이 저장소 작업 트리의 사본, 프로젝트 = 그 클론. 상류가 바뀐 뒤 프로젝트에서 upgrade 를 돌려
하네스 소유 경로만 바뀌고, 상류에서 지운 파일만 지워지고, 프로젝트 파일은 그대로인지 본다.

사용: python harness/selftest/check_upgrade_cases.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable
sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness_fixture import copy_harness_tree


def sh(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(args), cwd=cwd, capture_output=True, text=True, check=check)


def make_upstream(base: Path) -> Path:
    up = base / "up"
    copy_harness_tree(up)
    for cmd in (["git", "init", "-q", "-b", "main"], ["git", "config", "user.email", "t@example.invalid"],
                ["git", "config", "user.name", "t"], ["git", "add", "-A"], ["git", "commit", "-qm", "upstream"]):
        sh(up, *cmd)
    return up


def make_project(base: Path, up: Path, name: str) -> Path:
    proj = base / name
    sh(base, "git", "clone", "-q", str(up), str(proj))
    sh(proj, "git", "config", "user.email", "t@example.invalid")
    sh(proj, "git", "config", "user.name", "t")
    sh(proj, PY, "harness/init.py", "--package", "demo")
    (proj / "harness" / "legacy_project_setting.toml").write_text("x = 1\n", encoding="utf-8")  # 상류에 없던 파일
    sh(proj, "git", "add", "-A")
    sh(proj, "git", "commit", "-qm", "project init")
    return proj


def upgrade(proj: Path) -> subprocess.CompletedProcess[str]:
    return sh(proj, PY, "harness/upgrade.py", "--remote", "origin", "--ref", "main", check=False)


def main() -> int:
    results: list[tuple[str, bool, str]] = []
    base = Path(tempfile.mkdtemp(prefix="upgrade-case-"))
    try:
        up = make_upstream(base)

        # U01: 기록(UPSTREAM)이 없는 프로젝트에서 바뀔 것이 없어도 기준 기록을 남긴다
        p1 = make_project(base, up, "p1")
        (p1 / "harness" / "UPSTREAM").unlink()
        sh(p1, "git", "add", "-A")
        sh(p1, "git", "commit", "-qm", "기록 없는 예전 프로젝트 흉내")
        r = upgrade(p1)
        rec = p1 / "harness" / "UPSTREAM"
        head = sh(up, "git", "rev-parse", "HEAD").stdout.strip()
        results.append(("U01 바뀔 것이 없어도 기록이 없으면 기준 기록을 남긴다",
                        rec.exists() and rec.read_text(encoding="utf-8").strip() == head, r.stdout.strip()[-80:]))

        # 상류 변경: 템플릿 하나 지우고 하나 바꾼다
        p2 = make_project(base, up, "p2")
        sh(up, "git", "rm", "-q", "templates/experiment-amendment.md")
        with (up / "templates" / "checklist.md").open("a", encoding="utf-8") as f:
            f.write("\n# changed\n")
        sh(up, "git", "commit", "-qam", "upstream change")
        r = upgrade(p2)
        results.append(("U02 상류에서 지운 파일이 지워진다", not (p2 / "templates/experiment-amendment.md").exists(), ""))
        results.append(("U03 상류 변경이 반영된다",
                        (p2 / "templates/checklist.md").read_text(encoding="utf-8").rstrip().endswith("# changed"), ""))
        results.append(("U04 상류에 없던 하네스 경로 파일은 그대로", (p2 / "harness/legacy_project_setting.toml").exists(), ""))
        results.append(("U05 config/ 는 그대로", (p2 / "config/gates.toml").exists()
                        and "demo" in (p2 / "config/gates.toml").read_text(encoding="utf-8"), ""))
        results.append(("U06 기록이 새 상류 커밋으로 바뀐다",
                        (p2 / "harness/UPSTREAM").read_text(encoding="utf-8").strip()
                        == sh(up, "git", "rev-parse", "HEAD").stdout.strip(), ""))

        # U07: 하네스 소유 경로에 커밋하지 않은 변경이 있으면 거부
        p3 = make_project(base, up, "p3")
        with (p3 / "harness" / "kpi.py").open("a", encoding="utf-8") as f:
            f.write("\n# 프로젝트가 몰래 고침\n")
        r = upgrade(p3)
        results.append(("U07 하네스 소유 경로가 더러우면 거부", r.returncode != 0, r.stdout.strip()[-60:]))
    except subprocess.CalledProcessError as e:
        results.append(("실행 실패", False, f"{e.cmd} {e.stderr[-200:]}"))
    finally:
        shutil.rmtree(base, ignore_errors=True)

    all_good = True
    for name, good, detail in results:
        all_good &= good
        print(f"{'OK ' if good else 'BAD'} {name}{('  — ' + detail) if detail else ''}")
    print("결과: 모든 사례가 기대와 일치" if all_good else "결과: 기대와 다른 사례가 있다")
    return 0 if all_good else 1


if __name__ == "__main__":
    sys.exit(main())
