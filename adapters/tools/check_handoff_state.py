"""상태 줄 검사 도구 (WO-H48).

지정된 파일(기본값: docs/HANDOFF.md)에서 '상태:'로 시작하는 줄들을
기준 커밋 판과 작업 트리 판에서 비교하여 같으면 0, 다르면 1을 반환한다.
양쪽 모두 파일이 없으면 0을 반환한다.
기준 rev를 찾지 못하면 2를 반환한다.
읽기 전용으로 동작한다.

사용법:
  python adapters/tools/check_handoff_state.py --base <rev> [--file docs/HANDOFF.md]
"""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


class StateArgumentParser(argparse.ArgumentParser):
    """인자 파싱 오류 시 종료 코드 2로 종료하는 인자 파서."""

    def error(self, message: str) -> None:
        print(f"인자 오류: {message}", file=sys.stderr)
        sys.exit(2)


def extract_state_lines(content: str | None) -> list[str]:
    """텍스트에서 상태 줄들을 추출하고 정규화한다.

    1. 첫 줄의 BOM 제거
    2. ``` 로 시작하는 줄로 열고 닫는 코드 블록 안의 줄은 무시
    3. 앞 공백 제거
    4. 앞의 '> ', '- ', '* ' 반복 제거
    5. 앞의 '**' 제거
    6. '상태：'(전각) 을 '상태:' 로 변경
    7. '상태:' 로 시작하면 상태 줄. 비교 값은 정규화한 줄에서 '**' 를 지운 문자열.
    """
    if content is None:
        return []

    if content.startswith("\ufeff"):
        content = content[1:]

    lines: list[str] = []
    in_code_block = False

    for raw in content.splitlines():
        stripped = raw.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue

        if in_code_block:
            continue

        line = raw.lstrip()

        # 앞의 '> ', '- ', '* ' 반복 제거
        while True:
            matched = False
            for prefix in ("> ", "- ", "* "):
                if line.startswith(prefix):
                    line = line[len(prefix):].lstrip()
                    matched = True
                    break
            if not matched:
                break

        # 앞의 '**' 제거
        if line.startswith("**"):
            line = line[2:].lstrip()

        # 상태：(전각) 을 상태: 로
        if line.startswith("상태："):
            line = "상태:" + line[len("상태："):]

        # 상태: 로 시작하면 상태 줄
        if line.startswith("상태:"):
            val = line.replace("**", "").strip()
            lines.append(val)

    return lines


def main() -> None:
    parser = StateArgumentParser(description="상태 줄 검사 도구")
    parser.add_argument("--base", required=True, help="기준 git rev (예: HEAD, HEAD~1)")
    parser.add_argument("--file", default="docs/HANDOFF.md", help="검사 대상 파일 경로 (기본값: docs/HANDOFF.md)")

    args = parser.parse_args()

    # 기준 저장소: 도구 파일이 있는 저장소 (<도구>/../..) (WO-H54)
    repo_root = ROOT

    # 1. 기준 rev 존재 여부 확인
    rev_check = subprocess.run(
        ["git", "rev-parse", "--verify", args.base],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if rev_check.returncode != 0:
        print(f"오류: 기준 rev '{args.base}'를 찾을 수 없습니다.", file=sys.stderr)
        sys.exit(2)

    # 2. B09: --file 정규화
    # 절대 경로면 저장소 루트 기준 상대 경로로 바꾸고,
    # git ls-files -- <경로> 와 대소문자를 무시하고 비교해 실제 철자를 쓴다.
    file_arg_path = Path(args.file)
    if file_arg_path.is_absolute():
        try:
            rel_path = file_arg_path.resolve().relative_to(repo_root).as_posix()
        except ValueError:
            rel_path = file_arg_path.as_posix()
    else:
        rel_path = file_arg_path.as_posix()

    actual_path = rel_path
    target_lower = rel_path.lower()
    ls_proc = subprocess.run(
        ["git", "ls-files", "--", rel_path],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    found = False
    if ls_proc.returncode == 0:
        for line in ls_proc.stdout.splitlines():
            clean_line = line.strip()
            if clean_line.lower() == target_lower:
                actual_path = clean_line
                found = True
                break

    if not found:
        ls_all = subprocess.run(
            ["git", "ls-files"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if ls_all.returncode == 0:
            for line in ls_all.stdout.splitlines():
                clean_line = line.strip()
                if clean_line.lower() == target_lower:
                    actual_path = clean_line
                    break

    # 3. 기준 판 파일 내용 가져오기 (git show <rev>:<actual_path>)
    show_res = subprocess.run(
        ["git", "show", f"{args.base}:{actual_path}"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    base_file_exists = (show_res.returncode == 0)
    base_content = show_res.stdout if base_file_exists else None

    # 4. 작업 트리 파일 내용 가져오기 (작업 트리 파일도 그 철자로 읽음)
    work_path = repo_root / actual_path
    work_file_exists = work_path.is_file()
    work_content = None
    if work_file_exists:
        try:
            work_content = work_path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            print(f"오류: 작업 트리 파일 읽기 실패: {e}", file=sys.stderr)
            sys.exit(2)

    # 양쪽 모두 파일이 없으면 0
    if not base_file_exists and not work_file_exists:
        sys.exit(0)

    # 5. 상태 줄 추출 및 비교
    base_lines = extract_state_lines(base_content)
    work_lines = extract_state_lines(work_content)

    if base_lines == work_lines:
        sys.exit(0)

    # 다르면 두 판의 줄을 출력하고 1로 종료
    print(f"기준 ({args.base}):")
    for line in base_lines:
        print(f"  {line}")
    if not base_lines:
        print("  (없음)")

    print("작업 트리:")
    for line in work_lines:
        print(f"  {line}")
    if not work_lines:
        print("  (없음)")

    sys.exit(1)


if __name__ == "__main__":
    main()
