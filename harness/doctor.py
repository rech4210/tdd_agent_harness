"""환경 점검 (S7): 클론 직후 하네스가 돌 수 있는지 확인한다. 판정은 하지 않는다.

Core 점검: git 저장소인가 · 설정 파일이 있는가 · 어댑터를 불러올 수 있는가 · 역할 배정 검사가 통과하는가
어댑터 점검: 어댑터가 doctor() 를 제공하면 그 결과를 함께 보여 준다 (도구 설치 여부 등)

사용: python harness/doctor.py
종료 코드: 0 준비됨, 1 문제 있음
"""

from __future__ import annotations

import subprocess
import sys
import tomllib

from adapter_loader import load_adapter
from check_assignments import check as check_assignments
from paths import ASSIGNMENTS, CONFIG, GATES, ROLES, ROOT, SLICES


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"{'OK ' if ok else 'BAD'} {name}{(' — ' + detail) if detail else ''}")
    return ok


def main() -> int:
    ok = True
    inside = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=ROOT, capture_output=True, text=True)
    ok &= check("git 저장소", inside.stdout.strip() == "true", "G0 은 git diff 로 판정한다")
    ok &= check("설정 위치", CONFIG.is_dir(), str(CONFIG.relative_to(ROOT)))
    for path in (GATES, ROLES, SLICES, ASSIGNMENTS):
        exists = path.is_file()
        ok &= check(f"설정 {path.name}", exists)
        if exists:
            try:
                tomllib.loads(path.read_text(encoding="utf-8"))
            except tomllib.TOMLDecodeError as e:
                ok &= check(f"  {path.name} 문법", False, str(e))
    try:
        adapter = load_adapter()
        ok &= check("어댑터", True, adapter.__name__)
        if hasattr(adapter, "doctor"):
            for name, good, detail in adapter.doctor():
                ok &= check(f"  {name}", good, detail)
    except Exception as e:  # 점검 도구이므로 원인을 보여 주고 실패로 끝낸다
        ok &= check("어댑터", False, f"{type(e).__name__}: {e}")
    violations = check_assignments(ASSIGNMENTS)
    if not tomllib.loads(ASSIGNMENTS.read_text(encoding="utf-8")).get("slice"):
        # 클론 직후에는 배정이 없는 것이 정상이다. 게이트 GA 는 배정 없이 통과하지 않는다 (공허한 참 방지)
        print("WARN 역할 배정 (GA) — 아직 슬라이스 배정이 없다. 첫 슬라이스 전에 config/assignments.toml 을 채운다")
    else:
        ok &= check("역할 배정 제약 (GA)", not violations, "; ".join(violations[:3]))
    print("\n준비됨" if ok else "\n문제 있음: 위의 BAD 항목을 고친 뒤 다시 실행한다")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
