"""하네스 자기 검사: 한 마디 금지 호출과 Python 내장 이름 (WO-H31 T2, 증명 F4).

F4: forbidden_calls 의 한 마디 항목(예: ["open"])은 이름 전체가 그 낱말일 때만 맞는다 (Core 규칙, 바뀌지 않음).
Python 에서는 같은 내장 함수를 `builtins.open` · `import builtins as b; b.open` · `from builtins import open as o` 로도
부를 수 있어 한 마디 항목을 피해 간다.

계약 (이번 WO):
- Python 어댑터의 uses 가 최상위 `builtins.<이름>` 을 쓰면 내장 이름 `<이름>` 도 함께 낸다 (원래 이름은 그대로 남긴다).
- 그래서 한 마디 항목이 내장 호출의 점 이름 표기(import 별칭 · from import)를 막고, 두 마디 항목(["builtins", "open"])도 계속 맞는다.
- 계약 밖 (WO-H33 레드팀): 동적 접근(`getattr(b, "open")` · `__import__("builtins").open` · `b.__dict__["open"]`)과
  같은 별칭 이름을 함수마다 다른 모듈로 다시 import 하는 경우(별칭을 파일 전체에서 하나로 푸는 기존 한계, v0.5 이전부터).
- 다른 객체의 같은 이름 메서드(`p.open`, `os.open`), 최상위가 아닌 builtins(`mylib.builtins.open`)는 맞지 않는다.
- Core(harness/) 는 바뀌지 않는다. 계약 밖: import 없이 지역 변수 이름이 builtins 인 경우.

사용: python harness/selftest/check_arch_builtins_cases.py
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "harness"), str(ROOT)]
from arch import check_architecture  # noqa: E402
from adapters.python import adapter as py_adapter  # noqa: E402

CFG = {"package": "pkg", "inner": ["domain"], "checks": ["inner"], "forbidden_calls": [["open"]]}


def graph_of(body: str) -> list[dict]:
    tmp = Path(tempfile.mkdtemp(prefix="arch-builtins-"))
    try:
        (tmp / "pkg" / "domain").mkdir(parents=True)
        (tmp / "pkg" / "__init__.py").write_text("", encoding="utf-8")
        (tmp / "pkg" / "domain" / "__init__.py").write_text("", encoding="utf-8")
        (tmp / "pkg" / "domain" / "io.py").write_text(body, encoding="utf-8")
        return py_adapter.import_graph(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def verdict(body: str, cfg: dict | None = None) -> tuple[bool, list[str]]:
    return check_architecture({**CFG, **(cfg or {})}, graph_of(body), {"pkg"})


def main() -> int:
    rows: list[tuple[str, bool, str]] = []

    def case(cid: str, why: str, body: str, expect_ok: bool, cfg: dict | None = None) -> None:
        ok, bad = verdict(body, cfg)
        rows.append((f"{cid} {why}", ok == expect_ok, f"{'PASS' if ok else 'FAIL'} {bad[:2]}"[:150]))

    case("B01", "open(...) → 한 마디 항목에 걸림 (기존)", "def f():\n    return open('x')\n", False)
    case("B02", "import builtins; builtins.open(...) → 걸림", "import builtins\n\ndef f():\n    return builtins.open('x')\n", False)
    case("B03", "import builtins as b; b.open(...) → 걸림", "import builtins as b\n\ndef f():\n    return b.open('x')\n", False)
    case("B04", "from builtins import open as o; o(...) → 걸림", "from builtins import open as o\n\ndef f():\n    return o('x')\n", False)
    case("B05", "p.open() (다른 객체의 메서드) → 걸리지 않음", "def f(p):\n    return p.open()\n", True)
    case("B06", "import os; os.open(...) → 한 마디 항목에 걸리지 않음", "import os\n\ndef f():\n    return os.open('x', 0)\n", True)
    case("B07", "두 마디 항목 [builtins, open] 도 builtins.open 을 계속 막음",
         "import builtins\n\ndef f():\n    return builtins.open('x')\n", False, {"forbidden_calls": [["builtins", "open"]]})
    case("B08", "builtins.print 는 [open] 에 걸리지 않음", "import builtins\n\ndef f():\n    builtins.print('x')\n", True)
    case("B09", "최상위가 아닌 mylib.builtins.open → 걸리지 않음",
         "import mylib.builtins as mb\n\ndef f():\n    return mb.open('x')\n", True)
    case("B10", "두 마디 항목 [datetime, now] 판정은 그대로 (기존)",
         "from datetime import datetime\n\ndef f():\n    return datetime.now()\n", False, {"forbidden_calls": [["datetime", "now"]]})
    case("B11", "금지 항목이 없으면 builtins.open 도 통과 (기존)",
         "import builtins\n\ndef f():\n    return builtins.open('x')\n", True, {"forbidden_calls": []})

    # uses 계약: 원래 이름을 남기고 내장 이름을 덧붙인다
    uses = next(f for f in graph_of("import builtins\n\ndef f():\n    return builtins.open('x')\n")
                if f["file"].endswith("io.py"))["uses"]
    names = {u.split(":", 1)[1] for u in uses}
    rows.append(("U01 uses 에 builtins.open 과 open 이 함께 있다", {"builtins.open", "open"} <= names, repr(sorted(names))))
    uses = next(f for f in graph_of("def f(p):\n    return p.open()\n") if f["file"].endswith("io.py"))["uses"]
    names = {u.split(":", 1)[1] for u in uses}
    rows.append(("U02 p.open 은 open 을 덧붙이지 않는다", "open" not in names, repr(sorted(names))))

    all_good = True
    for name, good, detail in rows:
        all_good &= good
        print(f"{'OK ' if good else 'BAD'} {name}  — {detail}")
    print("결과: 모든 사례가 기대와 일치" if all_good else "결과: 기대와 다른 사례가 있다")
    return 0 if all_good else 1


if __name__ == "__main__":
    sys.exit(main())
