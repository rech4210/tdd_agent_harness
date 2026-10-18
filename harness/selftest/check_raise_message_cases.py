"""하네스 자기 검사: Python 어댑터의 raise-message 등가 규칙 (WO-H30, S4 누수 L-b).

L-b: 등가 규칙이 한 줄 `raise X("문자열")` 만 인식한다. 여러 줄로 나뉜 raise 에서 메시지 리터럴 줄만 바뀐
생존 변이는 등가로 빠지지 않아 G6 점수를 부당하게 낮춘다.

계약 (이번 WO):
- 여러 줄 raise 의 메시지만 바뀐 변이(문자열 리터럴 · None 사이)는 survivor_diff → equivalent 로 등가다.
- 판단 근거는 mutmut show diff 안(문맥 줄 포함)에서만 얻는다. raise 시작이 diff 문맥 밖이면 등가가 아니다 (보수적).
- 한 줄 raise 의 기존 판정은 바뀌지 않는다 (S1). 예외: 끝 주석이 붙은 한 줄 raise 의 메시지 변경은 등가다
  (기존은 정규식이 못 읽어 등가 아님. WO-H30 판정 후 사용자 결정, S08). Core(harness/) 와 어댑터 함수 이름 · 인자는 바뀌지 않는다.
- 단일 인자 뒤 쉼표(`"a",`)는 등가다 (WO-H31 T1, C01~C05). 쓰지 않는 정규식 상수는 남기지 않는다 (C06).

사용: python harness/selftest/check_raise_message_cases.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "harness"), str(ROOT)]
import run_gates  # noqa: E402  (모듈을 불러오기만 한다. 게이트를 실행하지 않는다)
from adapters.python import adapter as py_adapter  # noqa: E402

RULE = "raise-message"


def show(body: str, name: str = "src.demo.x.check__mutmut_1") -> str:
    """mutmut 3.x `mutmut show` 출력 모양: 머리 줄 + unified diff(문맥 3줄)."""
    return f"# {name}: survived\n--- src/demo/x.py\n+++ src/demo/x.py\n@@ -1,7 +1,7 @@\n{body.strip(chr(10))}\n"


def via_diff(text: str) -> tuple[bool, list[str], list[str]]:
    removed, added = py_adapter.survivor_diff("m", lambda cmd: text, {})
    return py_adapter.equivalent(RULE, removed, added), removed, added


# ── 여러 줄 raise: 등가여야 한다 ──
M01 = '''
 def check(x):
     if x < 0:
         raise ValueError(
-            "negative"
+            "XXnegativeXX"
         )
     return x
'''
M02 = '''
 def check(x):
     if x < 0:
         raise ValueError(
-            "negative"
+            None
         )
     return x
'''
M03 = '''
     if x < 0:
         raise ValueError(
             "value must not "
-            "be negative"
+            "XXbe negativeXX"
         )
     return x
'''
M04 = '''
     if x < 0:
         raise ValueError(  # 사용자에게 보이는 문장
-            "negative"
+            "XXnegativeXX"
         )
     return x
'''
M05 = '''
     if x < 0:
         raise errors.DomainError(
-            'negative'
+            'XXnegativeXX'
         )
     return x
'''
M06 = '''
     if x < 0:
         raise ValueError(
-            """negative"""
+            """XXnegativeXX"""
         )
     return x
'''

# ── 여러 줄이지만 등가가 아니다 ──
N01 = '''
     if x < 0:
         logger.error(
-            "negative"
+            "XXnegativeXX"
         )
     return x
'''
N02 = '''
     if x < 0:
         raise ValueError(
-            "negative",
+            "XXnegativeXX",
             x,
         )
'''
N03 = '''
     if x < 0:
         raise ValueError(
-            msg="negative"
+            msg="XXnegativeXX"
         )
     return x
'''
N04 = '''
             "first part "
             "second part "
             "third part "
-            "negative"
+            "XXnegativeXX"
         )
     return x
'''
N05 = '''
         raise ValueError(
             "negative"
         )
-    return "ok"
+    return "XXokXX"
'''
N06 = '''
     if x < 0:
-        raise ValueError(
+        raise TypeError(
             "negative"
         )
     return x
'''
N07 = '''
     if x < 0:
         raise ValueError(
-            "negative"
+            "XXnegativeXX"
         ) from None
     return x
'''
N08 = '''
     if x < 0:
         raise ValueError(
-            f"negative {x}"
+            f"XXnegative {x}XX"
         )
     return x
'''
N09 = '''
     if x < 0:
         raise ValueError(
-            describe(x)
+            describe(None)
         )
     return x
'''
N10 = '''
     if x < 0:
         raise ValueError(
-            3
+            4
         )
     return x
'''
N11 = '''
     if x < 0:
         raise ValueError(
-            "negative"
         )
     return x
'''
N12 = '''
     if x < 0:
         err = ValueError(
-            "negative"
+            "XXnegativeXX"
         )
     return x
'''
N13 = '''
     if x < 0:
         raise ValueError(
-            "negative"
+            "XXnegativeXX"
         )
-    return x
+    return None
'''

# ── 한 줄 raise: 기존 판정 유지 (S1) ──
S01 = '''
     if x < 0:
-        raise ValueError("negative")
+        raise ValueError("XXnegativeXX")
     return x
'''
S02 = '''
     if x < 0:
-        raise ValueError("negative")
+        raise ValueError(None)
     return x
'''
S03 = '''
     if x < 0:
-        raise ValueError()
+        raise ValueError(None)
     return x
'''
S04 = '''
     if x < 0:
-        raise ValueError("negative")
+        raise TypeError("negative")
     return x
'''
S05 = '''
     if x < 0:
-        raise ValueError("negative", x)
+        raise ValueError("XXnegativeXX", x)
     return x
'''
S06 = '''
     if x < 0:
-        log("negative")
+        log("XXnegativeXX")
     return x
'''
S08 = '''
     if x < 0:
-        raise ValueError("negative")  # 사용자에게 보이는 문장
+        raise ValueError("XXnegativeXX")  # 사용자에게 보이는 문장
     return x
'''
S09 = '''
     if x < 0:
-        raise ValueError("negative")  # 사용자에게 보이는 문장
+        raise TypeError("negative")  # 사용자에게 보이는 문장
     return x
'''
C01 = '''
     if x < 0:
-        raise ValueError("negative",)
+        raise ValueError("XXnegativeXX",)
     return x
'''
C02 = '''
     if x < 0:
         raise ValueError(
-            "negative",
+            "XXnegativeXX",
         )
     return x
'''
C03 = '''
     if x < 0:
-        raise ValueError("negative", )
+        raise ValueError(None, )
     return x
'''
C04 = '''
     if x < 0:
-        raise ValueError("negative", x,)
+        raise ValueError("XXnegativeXX", x,)
     return x
'''
C05 = '''
     if x < 0:
-        raise ValueError(("negative",))
+        raise ValueError(("XXnegativeXX",))
     return x
'''
S07 = '''
     if x < 0:
-        raise ValueError(f"negative {x}")
+        raise ValueError(f"XXnegative {x}XX")
     return x
'''


def main() -> int:
    rows: list[tuple[str, bool, str]] = []

    def case(name: str, body: str, expect: bool) -> None:
        got, removed, added = via_diff(show(body))
        rows.append((name, got is expect, f"등가={got} 기대={expect} removed={removed!r} added={added!r}"[:150]))

    case("M01 여러 줄 raise, 메시지 리터럴만 바뀜 → 등가", M01, True)
    case("M02 여러 줄 raise, 메시지 → None → 등가", M02, True)
    case("M03 여러 줄 raise, 이어 붙인 문자열 중 한 줄만 바뀜 → 등가", M03, True)
    case("M04 여러 줄 raise, 여는 괄호 뒤 주석 → 등가", M04, True)
    case("M05 여러 줄 raise, 점이 있는 예외 이름 · 작은따옴표 → 등가", M05, True)
    case("M06 여러 줄 raise, 세 따옴표 문자열 → 등가", M06, True)
    case("N01 여러 줄이지만 raise 가 아닌 호출 → 등가 아님", N01, False)
    case("N02 여러 줄 raise, 인자 2개 → 등가 아님 (한 줄 규칙과 같음)", N02, False)
    case("N03 여러 줄 raise, 키워드 인자 → 등가 아님", N03, False)
    case("N04 raise 시작이 diff 문맥 밖 → 등가 아님 (보수적)", N04, False)
    case("N05 바뀐 줄이 raise 문 다음의 다른 문장 → 등가 아님", N05, False)
    case("N06 여러 줄 raise, 예외 이름이 바뀜 → 등가 아님", N06, False)
    case("N07 여러 줄 raise ... from None → 등가 아님 (한 줄 규칙과 같음)", N07, False)
    case("N08 여러 줄 raise, f-문자열 → 등가 아님", N08, False)
    case("N09 여러 줄 raise, 리터럴이 아닌 인자 → 등가 아님", N09, False)
    case("N10 여러 줄 raise, 숫자 리터럴 → 등가 아님", N10, False)
    case("N11 줄이 지워지기만 함 (추가 없음) → 등가 아님", N11, False)
    case("N12 여러 줄 예외 생성이지만 raise 가 아님 (대입) → 등가 아님", N12, False)
    case("N13 메시지 변경 + raise 밖 다른 줄 변경이 함께 → 등가 아님", N13, False)
    case("S01 한 줄 raise 메시지 변경 → 등가 (기존)", S01, True)
    case("S02 한 줄 raise 메시지 → None → 등가 (기존)", S02, True)
    case("S03 한 줄 raise 빈 인자 → None → 등가 (기존)", S03, True)
    case("S04 한 줄 raise 예외 이름 변경 → 등가 아님 (기존)", S04, False)
    case("S05 한 줄 raise 인자 2개 → 등가 아님 (기존)", S05, False)
    case("S06 한 줄 raise 아닌 호출 → 등가 아님 (기존)", S06, False)
    case("S07 한 줄 raise f-문자열 → 등가 아님 (기존)", S07, False)
    case("S08 한 줄 raise + 끝 주석, 메시지만 바뀜 → 등가 (WO-H30 사용자 결정)", S08, True)
    case("S09 한 줄 raise + 끝 주석, 예외 이름 변경 → 등가 아님", S09, False)
    # WO-H31 T1: 단일 인자 뒤 쉼표 (이전 계약 밖 → 등가로 고정)
    case("C01 한 줄 raise, 단일 인자 뒤 쉼표 → 등가", C01, True)
    case("C02 여러 줄 raise, 단일 인자 뒤 쉼표 → 등가", C02, True)
    case("C03 한 줄 raise, 쉼표 뒤 공백 + None → 등가", C03, True)
    case("C04 한 줄 raise, 인자 2개 + 끝 쉼표 → 등가 아님", C04, False)
    case("C05 튜플 하나를 인자로 → 등가 아님", C05, False)
    rows.append(("C06 쓰지 않는 RAISE_CALL 상수가 어댑터에 남아 있지 않다", not hasattr(py_adapter, "RAISE_CALL"), ""))

    # 한 줄 diff 의 survivor_diff 반환은 바뀌지 않는다 (어댑터 계약 · S1)
    _, removed, added = via_diff(show(S01))
    rows.append(("D01 한 줄 diff: survivor_diff 는 바뀐 줄 그대로 1줄씩",
                 removed == ['        raise ValueError("negative")'] and added == ['        raise ValueError("XXnegativeXX")'],
                 f"{removed!r} {added!r}"[:150]))
    _, removed, added = via_diff(show(N01))
    rows.append(("D02 raise 가 아닌 여러 줄 diff: 바뀐 줄 그대로",
                 removed == ['            "negative"'] and added == ['            "XXnegativeXX"'], f"{removed!r} {added!r}"[:150]))
    rows.append(("D03 알 수 없는 규칙 이름 → 등가 아님", py_adapter.equivalent("unknown-rule", *py_adapter.survivor_diff(
        "m", lambda cmd: show(M01), {})) is False, ""))
    rows.append(("D04 문맥 없는 메시지 줄만으로는 equivalent 가 등가로 보지 않는다",
                 py_adapter.equivalent(RULE, ['            "negative"'], ['            "XXnegativeXX"']) is False, ""))

    # Core 집계 경로: 생존 변이 2개 중 여러 줄 메시지 변이만 제외 (Core 코드는 그대로)
    outputs = {"m_msg": show(M01, "m_msg"), "m_logic": show(N13, "m_logic")}

    def fake_full(cmd: list[str]) -> str:
        line = " ".join(cmd)
        if "results" in line:
            return "    m_msg: survived\n    m_logic: survived\n    m_dead: killed\n"
        return next(text for mid, text in outputs.items() if mid in line)

    saved = run_gates.run_full
    run_gates.run_full = fake_full
    try:
        excluded = run_gates.equivalent_survivors([RULE])
    finally:
        run_gates.run_full = saved
    rows.append(("E01 equivalent_survivors: 여러 줄 메시지 변이만 제외", excluded == [("m_msg", RULE)], repr(excluded)))

    all_good = True
    for name, good, detail in rows:
        all_good &= good
        print(f"{'OK ' if good else 'BAD'} {name}  — {detail}")
    print("결과: 모든 사례가 기대와 일치" if all_good else "결과: 기대와 다른 사례가 있다")
    return 0 if all_good else 1


if __name__ == "__main__":
    sys.exit(main())
