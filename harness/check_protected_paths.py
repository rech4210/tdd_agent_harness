"""G0: 보호 경로 · 테스트 우회 패턴 검사.

에이전트의 자기 보고("테스트 안 건드렸음")를 믿지 않고 git diff 로 확인한다.

검사 1. 역할별 쓰기 권한 (config/roles.toml)
검사 2. 추가된 줄의 우회 패턴 (skip, pragma: no cover, 테스트 환경 감지 등)
검사 3. 테스트 잠금: 사람이 승인한 tests/ 의 해시가 그대로인지 (config/test_lock.json)

사용:
  python harness/check_protected_paths.py --base main --role implementer
  python harness/check_protected_paths.py --write-lock      # 사람이 테스트를 승인한 직후에만
종료 코드: 0 통과, 1 위반, 2 사용법 오류
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

from adapter_loader import load_adapter

ROOT = Path(__file__).resolve().parents[1]
from paths import LOCK, ROLES  # noqa: E402

# 추가된 줄에서 찾는 우회 패턴. (정규식, 이유)
# 추가된 줄에서 찾는 우회 패턴. 패턴은 사실상 전부 언어 · 도구별이므로 어댑터가 제공한다(가이드 K6).
# Core 는 "추가된 줄에 패턴을 적용하는 장치"만 갖는다. 언어와 무관한 패턴이 생기면 CORE_PATTERNS 에 둔다
CORE_PATTERNS: list[tuple[str, str]] = []
ADAPTER = load_adapter()
GAMING_PATTERNS: list[tuple[str, str]] = CORE_PATTERNS + list(ADAPTER.GAMING_PATTERNS)
# C1: 우회 패턴을 적용할 소스 확장자는 어댑터가 정한다. 비어 있으면 모든 변경 파일에 적용한다
SOURCE_SUFFIXES: tuple[str, ...] = tuple(s.lower() for s in getattr(ADAPTER, "SOURCE_SUFFIXES", ()))
# C2: 잠금에서 뺄 도구 산출 폴더 (Core 공통 + 어댑터)
LOCK_EXCLUDE_PARTS = {".git", "node_modules"} | set(getattr(ADAPTER, "LOCK_EXCLUDE_PARTS", ()))


def _has_surrogates(s: str) -> bool:
    return any("\udc80" <= ch <= "\udcff" for ch in s)


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True, encoding="utf-8", errors="surrogateescape"
    ).stdout


def changed_files(base: str) -> list[str]:
    # 작업 트리까지 포함: 커밋하지 않은 변경도 잡는다
    out = git("diff", "--name-only", "-z", "--no-renames", base)
    try:
        out_head = git("diff", "--name-only", "-z", "--no-renames", base, "HEAD")
    except subprocess.CalledProcessError:
        out_head = ""
    untracked = git("ls-files", "-z", "--others", "--exclude-standard")
    diff_files = [p for p in out.split("\0") if p]
    head_files = [p for p in out_head.split("\0") if p]
    untracked_files = [p for p in untracked.split("\0") if p]
    return sorted(set(diff_files) | set(head_files) | set(untracked_files))


def added_lines(base: str, path: str, tracked: set[str] | None = None) -> list[tuple[int, str]]:
    diff = git("diff", "--text", "--no-ext-diff", "--no-textconv", "-U0", base, "--", path)
    lines, lineno = [], 0
    for raw in diff.splitlines():
        m = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)", raw)
        if m:
            lineno = int(m.group(1))
            continue
        if raw.startswith("+") and not raw.startswith("+++"):
            lines.append((lineno, raw[1:]))
            lineno += 1
    if not lines and (ROOT / path).is_file():
        if tracked is None:
            tracked = {p for p in git("ls-files", "-z").split("\0") if p}
        if path not in tracked:
            # 새 파일(untracked)은 diff 에 안 나오므로 전체를 추가된 줄로 본다
            text = (ROOT / path).read_text(encoding="utf-8", errors="replace")
            lines = list(enumerate(text.splitlines(), start=1))
    return lines


def match_any(path: str, globs: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, g) for g in globs)


def locked_globs() -> list[str]:
    """C2: 잠금 대상. gates.toml [harness] locked (기본 [\"tests/**\"]). 소스 옆에 테스트를 두는 언어는 glob 으로 지정한다"""
    from adapter_loader import harness_settings

    return list(harness_settings().get("locked", ["tests/**"]))


def _glob_base(glob: str) -> Path:
    """glob 의 고정 부분(첫 와일드카드 앞 폴더)만 훑는다. 저장소 전체(가상환경 · 링크)를 훑지 않기 위해서다."""
    parts = []
    for part in glob.split("/"):
        if any(ch in part for ch in "*?["):
            break
        parts.append(part)
    return ROOT.joinpath(*parts) if parts else ROOT


def tests_digest() -> dict[str, str]:
    globs = locked_globs()
    found: set[Path] = set()
    for glob in globs:
        base = _glob_base(glob)
        if not base.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in LOCK_EXCLUDE_PARTS]
            for name in filenames:
                p = Path(dirpath) / name
                if match_any(p.relative_to(ROOT).as_posix(), globs):
                    found.add(p)
    files = sorted(found)
    return {
        # 줄바꿈을 LF 로 맞춘 뒤 해시한다. 체크아웃 환경(CRLF/LF)이 달라도 같은 내용이면 같은 해시
        str(p.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        for p in files
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base")
    ap.add_argument("--role")
    ap.add_argument("--write-lock", action="store_true")
    args = ap.parse_args()

    if args.write_lock:
        digest = tests_digest()
        if not digest:
            # 0개를 잠그면 뒤의 잠금 검사가 비교할 것 없이 통과한다 (공허한 참 방지)
            print(f"잠글 테스트 파일이 없다: {', '.join(locked_globs())}. 잠금을 쓰지 않았다")
            return 1
        LOCK.write_text(json.dumps(digest, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
        print(f"테스트 잠금 갱신: {LOCK.relative_to(ROOT)} ({', '.join(locked_globs())})  (사람이 승인한 직후에만 실행할 것)")
        from events import log_event
        log_event("test_lock", files=len(json.loads(LOCK.read_text(encoding="utf-8"))))
        return 0
    if not (args.base and args.role):
        ap.print_usage()
        return 2

    roles = tomllib.loads(ROLES.read_text(encoding="utf-8"))["roles"]
    if args.role not in roles:
        print(f"알 수 없는 역할: {args.role}")
        return 2
    role = roles[args.role]

    violations: list[str] = []
    review: list[str] = []

    # 검사 1: 쓰기 권한
    files = changed_files(args.base)
    for f in files:
        if _has_surrogates(f):
            violations.append(f"[경로] 해석할 수 없는 파일 이름: {repr(f)}")
            continue
        if match_any(f, role.get("deny", [])):
            violations.append(f"[권한] {args.role} 는 {f} 를 바꿀 수 없다")
        elif not match_any(f, role.get("allow", [])):
            violations.append(f"[권한] {f} 는 {args.role} 의 허용 경로 밖이다")
        elif match_any(f, role.get("review_required", [])):
            review.append(f)

    # 검사 2: 우회 패턴 (모든 역할. 테스트 작성자의 skip 도 사람이 봐야 한다)
    tracked = {p for p in git("ls-files", "-z").split("\0") if p}
    for f in files:
        if _has_surrogates(f):
            continue
        if (SOURCE_SUFFIXES and not f.lower().endswith(SOURCE_SUFFIXES)) or not (ROOT / f).exists():
            continue
        for lineno, text in added_lines(args.base, f, tracked):
            for pattern, reason in GAMING_PATTERNS:
                if re.search(pattern, text):
                    violations.append(f"[우회] {f}:{lineno} {reason}: {text.strip()}")

    # 검사 3: 테스트 잠금 (테스트 작성자 또는 lock_exempt 지정 역할 외)
    exempt = False
    if "lock_exempt" in role:
        val = role["lock_exempt"]
        if not isinstance(val, bool):
            violations.append(f"[설정] roles.toml 의 {args.role}.lock_exempt 는 true 또는 false 여야 한다")
        else:
            exempt = val

    if args.role != "test_author" and not exempt:
        if not LOCK.exists():
            violations.append(f"[잠금] 잠금 파일이 없다 ({LOCK.relative_to(ROOT).as_posix()}). 사람이 테스트 승인 후 --write-lock 해야 한다")
        else:
            locked = json.loads(LOCK.read_text(encoding="utf-8"))
            if not locked:
                violations.append("[잠금] 잠금 파일이 비어 있다. 잠긴 테스트가 0개이면 잠금 검사는 아무것도 지키지 않는다")
            now = tests_digest()
            for path in sorted(set(locked) | set(now)):
                if locked.get(path) != now.get(path):
                    violations.append(f"[잠금] 승인 후 바뀐 테스트 파일: {path}")

    report = {
        "gate": "G0",
        "role": args.role,
        "base": args.base,
        "changed_files": [repr(f) if _has_surrogates(f) else f for f in files],
        "violations": violations,
        "review_required": review,
        "passed": not violations,
    }
    text = json.dumps(report, indent=2, ensure_ascii=False)
    # C3: 결과를 파일로도 남긴다. 실행기는 출력 텍스트가 아니라 이 파일을 읽는다 (출력은 잘릴 수 있다)
    out = ROOT / "reports" / "g0_report.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(text, encoding="utf-8", newline="\n")
    print(text)
    return 0 if not violations else 1


if __name__ == "__main__":
    sys.exit(main())
