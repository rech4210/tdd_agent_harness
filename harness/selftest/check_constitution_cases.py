"""하네스 자기 검사: 헌법 생성기(harness/constitution.py)의 판정 사례 (SDD 통합 E5).

헌법(Spec Kit 의 .specify/memory/constitution.md)은 config/*.toml 에서 생성한다. 유일한 권위는 toml 이고,
헌법은 에이전트가 읽는 사본이다. 사본이 어긋나면 에이전트는 자기를 판정하는 규칙을 모른 채 일한다.

사용: python harness/selftest/check_constitution_cases.py
종료 코드: 0 모든 사례가 기대와 같음, 1 다름
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness"))

GATES = """
[harness]
adapter = "python"
locked = ["tests/**", "specs/**"]

[[gate]]
id = "GA"
name = "역할 배정 제약"
why = "같은 계열은 같은 맹점을 공유한다"
cmd = ["python", "harness/check_assignments.py"]

[[gate]]
id = "GT"
name = "요구 추적성"
why = "요구마다 지키는 테스트가 있어야 한다"
kind = "traceability"
"""

ROLES = """
[roles.test_author]
allow = ["tests/**", "reports/**"]
deny  = ["src/**", "config/**"]

[roles.implementer]
allow = ["src/**", "reports/**"]
deny  = ["tests/**", "specs/**", "config/**"]

[separation]
pairs = [["test_author", "implementer"], ["implementer", "reviewer"]]
"""


def make_config() -> Path:
    d = Path(tempfile.mkdtemp(prefix="const-case-"))
    (d / "gates.toml").write_text(GATES, encoding="utf-8")
    (d / "roles.toml").write_text(ROLES, encoding="utf-8")
    return d


def main() -> int:
    import constitution  # noqa: PLC0415  (사례가 없는 모듈을 먼저 알리도록 여기서 불러온다)

    results: list[tuple[str, bool, str]] = []
    cfg = make_config()
    out_dir = Path(tempfile.mkdtemp(prefix="const-out-"))
    try:
        text = constitution.render(cfg)
        lines = text.splitlines()

        def has(s: str) -> bool:
            return s in text

        results.append(("C01 생성 파일 표시가 맨 앞에 있다", lines[0].startswith("<!-- 생성 파일"), lines[0][:60]))
        results.append(("C02 Spec Kit 헌법 절 이름 (Core Principles · Governance)",
                        has("## Core Principles") and has("## Governance"), ""))
        results.append(("C03 권위 순서", has("잠긴 테스트 > 결정(D-##) > 불변식(INV-#) > 명세 산문"), ""))
        gate_ok = all(has(s) for s in ("GA", "역할 배정 제약", "같은 계열은 같은 맹점을 공유한다",
                                       "GT", "요구 추적성", "요구마다 지키는 테스트가 있어야 한다"))
        results.append(("C04 게이트마다 id · 이름 · 이유", gate_ok, ""))
        role_ok = all(has(s) for s in ("test_author", "implementer", "tests/**", "src/**", "specs/**", "config/**"))
        results.append(("C05 역할마다 이름 · 허용 · 금지 경로", role_ok, ""))
        pair_ok = any("test_author" in ln and "implementer" in ln for ln in lines) and \
            any("implementer" in ln and "reviewer" in ln for ln in lines)
        results.append(("C06 분리 쌍마다 한 줄에 두 역할", pair_ok, ""))
        results.append(("C07 잠금 대상", any("tests/**" in ln and "specs/**" in ln for ln in lines), ""))
        results.append(("C08 결정적 (두 번 생성해도 같다)", constitution.render(cfg) == text, ""))

        target = out_dir / "constitution.md"
        ok, msg = constitution.check(target, cfg)
        results.append(("C09 헌법 파일이 없으면 check 실패", not ok, msg))
        target.write_text(text, encoding="utf-8", newline="\n")
        ok, msg = constitution.check(target, cfg)
        results.append(("C10 생성 결과와 같으면 check 통과", ok, msg))
        (cfg / "gates.toml").write_text(GATES.replace("요구 추적성", "요구 추적성 (바뀜)"), encoding="utf-8")
        ok, msg = constitution.check(target, cfg)
        results.append(("C11 toml 이 바뀌었는데 헌법이 그대로면 check 실패", not ok, msg))
        target.write_text(constitution.render(cfg).replace("## Governance", "## Governance\n\n사람이 덧붙인 문단"),
                          encoding="utf-8", newline="\n")
        ok, msg = constitution.check(target, cfg)
        results.append(("C12 헌법을 손으로 고치면 check 실패 (사본은 고치지 않는다)", not ok, msg))
        # C14 · C15: 필수 필드가 빠진 설정으로는 생성하지 않는다 (설정과 헌법이 함께 비어 일치하는 것 방지, 레드팀 C-01)
        (cfg / "gates.toml").write_text(GATES.replace('why = "요구마다 지키는 테스트가 있어야 한다"\n', ""), encoding="utf-8")
        try:
            constitution.render(cfg)
            results.append(("C14 게이트에 why 가 없으면 생성하지 않는다", False, "예외 없음"))
        except ValueError as e:
            results.append(("C14 게이트에 why 가 없으면 생성하지 않는다", True, str(e)[:60]))
        (cfg / "gates.toml").write_text(GATES, encoding="utf-8")
        (cfg / "roles.toml").write_text(ROLES.replace('allow = ["src/**", "reports/**"]\n', ""), encoding="utf-8")
        try:
            constitution.render(cfg)
            results.append(("C15 역할에 allow 가 없으면 생성하지 않는다", False, "예외 없음"))
        except ValueError as e:
            results.append(("C15 역할에 allow 가 없으면 생성하지 않는다", True, str(e)[:60]))
        (cfg / "roles.toml").unlink()
        try:
            constitution.render(cfg)
            results.append(("C13 설정 파일이 없으면 생성하지 않는다 (공허한 헌법 방지)", False, "예외 없음"))
        except (FileNotFoundError, ValueError) as e:
            results.append(("C13 설정 파일이 없으면 생성하지 않는다 (공허한 헌법 방지)", True, type(e).__name__))
    finally:
        shutil.rmtree(cfg, ignore_errors=True)
        shutil.rmtree(out_dir, ignore_errors=True)

    all_good = True
    for name, good, detail in results:
        all_good &= good
        print(f"{'OK ' if good else 'BAD'} {name}{('  — ' + detail) if detail else ''}")
    print("결과: 모든 사례가 기대와 일치" if all_good else "결과: 기대와 다른 사례가 있다")
    return 0 if all_good else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ModuleNotFoundError as e:
        print(f"BAD 헌법 생성기가 없다: {e}")
        sys.exit(1)
