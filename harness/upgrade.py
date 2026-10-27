"""하네스 업그레이드 (소유권 규칙, 가이드 §11 V7, WO-H40, WO-H42).

클론해서 쓰는 방식에서는 하네스 개선을 받을 때 **하네스 소유 경로만** 상류(upstream)에서 가져와야
프로젝트 코드 · 설정과 섞이지 않는다. 이 스크립트는 그 경로만 바꾸고 커밋은 하지 않는다(사람이 확인 후 커밋, H10).

하네스 소유: harness/ · adapters/ · templates/
하네스 소유 문서: 상류 docs/ (프로젝트가 고치지 않은 것만 갱신, 고친 것은 건너뜀)
역할 구간: 루트 CLAUDE.md · AGENTS.md 의 역할 구간 (고치지 않은 것만 새 템플릿으로 갱신)
프로젝트 소유: 그 밖의 전부 (config/, src/, tests/, reports/, 상류에 없는 docs/ …)

- 상류에 있는 파일: 가져온다 (추가 · 변경)
- 상류에서 지운 파일: 지난번 업그레이드 기록(harness/UPSTREAM)이 있으면, 그때 상류에 있었고 지금 없는 파일만 지운다
- 상류에 한 번도 없던 파일(예: 예전 배치의 harness/*.toml 설정)은 건드리지 않는다
- 하네스 소유 문서: 대상 커밋에서 닿는 상류 이력의 어느 판과든 같으면(줄 끝 정규화) 고치지 않은 것으로 보고 갱신, 다르면 건너뜀
- 역할 구간: 대상 커밋에서 닿는 이력의 templates/roles/<프리셋>/<파일> 판과 비교해 유일하게 맞는 프리셋으로 갱신(모호하면 건너뜀)
- 자기 교체: harness/ 적용으로 upgrade.py 내용이 바뀌면 새 코드로 한 번 이어 실행해 문서 · 역할 구간 단계를 처리 (--dry-run 은 알림만 출력)
- 이 수정(WO-H42 자기 교체)이 없는 예전 판에서 올라올 때는 첫 upgrade 를 두 번 돌려야 한다 (첫 실행은 하네스 코드만 교체됨)
- 바뀐 것이 없어도 harness/UPSTREAM 을 대상 커밋으로 기록한다

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
ROLE_FILES = ["CLAUDE.md", "AGENTS.md"]
BEGIN = "<!-- harness:roles:begin -->"
END = "<!-- harness:roles:end -->"


def git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def git_bytes(*args: str) -> bytes:
    res = subprocess.run(["git", *args], cwd=ROOT, capture_output=True)
    return res.stdout if res.returncode == 0 else b""


def tree(commit: str, paths: list[str]) -> set[str]:
    out = git("ls-tree", "-r", "--name-only", commit, "--", *paths)
    return set(out.stdout.split()) if out.returncode == 0 else set()


def norm_bytes(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n")


def check_role_state_discrepancy(root: Path):
    state_file = root / "config" / "role-state.json"
    if not state_file.is_file():
        return
    try:
        import json
        if str(root / "harness") not in sys.path:
            sys.path.insert(0, str(root / "harness"))
        import role_state
        state_data = json.loads(state_file.read_text(encoding="utf-8"))
        errs = role_state.internal_check(root, state_data)
        if errs:
            print("\n[알림] 상류 업그레이드로 인한 역할 상태 불일치 감지:")
            for e in errs:
                print(f"  - {e}")
            print("승인된 절차로 역할 상태 갱신이 필요합니다:")
            print("  python harness/roles.py --check")
            print("  python harness/roles.py --preset <프리셋> --activate --approval-ref <참조>")
    except Exception:
        pass


def check_docs_and_roles(target: str):
    # 하네스 소유 문서 (docs/)
    docs_to_add: list[tuple[str, bytes]] = []
    docs_to_update: list[tuple[str, bytes]] = []
    docs_to_skip: list[str] = []

    target_docs = sorted(tree(target, ["docs"]))
    for doc_path in target_docs:
        proj_path = ROOT / doc_path
        target_blob = git_bytes("show", f"{target}:{doc_path}")

        if not proj_path.exists():
            docs_to_add.append((doc_path, target_blob))
            continue

        if proj_path.is_file():
            curr_blob = proj_path.read_bytes()
            if norm_bytes(curr_blob) == norm_bytes(target_blob):
                continue

            commits = git("log", "--format=%H", target, "--", doc_path).stdout.split()
            matched = False
            curr_norm = norm_bytes(curr_blob)
            for c in commits:
                blob = git_bytes("show", f"{c}:{doc_path}")
                if norm_bytes(blob) == curr_norm:
                    matched = True
                    break

            if matched:
                docs_to_update.append((doc_path, target_blob))
            else:
                docs_to_skip.append(doc_path)
        else:
            docs_to_skip.append(doc_path)

    # 역할 구간 (CLAUDE.md, AGENTS.md)
    roles_to_update: list[tuple[str, str]] = []
    roles_to_skip: list[tuple[str, str]] = []

    all_presets = set()
    for p in tree(target, ["templates/roles"]):
        parts = p.split("/")
        if len(parts) >= 3 and parts[0] == "templates" and parts[1] == "roles":
            all_presets.add(parts[2])
    log_files = git("log", "--name-only", "--format=", target, "--", "templates/roles").stdout.split()
    for line in log_files:
        parts = line.strip().split("/")
        if len(parts) >= 3 and parts[0] == "templates" and parts[1] == "roles":
            all_presets.add(parts[2])
    presets = sorted(all_presets)

    for rf in ROLE_FILES:
        proj_file = ROOT / rf
        if not proj_file.is_file():
            continue

        curr_text = proj_file.read_text(encoding="utf-8", errors="replace")
        idx_begin = curr_text.find(BEGIN)
        idx_end = curr_text.find(END)

        if idx_begin == -1 or idx_end == -1 or idx_end <= idx_begin:
            roles_to_skip.append((rf, "프로젝트가 수정함"))
            continue

        curr_block = curr_text[idx_begin + len(BEGIN):idx_end].strip().replace("\r\n", "\n")

        matched_presets: list[str] = []
        for preset in presets:
            tpl_path = f"templates/roles/{preset}/{rf}"
            tpl_commits = git("log", "--format=%H", target, "--", tpl_path).stdout.split()
            for c in tpl_commits:
                raw = git_bytes("show", f"{c}:{tpl_path}").decode("utf-8", errors="replace")
                tpl_block = raw.replace("\r\n", "\n").strip()
                if tpl_block == curr_block:
                    matched_presets.append(preset)
                    break

        if len(matched_presets) > 1:
            roles_to_skip.append((rf, "모호"))
            continue
        if len(matched_presets) == 0:
            roles_to_skip.append((rf, "프로젝트가 수정함"))
            continue

        matched_preset = matched_presets[0]
        target_tpl_path = f"templates/roles/{matched_preset}/{rf}"
        target_check = git("cat-file", "-e", f"{target}:{target_tpl_path}")
        if target_check.returncode != 0:
            roles_to_skip.append((rf, "맞는 프리셋 없음"))
            continue

        new_tpl_raw = git_bytes("show", f"{target}:{target_tpl_path}").decode("utf-8", errors="replace")
        new_tpl_norm = new_tpl_raw.replace("\r\n", "\n").strip()

        if curr_block == new_tpl_norm:
            continue

        prefix = curr_text[:idx_begin]
        suffix = curr_text[idx_end + len(END):]
        new_text = f"{prefix}{BEGIN}\n{new_tpl_norm}\n{END}{suffix}"
        roles_to_update.append((rf, new_text))

    return docs_to_add, docs_to_update, docs_to_skip, roles_to_update, roles_to_skip


def print_docs_and_roles(docs_to_add, docs_to_update, docs_to_skip, roles_to_update, roles_to_skip) -> None:
    for doc_path, _ in docs_to_add:
        print(f"A\t{doc_path}")
    for doc_path, _ in docs_to_update:
        print(f"M\t{doc_path}")
    for rf, _ in roles_to_update:
        print(f"M\t{rf}")
    for doc_path in docs_to_skip:
        print(f"건너뜀\t{doc_path}  (프로젝트가 수정함)")
    for rf, reason in roles_to_skip:
        print(f"건너뜀\t{rf}  ({reason})")


def apply_docs_and_roles(docs_to_add, docs_to_update, roles_to_update) -> int:
    modified_paths: list[str] = []
    for doc_path, content_b in docs_to_add:
        p = ROOT / doc_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content_b)
        modified_paths.append(doc_path)

    for doc_path, content_b in docs_to_update:
        p = ROOT / doc_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content_b)
        modified_paths.append(doc_path)

    for rf, content_text in roles_to_update:
        p = ROOT / rf
        p.write_text(content_text, encoding="utf-8", newline="\n")
        modified_paths.append(rf)

    if modified_paths:
        add_res = git("add", "--", *modified_paths)
        if add_res.returncode != 0:
            print(f"인덱스 추가 실패: {add_res.stderr.strip()}")
            return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--remote", default="upstream")
    ap.add_argument("--ref", default="main")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--continue-from", default=None)
    args = ap.parse_args()

    if args.continue_from:
        # 자기 교체 뒤 옛 코드가 넘긴 대상만 받는다. 커밋이 아니면 아무것도 바꾸지 않는다 (리드 보정)
        if git("rev-parse", "--verify", "-q", f"{args.continue_from}^{{commit}}").returncode != 0:
            print(f"이어 실행 대상 {args.continue_from[:10]} 이 커밋이 아니다")
            return 1
        target = args.continue_from
        present = [p for p in OWNED if git("cat-file", "-e", f"{target}:{p}").returncode == 0]
        docs_to_add, docs_to_update, docs_to_skip, roles_to_update, roles_to_skip = check_docs_and_roles(target)
        print_docs_and_roles(docs_to_add, docs_to_update, docs_to_skip, roles_to_update, roles_to_skip)
        if apply_docs_and_roles(docs_to_add, docs_to_update, roles_to_update) != 0:
            return 1
        RECORD.write_text(target + "\n", encoding="utf-8", newline="\n")
        git("add", "--", RECORD.relative_to(ROOT).as_posix())
        print(f"\n{args.remote} {args.ref} ({target[:10]}) 의 {', '.join(present)} 를 가져왔다. 확인 후 커밋하고, doctor · 회귀 세트를 다시 돌린다:")
        print("  python harness/doctor.py")
        return 0

    if git("status", "--porcelain", "--", *OWNED).stdout.strip():
        print("하네스 소유 경로에 커밋하지 않은 변경이 있다. 먼저 정리한다 (프로젝트는 이 경로를 고치지 않는다)")
        return 1
    fetch = git("fetch", args.remote, args.ref)
    if fetch.returncode != 0:
        print(f"가져오기 실패: {fetch.stderr.strip()}")
        return 1
    parse = git("rev-parse", "--verify", "-q", "FETCH_HEAD^{commit}")
    target = parse.stdout.strip()
    if parse.returncode != 0 or not target:
        print("대상 커밋을 찾을 수 없다")
        return 1
    present = [p for p in OWNED if git("cat-file", "-e", f"{target}:{p}").returncode == 0]

    new_files = tree(target, present)
    previous = RECORD.read_text(encoding="utf-8").strip() if RECORD.exists() else ""
    previous_valid = False
    if previous:
        previous_valid = (git("rev-parse", "--verify", "-q", f"{previous}^{{commit}}").returncode == 0)
        if not previous_valid:
            print(f"지난 상류 커밋 {previous[:10]} 을 찾을 수 없다. 상류에서 지운 파일은 정리하지 않는다")
    old_files = tree(previous, OWNED) if previous_valid else set()
    removed = sorted(f for f in old_files - new_files if (ROOT / f).exists())

    changed = git("diff", "--name-status", "--diff-filter=d", "HEAD", target, "--", *present).stdout.split("\n")
    changed = [c for c in changed if c.strip()]

    upgrade_path = "harness/upgrade.py"
    curr_upgrade_bytes = (ROOT / upgrade_path).read_bytes() if (ROOT / upgrade_path).exists() else b""
    target_upgrade_blob = git_bytes("show", f"{target}:{upgrade_path}")
    upgrade_will_change = bool(target_upgrade_blob and norm_bytes(curr_upgrade_bytes) != norm_bytes(target_upgrade_blob))

    docs_to_add, docs_to_update, docs_to_skip, roles_to_update, roles_to_skip = check_docs_and_roles(target)

    if args.dry_run:
        for line in changed:
            print(line)
        for f in removed:
            print(f"D\t{f}  (상류에서 지움)")
        print_docs_and_roles(docs_to_add, docs_to_update, docs_to_skip, roles_to_update, roles_to_skip)
        if upgrade_will_change:
            print("자기 교체 — 적용 시 새 코드로 이어 실행")
        has_changes = bool(changed or removed or docs_to_add or docs_to_update or roles_to_update)
        if not has_changes:
            print("바뀔 것 없음")
        check_role_state_discrepancy(ROOT)
        return 0

    has_changes = bool(changed or removed or docs_to_add or docs_to_update or roles_to_update)
    if not has_changes:
        print("바뀔 것 없음")
        RECORD.write_text(target + "\n", encoding="utf-8", newline="\n")
        git("add", "--", RECORD.relative_to(ROOT).as_posix())
        check_role_state_discrepancy(ROOT)
        return 0

    # 변경 적용
    for line in changed:
        print(line)
    for f in removed:
        print(f"D\t{f}  (상류에서 지움)")

    if present:
        checkout = git("checkout", target, "--", *present)
        if checkout.returncode != 0:
            print(f"적용 실패: {checkout.stderr.strip()}")
            return 1
    if removed:
        rm = git("rm", "-q", "--", *removed)
        if rm.returncode != 0:
            print(f"지우기 실패: {rm.stderr.strip()}")
            return 1

    after_upgrade_bytes = (ROOT / upgrade_path).read_bytes() if (ROOT / upgrade_path).exists() else b""
    if after_upgrade_bytes != curr_upgrade_bytes:
        sub = subprocess.run([sys.executable, str(ROOT / upgrade_path), "--continue-from", target, "--remote", args.remote, "--ref", args.ref])
        return sub.returncode

    print_docs_and_roles(docs_to_add, docs_to_update, docs_to_skip, roles_to_update, roles_to_skip)
    if apply_docs_and_roles(docs_to_add, docs_to_update, roles_to_update) != 0:
        return 1

    RECORD.write_text(target + "\n", encoding="utf-8", newline="\n")
    git("add", "--", RECORD.relative_to(ROOT).as_posix())
    print(f"\n{args.remote} {args.ref} ({target[:10]}) 의 {', '.join(present)} 를 가져왔다. 확인 후 커밋하고, doctor · 회귀 세트를 다시 돌린다:")
    print("  python harness/doctor.py")
    check_role_state_discrepancy(ROOT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
