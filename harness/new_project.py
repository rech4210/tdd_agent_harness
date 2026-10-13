"""새 프로젝트 만들기 (HD-17).

하네스 저장소 HEAD 커밋의 내용에서 하네스 소유 경로 · 설정 틀 · 문서를 복사하고,
새 git 저장소를 초기화하며, 프로젝트 틀 문서를 렌더링하고 골격을 만든다.

사용:
  python harness/new_project.py <대상 폴더> --package <이름> [--roles <프리셋>] [--upstream <주소>]
"""

from __future__ import annotations

import argparse
import datetime
import io
import re
import subprocess
import sys
import tarfile
from pathlib import Path

HARNESS_ROOT = Path(__file__).resolve().parents[1]
OWNED_PATHS = ["harness", "adapters", "templates", "config", "docs"]
ROOT_FILES = [".gitignore", ".gitattributes"]


def validate(target_path: Path, package_name: str, roles_preset: str | None) -> list[str]:
    errors: list[str] = []

    # 검사 ①: 하네스 저장소의 harness · adapters · templates · config · docs 에 커밋 안 된 변경 · 추적 안 된 파일
    status_proc = subprocess.run(
        ["git", "status", "--porcelain", "--", *OWNED_PATHS],
        cwd=HARNESS_ROOT, capture_output=True, text=True
    )
    if status_proc.returncode != 0:
        errors.append(f"git status 실패: {status_proc.stderr.strip()}")
    elif status_proc.stdout.strip():
        errors.append(f"하네스 저장소 경로({', '.join(OWNED_PATHS)})에 커밋 안 된 변경이나 추적 안 된 파일이 있습니다")

    # 검사 ②: 대상이 있고 비어 있지 않음 (빈 폴더는 됨)
    if target_path.exists():
        if not target_path.is_dir() or any(target_path.iterdir()):
            errors.append(f"대상 폴더 '{target_path}' 가 이미 존재하고 비어 있지 않습니다")

    # 검사 ③: 대상이 하네스 저장소 안
    try:
        target_path.relative_to(HARNESS_ROOT)
        errors.append(f"대상 폴더 '{target_path}' 가 하네스 저장소 안에 있습니다")
    except ValueError:
        pass

    # 검사 ④: 패키지 이름이 ^[a-z_][a-z0-9_]*$ 가 아님 (B2: re.fullmatch)
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", package_name):
        errors.append(f"패키지 이름 '{package_name}' 이 식별자 형식(^[a-z_][a-z0-9_]*$)이 아닙니다")

    # 검사 ⑤: --roles 프리셋 (B3, B8: 값이 주어지면 re.fullmatch 및 HEAD 커밋 내용 검사)
    if roles_preset is not None:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", roles_preset):
            errors.append(f"역할 프리셋 이름 '{roles_preset}' 형식이 올바르지 않습니다 (^[A-Za-z0-9_-]+$)")
        else:
            cat_proc = subprocess.run(
                ["git", "cat-file", "-e", f"HEAD:templates/roles/{roles_preset}/preset.toml"],
                cwd=HARNESS_ROOT, capture_output=True, text=True
            )
            if cat_proc.returncode != 0:
                errors.append(f"역할 프리셋 '{roles_preset}' 이 templates/roles 에 없습니다")

    return errors


def copy_head_files(target_path: Path) -> bool:
    all_targets = OWNED_PATHS + ROOT_FILES
    tree_proc = subprocess.run(
        ["git", "ls-tree", "--name-only", "HEAD", "--", *all_targets],
        cwd=HARNESS_ROOT, capture_output=True, text=True
    )
    if tree_proc.returncode != 0:
        print(f"오류: git ls-tree 실패: {tree_proc.stderr.strip()}", file=sys.stderr)
        return False

    existing_names = set(tree_proc.stdout.splitlines())
    archive_paths = [p for p in all_targets if p in existing_names]
    if not archive_paths:
        print("오류: HEAD 에 복사할 하네스 파일이 없습니다", file=sys.stderr)
        return False

    proc = subprocess.run(
        ["git", "archive", "HEAD", "--", *archive_paths],
        cwd=HARNESS_ROOT, capture_output=True
    )
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace").strip()
        print(f"오류: git archive 실패: {err}", file=sys.stderr)
        return False

    try:
        with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as tar:
            if hasattr(tarfile, "data_filter"):
                tar.extractall(target_path, filter="data")
            else:
                tar.extractall(target_path)
    except Exception as e:
        print(f"오류: tar 압축 풀기 실패: {e}", file=sys.stderr)
        return False

    for rf in ROOT_FILES:
        if rf in archive_paths and not (target_path / rf).is_file():
            show_proc = subprocess.run(["git", "show", f"HEAD:{rf}"], cwd=HARNESS_ROOT, capture_output=True)
            if show_proc.returncode != 0:
                print(f"오류: git show HEAD:{rf} 실패: {show_proc.stderr.decode('utf-8', errors='replace').strip()}", file=sys.stderr)
                return False
            if not show_proc.stdout:
                print(f"오류: HEAD:{rf} 내용이 비어 있습니다", file=sys.stderr)
                return False
            (target_path / rf).write_bytes(show_proc.stdout)

    return True


def render_project_templates(target_path: Path, package_name: str, harness_ref: str) -> bool:
    tpl_dir = target_path / "templates" / "project"
    if not tpl_dir.is_dir():
        return True
    today_str = datetime.date.today().isoformat()
    try:
        for f in sorted(tpl_dir.rglob("*")):
            if f.is_file():
                rel = f.relative_to(tpl_dir)
                dest = target_path / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                text = f.read_text(encoding="utf-8")
                rendered = (
                    text.replace("{{PROJECT}}", target_path.name)
                    .replace("{{PACKAGE}}", package_name)
                    .replace("{{DATE}}", today_str)
                    .replace("{{HARNESS_REF}}", harness_ref)
                )
                dest.write_text(rendered, encoding="utf-8", newline="\n")
        return True
    except Exception as e:
        print(f"오류: 프로젝트 문서 틀 렌더링 실패: {e}", file=sys.stderr)
        return False


def setup_git_repo(target_path: Path, upstream_arg: str | None) -> bool:
    init_proc = subprocess.run(["git", "init", "-b", "main"], cwd=target_path, capture_output=True, text=True)
    if init_proc.returncode != 0:
        fallback = subprocess.run(["git", "init"], cwd=target_path, capture_output=True, text=True)
        if fallback.returncode != 0:
            print(f"오류: git init 실패: {fallback.stderr.strip()}", file=sys.stderr)
            return False
        co = subprocess.run(["git", "checkout", "-b", "main"], cwd=target_path, capture_output=True, text=True)
        if co.returncode != 0:
            print(f"오류: git checkout -b main 실패: {co.stderr.strip()}", file=sys.stderr)
            return False

    if upstream_arg:
        upstream_url = upstream_arg
    else:
        origin_proc = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=HARNESS_ROOT, capture_output=True, text=True
        )
        if origin_proc.returncode == 0 and origin_proc.stdout.strip():
            upstream_url = origin_proc.stdout.strip()
        else:
            upstream_url = str(HARNESS_ROOT.resolve())

    remote_proc = subprocess.run(
        ["git", "remote", "add", "upstream", upstream_url],
        cwd=target_path, capture_output=True, text=True
    )
    if remote_proc.returncode != 0:
        print(f"오류: git remote add upstream 실패: {remote_proc.stderr.strip()}", file=sys.stderr)
        return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="새 프로젝트 만들기")
    ap.add_argument("target", help="대상 폴더 경로")
    ap.add_argument("--package", required=True, help="프로젝트 패키지 이름 (^[a-z_][a-z0-9_]*$)")
    ap.add_argument("--roles", help="역할 프리셋 이름")
    ap.add_argument("--upstream", help="상류 저장소 주소")
    args = ap.parse_args()

    target_path = Path(args.target).resolve()

    # P1: 먼저 모두 검사
    errors = validate(target_path, args.package, args.roles)
    if errors:
        for err in errors:
            print(f"오류: {err}", file=sys.stderr)
        return 1

    # P2: 대상 폴더 생성 및 복사
    target_created = False
    try:
        target_path.mkdir(parents=True, exist_ok=True)
        target_created = True

        # HEAD 커밋 해시 구하기 (B9: rev-parse 실패 검사)
        head_proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=HARNESS_ROOT, capture_output=True, text=True
        )
        if head_proc.returncode != 0:
            print(f"오류: git rev-parse HEAD 실패: {head_proc.stderr.strip()}", file=sys.stderr)
            print("대상 폴더가 일부만 만들어졌다. 지우고 다시 실행", file=sys.stderr)
            return 1
        head_full = head_proc.stdout.strip()
        harness_ref = head_full[:10]

        # P2: HEAD 커밋 내용 복사 (B8, B9)
        if not copy_head_files(target_path):
            print("대상 폴더가 일부만 만들어졌다. 지우고 다시 실행", file=sys.stderr)
            return 1

        # P3: 프로젝트 문서 틀 렌더링 (B8: 복사한 대상의 templates/project/ 읽기)
        if not render_project_templates(target_path, args.package, harness_ref):
            print("대상 폴더가 일부만 만들어졌다. 지우고 다시 실행", file=sys.stderr)
            return 1

        # P4: git init -b main 및 upstream 원격 설정
        if not setup_git_repo(target_path, args.upstream):
            print("대상 폴더가 일부만 만들어졌다. 지우고 다시 실행", file=sys.stderr)
            return 1

        # P5: harness/UPSTREAM 기록
        try:
            (target_path / "harness" / "UPSTREAM").write_text(head_full + "\n", encoding="utf-8", newline="\n")
        except Exception as e:
            print(f"오류: harness/UPSTREAM 쓰기 실패: {e}", file=sys.stderr)
            print("대상 폴더가 일부만 만들어졌다. 지우고 다시 실행", file=sys.stderr)
            return 1

        # P6: harness/init.py --package <이름> 실행
        init_step = subprocess.run(
            [sys.executable, "harness/init.py", "--package", args.package],
            cwd=target_path
        )
        if init_step.returncode != 0:
            print("오류: harness/init.py 실행 실패", file=sys.stderr)
            print("대상 폴더가 일부만 만들어졌다. 지우고 다시 실행", file=sys.stderr)
            return 1

        # P7: --roles <프리셋> 이 있으면 harness/roles.py --preset <프리셋> 실행
        if args.roles:
            roles_step = subprocess.run(
                [sys.executable, "harness/roles.py", "--preset", args.roles],
                cwd=target_path
            )
            if roles_step.returncode != 0:
                print(f"오류: harness/roles.py --preset {args.roles} 실행 실패", file=sys.stderr)
                print("대상 폴더가 일부만 만들어졌다. 지우고 다시 실행", file=sys.stderr)
                return 1

    except Exception as e:
        print(f"오류: {e}", file=sys.stderr)
        if target_created:
            print("대상 폴더가 일부만 만들어졌다. 지우고 다시 실행", file=sys.stderr)
        return 1

    # P8: 다음 단계 출력
    print(
        "\n다음 단계:\n"
        "  1. 생성된 파일을 확인하고 첫 커밋: git add -A && git commit -m \"init\"\n"
        "  2. 프로젝트 원격(origin) 추가 및 푸시: git remote add origin <주소> && git push -u origin main\n"
        "  3. Spec Kit 초기화: specify init --here && specify preset add --dev templates/sdd/preset --priority 1\n"
        "  4. 역할 프리셋 확인: python harness/roles.py --list\n"
        "     (적용: python harness/roles.py --preset <이름> [--slice <ID>])"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
