"""처음 한 번 (S7): 클론한 하네스 저장소를 프로젝트로 만든다. 판정은 하지 않는다.

1. config/*.toml 의 <package> 를 프로젝트 패키지 이름으로 바꾼다 (설정은 프로젝트 소유)
2. 어댑터가 scaffold(root, package) 를 제공하면 언어별 프로젝트 골격을 만든다 (있는 파일은 건드리지 않는다)
3. 다음 단계를 안내한다 (doctor → 결정 기록 → 첫 슬라이스)

사용: python harness/init.py --package <이름>
"""

from __future__ import annotations

import argparse
import subprocess
import sys

from adapter_loader import load_adapter
from constitution import render as render_constitution
from paths import CONFIG, ROOT

PLACEHOLDER = "<package>"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--package", required=True, help="프로젝트의 최상위 패키지(모듈) 이름")
    args = ap.parse_args()

    changed = []
    for path in sorted(CONFIG.glob("*.toml")):
        text = path.read_text(encoding="utf-8")
        if PLACEHOLDER in text:
            path.write_text(text.replace(PLACEHOLDER, args.package), encoding="utf-8", newline="\n")
            changed.append(path.relative_to(ROOT).as_posix())
    print(f"설정 {len(changed)}개에 패키지 이름 반영: {', '.join(changed) or '(이미 반영됨)'}")

    adapter = load_adapter()
    if hasattr(adapter, "scaffold"):
        made = adapter.scaffold(ROOT, args.package)
        print(f"골격 {len(made)}개 생성: {', '.join(made) or '(이미 있음)'}")
    (ROOT / "reports").mkdir(exist_ok=True)

    # 업그레이드 기준: 클론한 커밋 = 지금 하네스 소유 경로의 상류 판. upgrade.py 가 상류에서 지운 파일을 가려낼 때 쓴다
    record = ROOT / "harness" / "UPSTREAM"
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    if not record.exists() and head.returncode == 0:
        record.write_text(head.stdout.strip() + "\n", encoding="utf-8", newline="\n")
        print(f"업그레이드 기준 기록: harness/UPSTREAM = {head.stdout.strip()[:10]}")

    # 헌법 = 설정의 사본 (게이트 GC). 설정을 고칠 때마다 사람이 --write 로 다시 만든다
    out = ROOT / ".specify" / "memory" / "constitution.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_constitution(CONFIG), encoding="utf-8", newline="\n")
    print(f"헌법 생성: {out.relative_to(ROOT).as_posix()}")

    print(
        "\n다음 단계\n"
        "  1. python harness/doctor.py                 도구 설치 확인\n"
        "  2. python harness/roles.py --preset <이름> [--slice <ID>]   역할 프리셋 적용 · 에이전트 등록 · 첫 슬라이스 배정 (사람)\n"
        "  3. specify · clarify → 결정 기록 → 테스트 (docs/sdd-integration.md, docs/slice-lifecycle.md)\n"
        "  4. 테스트 승인(H3) 뒤 python harness/check_protected_paths.py --write-lock   (테스트 + 명세 잠금)\n"
        "  5. python harness/run_gates.py --role implementer --base <기준 커밋> --wo <지시서>\n"
        "  설정(config/*.toml)을 고치면: python harness/constitution.py --write"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
