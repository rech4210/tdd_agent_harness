"""하네스 업그레이드 (소유권 규칙, 가이드 §11 V7).

클론해서 쓰는 방식에서는 하네스 개선을 받을 때 **하네스 소유 경로만** 상류(upstream)에서 가져와야
프로젝트 코드 · 설정과 섞이지 않는다. 이 스크립트는 그 경로만 바꾸고 커밋은 하지 않는다(사람이 확인 후 커밋, H10).

하네스 소유: harness/ · adapters/ · templates/
프로젝트 소유: 그 밖의 전부 (config/, src/, tests/, reports/ …)

- 상류에 있는 파일: 가져온다 (추가 · 변경)
- 상류에서 지운 파일: 지난번 업그레이드 기록(harness/UPSTREAM)이 있으면, 그때 상류에 있었고 지금 없는 파일만 지운다
- 상류에 한 번도 없던 파일(예: 예전 배치의 harness/*.toml 설정)은 건드리지 않는다

사용:
  python harness/upgrade.py --remote upstream --ref main --dry-run   # 바뀔 파일만 보기
  python harness/upgrade.py --remote upstream --ref main             # 하네스 소유 경로만 가져오기
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# 다른 하네스 모듈에 의존하지 않는다. 업그레이드 전의 옛 하네스에는 그 모듈이 없을 수 있다 (자기 자신도 교체 대상)
ROOT = Path(__file__).resolve().parents[1]

OWNED = ["harness", "adapters", "templates"]
RECORD = ROOT / "harness" / "UPSTREAM"  # 마지막으로 가져온 상류 커밋


def git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def tree(commit: str, paths: list[str]) -> set[str]:
    out = git("ls-tree", "-r", "--name-only", commit, "--", *paths)
    return set(out.stdout.split()) if out.returncode == 0 else set()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--remote", default="upstream")
    ap.add_argument("--ref", default="main")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if git("status", "--porcelain", "--", *OWNED).stdout.strip():
        print("하네스 소유 경로에 커밋하지 않은 변경이 있다. 먼저 정리한다 (프로젝트는 이 경로를 고치지 않는다)")
        return 1
    fetch = git("fetch", args.remote, args.ref)
    if fetch.returncode != 0:
        print(f"가져오기 실패: {fetch.stderr.strip()}")
        return 1
    target = git("rev-parse", f"{args.remote}/{args.ref}").stdout.strip()
    present = [p for p in OWNED if git("cat-file", "-e", f"{target}:{p}").returncode == 0]

    new_files = tree(target, present)
    previous = RECORD.read_text(encoding="utf-8").strip() if RECORD.exists() else ""
    old_files = tree(previous, OWNED) if previous else set()
    if previous and not old_files:
        print(f"지난 상류 커밋 {previous[:10]} 을 찾을 수 없다. 상류에서 지운 파일은 정리하지 않는다")
    removed = sorted(f for f in old_files - new_files if (ROOT / f).exists())

    changed = git("diff", "--name-status", "--diff-filter=d", "HEAD", target, "--", *present).stdout.split("\n")
    changed = [c for c in changed if c.strip()]
    for line in changed:
        print(line)
    for f in removed:
        print(f"D\t{f}  (상류에서 지움)")
    if not changed and not removed:
        print("바뀔 것 없음")
        if not previous and not args.dry_run:
            RECORD.write_text(target + "\n", encoding="utf-8", newline="\n")
            git("add", "--", RECORD.relative_to(ROOT).as_posix())
        return 0
    if args.dry_run:
        return 0

    checkout = git("checkout", target, "--", *present)
    if checkout.returncode != 0:
        print(f"적용 실패: {checkout.stderr.strip()}")
        return 1
    if removed:
        rm = git("rm", "-q", "--", *removed)
        if rm.returncode != 0:
            print(f"지우기 실패: {rm.stderr.strip()}")
            return 1
    RECORD.write_text(target + "\n", encoding="utf-8", newline="\n")
    git("add", "--", RECORD.relative_to(ROOT).as_posix())
    print(f"\n{args.remote}/{args.ref} ({target[:10]}) 의 {', '.join(present)} 를 가져왔다. 확인 후 커밋하고, doctor · 회귀 세트를 다시 돌린다:")
    print("  python harness/doctor.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
