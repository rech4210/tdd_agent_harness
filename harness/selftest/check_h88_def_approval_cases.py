"""하네스 자기 검사: 공통 기본값(DEF-##)은 사람이 승인한 것만 인용 · 명세 안 DEF 정의 금지 (WO-H88).

계약은 dev/WO-H88.md §계약.
- A  승인: 경계 표에서 인용한 DEF 는 공통 기본값 문서의 표 행이고, 그 행의 마지막 칸(승인)이 정의된 D-## 여야 한다.
     자리표시 행(규칙 칸이 비었거나 괄호로만 된 행)은 정의가 아니다. 인용하지 않은 미승인 후보는 위반이 아니다.
- S  범위(defaults_scope): 명세 파일 안에서 DEF-## 를 정의하는 줄(목록 · 제목 · 표 첫 칸)은 위반이다.
     본문 인용 · 경계 표 마지막 칸 인용은 정의가 아니다.
- T  틀: templates/project/docs/DEFAULTS.md 에 자리표시 행이 없고, 미승인 후보 묶음과 일괄 승인 안내가 있다.
사용: python harness/selftest/check_h88_def_approval_cases.py
"""

from __future__ import annotations

import re
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE.parent))

from traceability import check_traceability  # noqa: E402

DECISIONS = "# 결정 기록\n\n## D-01 공통 기본값 일괄 승인\n\n승인.\n"
TEST = '"""t.\n\nguards:      FR-001\ntechnique:   05\nchecklist:   B-2\n"""\n'
CFG = {"requirements": ["specs/*/spec.md"], "decisions": ["reports/decisions.md"], "modules": ["tests/**/test_*.py"],
       "defaults": ["docs/DEFAULTS*.md"]}
HEAD = "# 공통 기본값\n\n| ID | 규칙 | 승인 |\n|:---|:---|:---|\n"


def spec(rows: list[str], extra: str = "") -> str:
    return ("# 기능\n\n## Requirements\n\n- **FR-001**: 입력을 검증한다\n" + extra +
            "\n## Boundary tables\n\n| 입력 | 정의역 | 경계 | 승격 근거 |\n|:---|:---|:---|:---|\n" +
            "".join(r + "\n" for r in rows))


def gt(defaults: str | dict[str, str], rows: list[str], extra: str = "", cfg_extra: dict | None = None):
    root = Path(tempfile.mkdtemp(prefix="h88-gt-"))
    try:
        files = {"specs/001-input/spec.md": spec(rows, extra), "reports/decisions.md": DECISIONS,
                 "tests/unit/test_input.py": TEST}
        if isinstance(defaults, dict):
            files.update(defaults)
        else:
            files["docs/DEFAULTS.md"] = defaults
        for rel, text in files.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text, encoding="utf-8", newline="\n")
        return check_traceability({**CFG, **(cfg_extra or {})}, root)
    finally:
        shutil.rmtree(root, ignore_errors=True)


ROW = "| n | 0 ~ | 0 포함 | {} |"


def main() -> int:
    results: list[tuple[str, bool, str]] = []

    def case(cid: str, good: bool, detail: str) -> None:
        results.append((cid, good, detail))

    def cite(cid: str, defaults, cell: str, expect_ok: bool, need: str = "", **kw) -> None:
        ok, bad, notes = gt(defaults, [ROW.format(cell)], **kw)
        bb = [b for b in bad if b.startswith("[경계]")]
        if expect_ok:
            good = ok and not bad and "계약 안 1" in " ".join(notes)
        else:
            good = (not ok) and bool(bb) and need in " ".join(bb)
        case(cid, good, "; ".join(bad)[:200] or "; ".join(notes)[:200])

    approved = HEAD + "| **DEF-01** | 거부는 상태를 바꾸지 않는다 | D-01 |\n"
    # ---------- A: 승인 ----------
    cite("A01 승인 칸 = 정의된 D-01 → 인용 통과", approved, "DEF-01", True)
    cite("A02 승인 칸 [승인 대기] → [경계] 실패(승인 안 됨)",
         HEAD + "| **DEF-01** | 거부는 상태를 바꾸지 않는다 | [승인 대기] |\n", "DEF-01", False, "승인")
    cite("A03 승인 칸 D-99(결정 기록에 없음) → 실패",
         HEAD + "| **DEF-01** | 거부는 상태를 바꾸지 않는다 | D-99 |\n", "DEF-01", False, "승인")
    cite("A04 승인 칸 '거부: D-01' → 실패(정확히 D-## 만 승인)",
         HEAD + "| **DEF-01** | 거부는 상태를 바꾸지 않는다 | 거부: D-01 |\n", "DEF-01", False, "승인")
    cite("A05 승인 칸 **D-01**(굵게) → 통과", HEAD + "| **DEF-01** | 거부는 상태를 바꾸지 않는다 | **D-01** |\n",
         "DEF-01", True)
    for bad_cell in ("D-01*", "*D-01", "**D-01", "***D-01***"):
        cite(f"A23 승인 칸 '{bad_cell}'(비대칭 · 남는 별표) → 실패(정확히 D-## 또는 **D-##** 만, 마일스톤 검토 A02)",
             HEAD + f"| **DEF-01** | 거부는 상태를 바꾸지 않는다 | {bad_cell} |\n", "DEF-01", False, "승인")
    cite("A06 승인 칸이 없는 2칸 행 → 실패(승인 안 됨)",
         "# 공통 기본값\n\n| ID | 규칙 |\n|:---|:---|\n| **DEF-01** | 거부는 상태를 바꾸지 않는다 |\n",
         "DEF-01", False, "승인")
    cite("A07 제목 형식 정의(## DEF-01) → 실패(승인 칸 없음)",
         "# 공통 기본값\n\n## DEF-01 거부는 상태를 바꾸지 않는다\n\n승인: D-01\n", "DEF-01", False, "승인")
    cite("A08 자리표시 행(규칙 칸이 괄호뿐) → 실패(정의되지 않음), 승인 칸이 D-01 이어도",
         HEAD + "| **DEF-05** | (추가 규칙 정의 시 새 번호 부여) | D-01 |\n", "DEF-05", False, "정의되지 않은")
    cite("A09 규칙 칸이 빈 행 → 실패(정의되지 않음)", HEAD + "| **DEF-05** |  | D-01 |\n", "DEF-05", False,
         "정의되지 않은")
    cite("A10 없는 DEF-09 → 실패(정의되지 않음, 지금 메시지 유지)", approved, "DEF-09", False, "정의되지 않은")
    cite("A11 정의 파일 둘(glob) — 둘째 파일의 승인 행 인용 통과",
         {"docs/DEFAULTS.md": approved, "docs/DEFAULTS-net.md": HEAD + "| DEF-02 | 시간 초과는 거부 | D-01 |\n"},
         "DEF-02", True)
    cite("A12 같은 DEF 가 승인 행 하나 + 미승인 행 하나 → 실패(어느 하나라도 미승인이면 승인 아님)",
         {"docs/DEFAULTS.md": approved, "docs/DEFAULTS-old.md": HEAD + "| DEF-01 | 다른 규칙 | [승인 대기] |\n"},
         "DEF-01", False, "승인")
    # 사전 검토(Sonnet) 보강
    for cid, cell_ in (("A15a", "D-01 (보류)"), ("A15b", "D-01, D-02")):
        cite(f"{cid} 승인 칸 '{cell_}' → 실패(접두 일치 아님)",
             HEAD + f"| **DEF-01** | 거부는 상태를 바꾸지 않는다 | {cell_} |\n", "DEF-01", False, "승인")
    cite("A16 승인 파일이 앞(a) · 미승인 중복이 뒤(b) → 실패(첫 행이 이기지 않음)",
         {"docs/DEFAULTS-a.md": approved, "docs/DEFAULTS-b.md": HEAD + "| DEF-01 | 다른 규칙 | [승인 대기] |\n"},
         "DEF-01", False, "승인")
    cite("A16b 한 파일 안 승인 행 뒤 미승인 중복 → 실패",
         approved + "| DEF-01 | 다른 규칙 | [승인 대기] |\n", "DEF-01", False, "승인")
    cite("A17 4칸 행: 승인은 마지막 칸 — '| DEF-01 | 규칙 | 비고 | D-01 |' 통과",
         "# 기본값\n\n| ID | 규칙 | 비고 | 승인 |\n|:---|:---|:---|:---|\n| DEF-01 | 거부는 상태 불변 | 메모 | D-01 |\n",
         "DEF-01", True)
    cite("A17b 4칸 행: 셋째 칸이 D-01 이어도 마지막 칸이 [승인 대기] → 실패",
         "# 기본값\n\n| ID | 규칙 | 비고 | 승인 |\n|:---|:---|:---|:---|\n| DEF-01 | 거부는 상태 불변 | D-01 | [승인 대기] |\n",
         "DEF-01", False, "승인")
    cite("A18a 자리표시 '(TBD)' → 정의되지 않음", HEAD + "| DEF-05 | (TBD) | D-01 |\n", "DEF-05", False, "정의되지 않은")
    cite("A18b 자리표시 '()' → 정의되지 않음", HEAD + "| DEF-05 | () | D-01 |\n", "DEF-05", False, "정의되지 않은")
    cite("A18c 괄호로 시작하는 진짜 규칙 '(예외 제외) 거부는 …' → 정의 · 승인 통과",
         HEAD + "| DEF-05 | (예외 제외) 거부는 상태를 바꾸지 않는다 | D-01 |\n", "DEF-05", True)
    cite("A19a 기본값 파일의 승인 행이 HTML 주석 안에만 → 정의되지 않음",
         HEAD + "<!--\n| DEF-01 | 거부는 상태 불변 | D-01 |\n-->\n", "DEF-01", False, "정의되지 않은")
    cite("A19b 기본값 파일의 승인 행이 코드 블록 안에만 → 정의되지 않음",
         HEAD + "```\n| DEF-01 | 거부는 상태 불변 | D-01 |\n```\n", "DEF-01", False, "정의되지 않은")
    for cid, dec in (("A20a", "# 결정 기록\n\n```\n## D-01 예시\n```\n"), ("A20b", "# 결정 기록\n\nD-01 은 아직 미정이다.\n")):
        root_dec = {"docs/DEFAULTS.md": approved, "reports/decisions.md": dec}
        cite(f"{cid} 결정 D-01 이 정의 줄이 아님(코드 블록 · 본문 언급) → 승인 아님", root_dec, "DEF-01", False, "승인")
    cite("A21 2칸 행 '| DEF-01 | D-01 |' → 승인 아님(3칸 이상)",
         "# 기본값\n\n| ID | 승인 |\n|:---|:---|\n| **DEF-01** | D-01 |\n", "DEF-01", False, "승인")
    cite("A22 같은 DEF 가 승인 표 행 + 다른 파일의 제목 형식 → 실패(제목은 미승인 정의)",
         {"docs/DEFAULTS.md": approved, "docs/DEFAULTS-h.md": "# 기본값\n\n## DEF-01 다른 규칙\n"},
         "DEF-01", False, "승인")

    # 인용하지 않은 미승인 후보는 위반이 아니다(후보 묶음을 둘 수 있어야 한다)
    ok, bad, _ = gt(approved + "| **DEF-02** | 후보 | [승인 대기] |\n| **DEF-03** | (자리) | [승인 대기] |\n",
                    [ROW.format("DEF-01")])
    case("A13 인용 안 한 미승인 · 자리표시 후보가 있어도 통과", ok and not bad, "; ".join(bad)[:200])
    ok, bad, _ = gt(approved, [ROW.format("D-01, DEF-01")])
    case("A14 회귀: 'D-01, DEF-01' 섞기는 여전히 거부", not ok and any("승격 근거 올바르지 않음" in b for b in bad),
         "; ".join(bad)[:200])

    # ---------- S: 명세 안 DEF 정의 금지 ----------
    def scope(cid: str, extra: str, expect_violation: bool, cfg_extra: dict | None = None,
              rows: list[str] | None = None) -> None:
        ok, bad, _ = gt(approved, rows if rows is not None else [ROW.format("D-01")], extra=extra, cfg_extra=cfg_extra)
        sb = [b for b in bad if b.startswith("[기본값]")]
        if expect_violation:
            good = (not ok) and bool(sb) and "specs/001-input/spec.md" in " ".join(sb)
        else:
            good = ok and not bad
        case(cid, good, "; ".join(bad)[:200] or "통과")

    scope("S01 명세 목록 '- DEF-05: …' 정의 → [기본값] 위반", "\n## 설계 기본값\n\n- DEF-05: JPEG 품질 0.7\n", True)
    scope("S02 명세 목록 '- **DEF-05**: …' → 위반", "\n- **DEF-05**: JPEG 품질 0.7\n", True)
    scope("S03 명세 제목 '### DEF-05 …' → 위반", "\n### DEF-05 JPEG 품질\n", True)
    scope("S04 명세 표 첫 칸 '| DEF-05 | … |' → 위반", "\n| ID | 값 |\n|:---|:---|\n| DEF-05 | 0.7 |\n", True)
    scope("S04b 번호 목록 '1. DEF-05: …' → 위반", "\n1. DEF-05: JPEG 품질 0.7\n", True)
    scope("S05 본문 인용 'DEF-01 에 따라 거부한다' → 위반 아님", "\n거부는 DEF-01 에 따라 상태를 바꾸지 않는다.\n", False)
    scope("S06 경계 표 마지막 칸 인용 → 위반 아님", "", False, rows=[ROW.format("DEF-01")])
    scope("S07 HTML 주석 · 코드 블록 안 정의 → 무시",
          "\n<!--\n- DEF-05: 주석\n-->\n\n```\n- DEF-06: 코드\n```\n", False)
    scope("S08 checks 에 defaults_scope 가 없으면 검사 안 함", "\n- DEF-05: JPEG 품질 0.7\n", False,
          cfg_extra={"checks": ["header", "boundaries"]})
    scope("S09 기본 checks 에 defaults_scope 포함(설정 없이 위반)", "\n- DEF-07: 정렬\n", True)
    ok, bad, _ = gt(approved, [ROW.format("D-01")], extra="\n- DEF-05: x\n")
    case("S10 위반 메시지가 대안(공통 기본값 문서 · D-##)을 알려 줌",
         any(b.startswith("[기본값]") and "D-" in b and "DEFAULTS" in b for b in bad), "; ".join(bad)[:200])

    # 사전 검토(Sonnet) 보강 — 목록 항목은 ID 바로 뒤 콜론이 있어야 정의(계약 3)
    scope("S11a 목록 안 인용 '- 거부는 DEF-01 에 따라 …' → 위반 아님", "\n- 거부는 DEF-01 에 따라 상태를 바꾸지 않는다\n", False)
    scope("S11b 목록 안 인용 '- FR-002 근거: DEF-01' → 위반 아님", "\n- 비고 근거: DEF-01\n", False)
    scope("S11c 줄머리 인용 '- DEF-01 에 따라 거부한다'(콜론 없음) → 위반 아님", "\n- DEF-01 에 따라 거부한다\n", False)
    spec_extra = "\n- DEF-05: JPEG 품질 0.7\n- DEF-06: 한 번만 시도\n"
    ok, bad, _ = gt(approved, [ROW.format("D-01")], extra=spec_extra)
    sb = [b for b in bad if b.startswith("[기본값]")]
    full = spec([ROW.format("D-01")], spec_extra).splitlines()
    want_ln = [i + 1 for i, line in enumerate(full) if line.startswith("- DEF-0")]
    case("S12 위반 줄마다 1건(2줄 → 2건)", len(sb) == 2, "; ".join(sb)[:200])
    case("S13 메시지에 정확한 줄 번호와 ID",
         len(sb) == 2 and f"spec.md:{want_ln[0]}" in sb[0] and "'DEF-05'" in sb[0]
         and f"spec.md:{want_ln[1]}" in sb[1] and "'DEF-06'" in sb[1], f"want={want_ln} got={sb}")

    # ---------- T: 틀 ----------
    tpl = (ROOT / "templates" / "project" / "docs" / "DEFAULTS.md").read_text(encoding="utf-8")
    rows = [r for r in tpl.splitlines() if re.match(r"^\|\s*\*{0,2}DEF-\d+\*{0,2}\s*\|", r)]
    cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
    ids = [c[0].strip("* ") for c in cells]
    case("T01 틀에 자리표시 행 없음(규칙 칸이 비었거나 괄호뿐인 DEF 행 0)",
         bool(cells) and not any((len(c) < 2 or not c[1] or re.fullmatch(r"\(.*\)", c[1])) for c in cells),
         str([c[:2] for c in cells]))
    case("T02 후보 묶음 8개 이상, 모두 미승인([승인 대기]) — 틀이 대신 승인하지 않는다",
         len(cells) >= 8 and all(len(c) >= 3 and c[-1] == "[승인 대기]" for c in cells), f"{len(cells)}개")
    case("T03 DEF 번호가 겹치지 않고 01부터 연속",
         ids == [f"DEF-{i:02d}" for i in range(1, len(ids) + 1)], str(ids))
    head = tpl.split("## 1.")[0]
    case("T04 머리말: 한 번의 결정 기록(D-##)으로 일괄 승인 · 승인 칸에 그 D-## · 미승인은 인용 불가",
         all(s in head for s in ("D-##", "승인 칸", "인용할 수 없")), "")
    case("T05 머리말: 슬라이스 모델 기본값은 명세에 DEF 로 번호 매기지 않는다",
         "DEF 로 번호" in head or "DEF 번호를 쓰지 않" in head, "")

    bad_n = 0
    for name, good, detail in results:
        print(f"{'OK ' if good else 'BAD'} {name}{('  — ' + detail) if detail and not good else ''}")
        bad_n += 0 if good else 1
    print(f"\n{len(results) - bad_n}/{len(results)} 통과")
    return 1 if bad_n else 0


if __name__ == "__main__":
    sys.exit(main())
