"""하네스 자기 검사: SDD + TDD 흐름 끝에서 끝까지 (S7 + SDD 통합).

이 저장소의 작업 트리를 임시 폴더에 복사(= 클론)하고, 명세 → 결정 → 테스트 → 헌법 → 잠금 → 구현 → 판정을 실제로 돌린다.
그 뒤 공격을 하나씩 넣어 의도한 게이트가 실패하는지 본다.

사용: <테스트 도구가 설치된 Python> harness/selftest/check_sdd_flow.py
      (G2 · G3 은 어댑터의 도구로 돈다. Python 어댑터면 pytest · pytest-cov · hypothesis · pytest-timeout 이 있는 인터프리터)
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable
ONLY = "GA,G0,GT,GC,G1,G2,G3"

SPEC = """# Feature Specification: 금액

## Clarifications

### Session 2026-10-10

- Q: 통화는 몇 개인가? → A: 원화 하나 → D-2

## Requirements

### Functional Requirements

- **FR-001**: 시스템은 음수 금액을 거부해야 한다
- **FR-002**: 두 금액의 합은 각 금액의 센트 합과 같아야 한다

## Assumptions

- 금액 단위는 센트 정수다 → D-1

## Invariants

- **INV-1**: 금액은 0 이상이다

## Boundary tables

| 입력 | 정의역 | 단위 | 근거 |
|:---|:---|:---|:---|
| cents | 0 이상 정수, 상한 없음 | 센트 | D-1 |
"""

DECISIONS = "# 결정 기록\n\n## D-1 금액 단위\n\n센트 정수. 상한 없음.\n\n## D-2 통화\n\n원화 하나.\n"

TEST = '''"""금액 값 객체: 거부 영역과 합.

guards:      FR-001, FR-002, INV-1, D-1
technique:   05 거부 영역 전체, 02 독립 오라클 성질
checklist:   B-2, C-A3
decisions:   D-1 (단위: 센트 정수, 상한 없음)
"""

import pytest
from hypothesis import given, strategies as st

from demo.domain.money import Money, NegativeAmount


def test_zero_is_allowed() -> None:
    """FR-001 · INV-1: 0 은 허용한다."""
    assert Money(0).cents == 0


def test_negative_is_rejected() -> None:
    """FR-001: 음수는 거부한다."""
    with pytest.raises(NegativeAmount):
        Money(-1)


@given(st.integers(min_value=0, max_value=10**9), st.integers(min_value=0, max_value=10**9))
def test_add_is_sum(a: int, b: int) -> None:
    """FR-002: 합은 센트의 합이다."""
    assert (Money(a) + Money(b)).cents == a + b
'''

IMPL = '''from __future__ import annotations

from dataclasses import dataclass


class NegativeAmount(ValueError):
    pass


@dataclass(frozen=True)
class Money:
    cents: int

    def __post_init__(self) -> None:
        if self.cents < 0:
            raise NegativeAmount(self.cents)

    def __add__(self, other: Money) -> Money:
        return Money(self.cents + other.cents)
'''

AGENTS = '\n[agents.claude]\nfamily = "anthropic"\n\n[agents.codex]\nfamily = "openai"\n\n[agents.gemini]\nfamily = "google"\n'
ASSIGN = ('\n[slice.WO-01]\nstatus = "open"\nspec_author = "gemini"\ntest_author = "claude"\nimplementer = "codex"\n'
          'reason = "자기 검사"\n')


class Flow:
    def __init__(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="sdd-flow-"))
        self.results: list[tuple[str, bool, str]] = []

    def sh(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(list(args), cwd=self.dir, capture_output=True, text=True, check=check)

    def write(self, rel: str, text: str, append: bool = False) -> None:
        p = self.dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        old = p.read_text(encoding="utf-8") if append and p.exists() else ""
        p.write_text(old + text, encoding="utf-8", newline="\n")

    def commit(self, msg: str) -> None:
        self.sh("git", "add", "-A")
        self.sh("git", "commit", "-qm", msg)

    def gates(self, role: str, base: str, only: str = ONLY) -> dict[str, str]:
        self.sh(PY, "harness/run_gates.py", "--role", role, "--base", base, "--wo", "WO-01", "--only", only, "--keep-going",
                check=False)
        report = json.loads((self.dir / "reports" / "gate_report.json").read_text(encoding="utf-8"))
        return {g["id"]: {True: "PASS", False: "FAIL"}.get(g["passed"], "SKIP") for g in report["gates"]}

    def expect(self, name: str, got: dict[str, str], fail: set[str]) -> None:
        bad_gates = {k for k, v in got.items() if v == "FAIL"}
        self.results.append((name, bad_gates == fail, f"FAIL={sorted(bad_gates)} 기대={sorted(fail)}"))

    def setup(self) -> None:
        files = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=ROOT, capture_output=True,
                               text=True, check=True).stdout.split()
        for rel in files:
            src = ROOT / rel
            if src.is_file() and "__pycache__" not in rel:
                (self.dir / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, self.dir / rel)
        self.sh("git", "init", "-q")
        self.sh("git", "config", "user.email", "selftest@example.invalid")
        self.sh("git", "config", "user.name", "selftest")
        self.commit("clone")
        self.sh(PY, "harness/init.py", "--package", "demo")
        self.write("config/agents.toml", AGENTS, append=True)
        self.write("config/assignments.toml", ASSIGN, append=True)
        self.commit("init (사람)")
        self.sh("git", "tag", "spec-base")

    def run(self) -> None:
        self.setup()
        # 1. 명세 작성자: 명세 (잠금 전)
        self.write("specs/001-money/spec.md", SPEC)
        self.commit("spec (spec_author)")
        self.expect("F01 명세 작성자의 변경은 G0 통과 (잠금 전)", self.gates("spec_author", "spec-base", "G0"), set())
        # 2. 사람: 결정 기록 · 헌법
        self.write("reports/decisions.md", DECISIONS)
        self.sh(PY, "harness/constitution.py", "--write")
        self.commit("decisions + constitution (사람)")
        self.sh("git", "tag", "tests-base")
        # 3. 테스트 작성자: 테스트 → GT
        self.write("tests/unit/test_money.py", TEST)
        self.commit("tests (test_author)")
        self.expect("F02 테스트 작성자 쪽 GA · G0 · GT · GC 통과 (H3 전 확인)",
                    self.gates("test_author", "tests-base", "GA,G0,GT,GC"), set())
        # 4. 사람: H3 승인 → 잠금 (테스트 + 명세)
        self.sh(PY, "harness/check_protected_paths.py", "--write-lock")
        lock = json.loads((self.dir / "config" / "test_lock.json").read_text(encoding="utf-8"))
        self.results.append(("F03 잠금에 명세가 들어간다", any(k.startswith("specs/") for k in lock), str(len(lock))))
        self.commit("lock (사람, H3)")
        self.sh("git", "tag", "wo01-base")
        # 5. 구현자: 구현 → 전체 판정
        self.write("src/demo/domain/money.py", IMPL)
        self.commit("impl (implementer)")
        self.expect("F04 정상 구현: 모든 게이트 통과", self.gates("implementer", "wo01-base"), set())

        # 공격
        self.write("specs/001-money/spec.md", SPEC.replace("음수 금액을 거부", "음수 금액을 허용"))
        self.expect("A01 구현자가 명세를 고친다 → G0", self.gates("implementer", "wo01-base"), {"G0"})
        self.sh("git", "checkout", "-q", "--", "specs")

        self.write("config/gates.toml", (self.dir / "config/gates.toml").read_text(encoding="utf-8")
                   .replace('name = "요구 추적성 (SDD)"', 'name = "요구 추적성"'))
        self.commit("사람이 설정만 바꾸고 헌법을 다시 만들지 않음")
        got = self.gates("implementer", "wo01-base", "GC")
        self.expect("A02 설정이 바뀌었는데 헌법 그대로 → GC", got, {"GC"})
        self.sh("git", "reset", "-q", "--hard", "HEAD~1")

        gates = (self.dir / "config/gates.toml").read_text(encoding="utf-8")
        self.write("config/gates.toml", gates.replace('# deferred = { "FR-007"', 'deferred = { "FR-002" = "H2: 시험" }\n# x = { "FR-007"'))
        self.expect("A03 전체 게이트에 미룬 요구 → GT", self.gates("implementer", "wo01-base", "GT"), {"GT"})
        self.sh("git", "checkout", "-q", "--", "config")

        self.write("src/demo/domain/money.py", IMPL.replace("Money(self.cents + other.cents)", "Money(self.cents)"))
        self.expect("A04 게으른 구현 → G2", self.gates("implementer", "wo01-base"), {"G2"})
        self.sh("git", "checkout", "-q", "--", "src")

    def report(self) -> int:
        all_good = True
        for name, good, detail in self.results:
            all_good &= good
            print(f"{'OK ' if good else 'BAD'} {name}  — {detail}")
        print("결과: 모든 단계가 기대와 일치" if all_good else "결과: 기대와 다른 단계가 있다")
        return 0 if all_good else 1


def main() -> int:
    flow = Flow()
    try:
        flow.run()
    except subprocess.CalledProcessError as e:
        print(f"BAD 흐름 실행 실패: {e.cmd}\n{e.stdout}\n{e.stderr}")
        return 1
    finally:
        code = flow.report()
        shutil.rmtree(flow.dir, ignore_errors=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
