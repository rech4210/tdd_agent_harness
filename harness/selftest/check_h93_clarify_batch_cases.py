"""하네스 자기 검사: clarify 옵트아웃 — 일괄 승인 패키지의 기계적 전개와 게이트 검사 (WO-H93, HD-83).

계약은 dev/WO-H93.md.
- E: `harness/decisions.py expand` 가 패키지를 질문마다 D-## 로 원문 그대로 전개
- X: 형식 · 제외 범위(위험도 high · H#) · 인자 위반은 종료 2, 결정 파일 그대로
- G: 추적 검사 `batch_integrity` 가 기록 = 사람이 본 패키지(해시 · 원문)를 확인
사용: python harness/selftest/check_h93_clarify_batch_cases.py
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "harness" / "decisions.py"
PY = sys.executable

EXISTING = """# 결정 기록 (fixture)

<!-- D-40 은 주석 안이라 정의가 아니다 -->

| ID | 결정 | 고려한 대안 | 이유 | 출처 | 추천과 다름 | 지키는 테스트 |
|:---|:---|:---|:---|:---|:---|:---|
| **D-01** | 금액은 정수 원 | 실수 | 반올림 오류 | 사용자 | 아니오 | `tests/test_a.py::t` |
| **D-02** | 이름 1~40자 | 무제한 | 화면 | 사용자 | 아니오 | `tests/test_a.py::u` |

결정자: 사람, 2026-10-01 (S1)

## D-11 긴 결정

| 항목 | 내용 |
|:---|:---|
| **결정** | 정렬은 ID 순 |

결정자: 사람, 2026-10-02 (S1)
"""

PKG = """# 일괄 질문 B-01

위험도: low
슬라이스: S1

| Q | 질문 | 추천 | 근거 | 대안 |
|:---|:---|:---|:---|:---|
| Q1 | 빈 제목을 허용하나? | 거부(오류 코드 EMPTY) | 빈 항목은 목록에서 구분 불가 | 허용하고 "(제목 없음)" 표시 |
| Q2 | 같은 영상 중복 기록은? | 마지막 기록으로 덮어씀 | 사용자는 최신 시청 위치만 필요 | 모두 보존 |
| Q3 | 목록 최대 개수는? | 상한 없음 | 개인 기록이라 수백 개 수준 | 1000개 상한 \\| 초과 시 거부 |
"""


def norm_hash(text: str) -> str:
    return hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def load_trace():
    spec = importlib.util.spec_from_file_location("h93_traceability", ROOT / "harness" / "traceability.py")
    mod = importlib.util.module_from_spec(spec)
    if str(ROOT / "harness") not in sys.path:
        sys.path.insert(0, str(ROOT / "harness"))
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    results: list[tuple[str, bool, str]] = []

    def case(name: str, good: bool, detail: str = "") -> None:
        results.append((name, good, detail))

    trace = load_trace()
    base = Path(tempfile.mkdtemp(prefix="h93-"))

    def project(name: str, pkg: str | None = PKG, existing: str | None = EXISTING, pkg_name: str = "B-01") -> Path:
        r = base / name
        (r / "reports" / "clarify").mkdir(parents=True)
        (r / "tests").mkdir()
        (r / "tests" / "test_x.py").write_text('"""guards: D-01\ntechnique: x\nchecklist: x"""\n', encoding="utf-8")
        if existing is not None:
            (r / "reports" / "decisions.md").write_text(existing, encoding="utf-8")
        if pkg is not None:
            (r / "reports" / "clarify" / f"{pkg_name}.md").write_text(pkg, encoding="utf-8")
        return r

    def expand(r: Path, *extra: str, pkg: str = "reports/clarify/B-01.md", approval: str | None = "OK",
               by: str = "user"):
        a = [PY, str(TOOL), "expand", "--root", str(r), "--package", pkg, "--by", by, "--date", "2026-10-30"]
        if approval is not None:
            a += ["--approval", approval]
        a += list(extra)
        p = subprocess.run(a, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=r)
        # 도구가 없거나(python 의 종료 2) 인자가 없어 argparse 가 낸 2 는 계약상 거부가 아니다(헛통과 방지)
        if "unrecognized arguments" in p.stderr or "invalid choice" in p.stderr or "can't open file" in p.stderr:
            p.returncode = 99
        try:
            j = json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else {}
        except Exception:
            j = {}
        return p, j

    def dec(r: Path) -> str:
        f = r / "reports" / "decisions.md"
        return f.read_text(encoding="utf-8") if f.exists() else ""

    def gate(r: Path, checks=("batch_integrity",), extra_cfg=None):
        cfg = {"requirements": ["specs/*/spec.md"], "decisions": ["reports/decisions.md"],
               "modules": ["tests/test_*.py"], "checks": list(checks)}
        cfg.update(extra_cfg or {})
        try:
            ok, bad, _ = trace.check_traceability(cfg, r)
        except Exception as e:  # 구현 전(검사 없음) · 예외는 실패로
            return False, [f"예외: {e!r}"]
        return ok, bad

    def tagged(bad: list[str]) -> bool:
        return any(b.startswith("[일괄]") for b in bad)

    def rows(text: str) -> dict[str, list[str]]:
        out = {}
        for line in text.splitlines():
            m = re.match(r"^\|\s*\*\*(D-\d+)\*\*\s*\|", line)
            if m and "| Q" in line:
                cells = [c.strip() for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
                out[m.group(1)] = cells
        return out

    try:
        # ---------------- E: 전개 ----------------
        r = project("e01")
        before = dec(r)
        p, j = expand(r)
        text = dec(r)
        case("E01 전개: 종료 0 · Q1~Q3 → D-12~D-14(기존 최대 D-11 다음, 주석 속 D-40 무시)",
             p.returncode == 0 and j.get("batch") == "B-01"
             and j.get("decisions") == {"Q1": "D-12", "Q2": "D-13", "Q3": "D-14"} and j.get("skipped") == [],
             f"rc={p.returncode} {j} {p.stderr[-300:]}")
        case("E02 기존 내용은 그대로 앞에 남고 블록이 끝에 붙음", text.startswith(before) and "## 일괄 승인 B-01" in text,
             text[-400:])
        h = norm_hash(PKG)
        head_ok = re.search(r"^패키지: `reports/clarify/B-01\.md` · sha256 `" + h + r"` · 승인 원문: \"OK\" · 결정자: 사람, 2026-10-30$",
                            text, re.M)
        case("E03 머리 줄: 패키지 경로 · 정규화 sha256 · 승인 원문 · 결정자 · 날짜", bool(head_ok), text[-600:])
        rw = rows(text)
        exp1 = ["**D-12**", "Q1", "빈 제목을 허용하나?", "거부(오류 코드 EMPTY)", "허용하고 \"(제목 없음)\" 표시",
                "빈 항목은 목록에서 구분 불가", "아니오", "(보강 예정: 일괄 B-01 Q1)"]
        case("E04 행: ID · Q · 질문 · 결정=추천 · 고려한 대안=대안 · 근거 · 아니오 · 지키는 테스트 표시",
             rw.get("D-12") == exp1, str(rw.get("D-12")))
        case("E05 칸 안 '\\|' 는 그대로 보존", rw.get("D-14", [""] * 5)[4] == "1000개 상한 \\| 초과 시 거부",
             str(rw.get("D-14")))
        ok, bad = gate(r)
        case("E06 전개 결과는 게이트 batch_integrity 통과", "## 일괄 승인 B-01" in dec(r) and ok and not bad, str(bad))

        r = project("e07")
        p, j = expand(r, "--alt", "Q2")
        rw = rows(dec(r))
        case("E07 --alt Q2: 결정 = 대안, 고려한 대안 = 추천, 추천과 다름 = 예",
             p.returncode == 0 and rw.get("D-13", [None] * 7)[3:7] == ["모두 보존", "마지막 기록으로 덮어씀",
                                                                       "사용자는 최신 시청 위치만 필요", "예"],
             f"rc={p.returncode} {rw.get('D-13')} {p.stderr[-200:]}")
        ok, bad = gate(r)
        case("E08 대안 채택 결과도 게이트 통과", "## 일괄 승인 B-01" in dec(r) and ok, str(bad))

        r = project("e09")
        p, j = expand(r, "--skip", "Q2")
        text = dec(r)
        rw = rows(text)
        case("E09 --skip Q2: Q1 → D-12, Q3 → D-13, '건너뜀: Q2' 줄",
             p.returncode == 0 and j.get("decisions") == {"Q1": "D-12", "Q3": "D-13"} and j.get("skipped") == ["Q2"]
             and re.search(r"^건너뜀: Q2$", text, re.M) is not None and set(rw) == {"D-12", "D-13"},
             f"rc={p.returncode} {j}")
        ok, bad = gate(r)
        case("E10 건너뛴 결과도 게이트 통과", "## 일괄 승인 B-01" in dec(r) and ok, str(bad))

        r = project("e11", existing=None)
        p, j = expand(r)
        case("E11 결정 파일이 없으면 만든다(D-01 부터)", p.returncode == 0 and j.get("decisions", {}).get("Q1") == "D-01"
             and "## 일괄 승인 B-01" in dec(r), f"rc={p.returncode} {j} {p.stderr[-200:]}")

        r = project("e12")
        # write_text 는 Windows 에서 \n 을 다시 \r\n 으로 바꿔 \r\r\n 이 된다 — 바이트로 정확히 CRLF 를 쓴다
        (r / "reports" / "clarify" / "B-01.md").write_bytes(PKG.replace("\n", "\r\n").encode("utf-8"))
        p, j = expand(r)
        case("E12 CRLF 패키지도 같은 정규화 해시", p.returncode == 0 and norm_hash(PKG) in dec(r), p.stderr[-200:])

        r = project("e13")
        p, j = expand(r, approval='좋아요 "전부" OK | 진행')
        case("E13 승인 원문의 따옴표 · '|' 를 이스케이프해 한 줄로", p.returncode == 0
             and '승인 원문: "좋아요 \\"전부\\" OK \\| 진행"' in dec(r), dec(r)[-300:])
        ok, bad = gate(r)
        case("E14 이스케이프한 승인 원문도 게이트 통과", "## 일괄 승인 B-01" in dec(r) and ok, str(bad))

        # ---------------- X: 거부 ----------------
        def rejected(cid: str, desc: str, pkg_text: str | None, *extra: str, approval: str | None = "OK",
                     by: str = "user", pkg_path: str = "reports/clarify/B-01.md", pkg_name: str = "B-01") -> None:
            r = project(cid.lower(), pkg=pkg_text, pkg_name=pkg_name)
            before = dec(r)
            p, _ = expand(r, *extra, approval=approval, by=by, pkg=pkg_path)
            case(f"{cid} {desc} → 종료 2, 결정 파일 그대로", p.returncode == 2 and dec(r) == before,
                 f"rc={p.returncode} {p.stderr[-200:]}")

        rejected("X01", "위험도 high(Q2 제외 범위)", PKG.replace("위험도: low", "위험도: high"))
        rejected("X02", "추천에 H# (사람 개입 구간)", PKG.replace("마지막 기록으로 덮어씀", "H4 에서 사람이 정함"))
        rejected("X03", "대안에 H#", PKG.replace("모두 보존", "H10 로 미룸"))
        rejected("X04", "Q 번호 빈칸(Q1, Q3)", PKG.replace("| Q2 |", "| Q4 |"))
        rejected("X05", "빈 칸", PKG.replace("| 사용자는 최신 시청 위치만 필요 |", "|   |"))
        rejected("X06", "칸 수 다름", PKG.replace("| 모두 보존 |", "| 모두 보존 | 덤 |"))
        rejected("X07", "제목 ID ≠ 파일 이름", PKG.replace("# 일괄 질문 B-01", "# 일괄 질문 B-02"))
        rejected("X08", "--by 가 user 아님", PKG, by="agent")
        rejected("X09", "승인 원문 비었음", PKG, approval="  ")
        rejected("X10", "--alt 의 Q 가 패키지에 없음", PKG, "--alt", "Q9")
        rejected("X11", "--alt · --skip 같은 Q", PKG, "--alt", "Q2", "--skip", "Q2")
        rejected("X12", "패키지 경로가 저장소 밖(..)", PKG, pkg_path="../outside/B-01.md")
        rejected("X13", "날짜 형식 틀림", PKG, "--date", "2026/10/30")
        rejected("X14", "위험도 줄 없음", PKG.replace("위험도: low\n", ""))
        rejected("X15", "위험도 값 모름", PKG.replace("위험도: low", "위험도: tiny"))
        r = project("x16")
        expand(r)
        once = dec(r)
        p, _ = expand(r)
        case("X16 같은 일괄 ID 를 두 번 전개 → 종료 2, 중복 블록 없음", p.returncode == 2 and dec(r) == once,
             f"rc={p.returncode}")

        # ---------------- G: 게이트 ----------------
        def expanded(name: str, *extra: str) -> Path:
            r = project(name)
            p, _ = expand(r, *extra)
            assert p.returncode == 0, p.stderr
            return r

        def g_bad(cid: str, desc: str, r: Path) -> None:
            ok, bad = gate(r)
            case(f"{cid} {desc} → [일괄] 위반", not ok and tagged(bad), str(bad))

        try:
            r = expanded("g01")
            pk = r / "reports" / "clarify" / "B-01.md"
            pk.write_text(pk.read_text(encoding="utf-8").replace("거부(오류 코드 EMPTY)", "거부"), encoding="utf-8")
            g_bad("G01", "전개 뒤 패키지를 고침(해시 불일치)", r)

            r = expanded("g02")
            d = r / "reports" / "decisions.md"
            d.write_text(d.read_text(encoding="utf-8").replace("| 거부(오류 코드 EMPTY) |", "| 거부(오류 코드 EMPTY, 공백만도) |"),
                         encoding="utf-8")
            g_bad("G02", "기록의 결정 칸을 다듬음(사람이 보지 않은 내용)", r)

            r = expanded("g03")
            d = r / "reports" / "decisions.md"
            d.write_text(d.read_text(encoding="utf-8").replace("| 같은 영상 중복 기록은? |", "| 같은 영상 중복 기록 처리는? |"),
                         encoding="utf-8")
            g_bad("G03", "기록의 질문 칸을 바꿈", r)

            r = expanded("g04")
            d = r / "reports" / "decisions.md"
            t = d.read_text(encoding="utf-8")
            t = re.sub(r"(\| \*\*D-13\*\* \|.*)\| 아니오 \|", r"\1| 예 |", t)
            d.write_text(t, encoding="utf-8")
            g_bad("G04", "추천과 다름만 '예' 로 바꾸고 결정 · 대안은 그대로", r)

            r = expanded("g05")
            d = r / "reports" / "decisions.md"
            d.write_text("\n".join(l for l in d.read_text(encoding="utf-8").splitlines() if "**D-13**" not in l) + "\n",
                         encoding="utf-8")
            g_bad("G05", "Q2 행을 지움(건너뜀에도 없음)", r)

            r = expanded("g06", "--skip", "Q2")
            d = r / "reports" / "decisions.md"
            d.write_text(d.read_text(encoding="utf-8").replace("건너뜀: Q2", "건너뜀: Q2, Q1"), encoding="utf-8")
            g_bad("G06", "같은 Q 가 행과 건너뜀에 모두", r)

            r = expanded("g07")
            (r / "reports" / "clarify" / "B-01.md").unlink()
            g_bad("G07", "패키지 파일 없음", r)

            r = expanded("g08")
            d = r / "reports" / "decisions.md"
            t = d.read_text(encoding="utf-8")
            blk = t[t.index("## 일괄 승인 B-01"):]
            d.write_text(t + "\n" + blk.replace("D-12", "D-15").replace("D-13", "D-16").replace("D-14", "D-17"),
                         encoding="utf-8")
            g_bad("G08", "같은 일괄 ID 블록 둘", r)

            r = expanded("g09")
            pk = r / "reports" / "clarify" / "B-01.md"
            pk.write_bytes(pk.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
            ok, bad = gate(r)
            case("G09 체크아웃이 패키지를 CRLF 로 바꿔도 통과(정규화 해시)", ok, str(bad))

            # 도구를 거치지 않고 손으로 쓴 블록: 게이트가 독립적으로 제외 범위를 본다
            r = expanded("g10")
            pk = r / "reports" / "clarify" / "B-01.md"
            hi = PKG.replace("위험도: low", "위험도: high")
            pk.write_text(hi, encoding="utf-8")
            d = r / "reports" / "decisions.md"
            d.write_text(d.read_text(encoding="utf-8").replace(norm_hash(PKG), norm_hash(hi)), encoding="utf-8")
            g_bad("G10", "해시를 맞춘 손 블록이라도 패키지 위험도 high", r)

            r = expanded("g11")
            d = r / "reports" / "decisions.md"
            d.write_text(d.read_text(encoding="utf-8").replace(" · 결정자: 사람, 2026-10-30", ""), encoding="utf-8")
            g_bad("G11", "머리 줄에 결정자 없음", r)

            r = expanded("g12")
            d = r / "reports" / "decisions.md"
            d.write_text(d.read_text(encoding="utf-8").replace("`reports/clarify/B-01.md`", "`../B-01.md`"), encoding="utf-8")
            g_bad("G12", "머리 줄 패키지 경로가 저장소 밖", r)

            r = expanded("g13")
            d = r / "reports" / "decisions.md"
            t = d.read_text(encoding="utf-8")
            t = t.replace("| **D-12** | Q1 |", "| **D-12** | Q2 |", 1)
            d.write_text(t, encoding="utf-8")
            g_bad("G13", "같은 Q 를 두 행이 씀", r)
        except AssertionError as e:
            case("G00 게이트 사례 준비(전개) 실패", False, str(e)[-300:])

        r = project("g14")
        ok, bad = gate(r)
        case("G14 대조: 일괄 블록이 없는 결정 파일은 통과(헛위반 없음)", ok and not bad, str(bad))

        src = (ROOT / "harness" / "traceability.py").read_text(encoding="utf-8")
        m = re.search(r'cfg\.get\(\s*"checks",\s*\[(.*?)\]', src, re.S)
        case("G15 batch_integrity 가 기본 checks 목록에 있음", bool(m) and '"batch_integrity"' in m.group(1), "")

        r = project("g16")
        expand(r)
        (r / "specs" / "s1").mkdir(parents=True)
        (r / "specs" / "s1" / "spec.md").write_text(
            "# S1\n\n## Clarifications\n\n- Q: 빈 제목? → A: 거부 → D-12\n", encoding="utf-8")
        ok, bad = gate(r, checks=("clarifications",))
        (r / "specs" / "s1" / "spec.md").write_text(
            "# S1\n\n## Clarifications\n\n- Q: 빈 제목? → A: 거부 → D-99\n", encoding="utf-8")
        ok2, bad2 = gate(r, checks=("clarifications",))
        case("G16 전개된 D-12 는 명세에서 인용 가능(정의됨), 대조 D-99 는 위반", ok and not ok2, f"{bad} / {bad2}")

        # ---------------- 사전 검토 보강 (Codex sol 1회차 R01 ~ R19) ----------------
        def tamper(r: Path, old: str, new: str, rel: str = "reports/decisions.md") -> None:
            f = r / rel
            t = f.read_text(encoding="utf-8")
            assert old in t, (old, t[-500:])
            f.write_text(t.replace(old, new, 1), encoding="utf-8")

        def set_pkg(r: Path, new_text: str, rel: str = "reports/decisions.md") -> None:
            pk = r / "reports" / "clarify" / "B-01.md"
            old_h = norm_hash(pk.read_text(encoding="utf-8"))
            pk.write_text(new_text, encoding="utf-8")
            tamper(r, old_h, norm_hash(new_text), rel)

        try:
            r = expanded("g17")
            tamper(r, "| 빈 항목은 목록에서 구분 불가 |", "| 빈 항목은 구분이 어렵다 |")
            g_bad("G17", "기록의 근거 칸만 바꿈", r)

            r = expanded("g18")
            tamper(r, "| 허용하고 \"(제목 없음)\" 표시 |", "| 허용 |")
            g_bad("G18a", "기록의 고려한 대안 칸만 바꿈(추천 채택 행)", r)
            r = expanded("g18b", "--alt", "Q2")
            tamper(r, "| 모두 보존 | 마지막 기록으로 덮어씀 |", "| 모두 보존 | 덮어씀 |")
            g_bad("G18b", "대안 채택 행의 고려한 대안(= 추천)만 바꿈", r)

            for cid, col_old, col_new in (("G19a", "| 마지막 기록으로 덮어씀 |", "| H4 에서 정함 |"),
                                          ("G19b", "| 모두 보존 |", "| H10 로 미룸 |")):
                r = expanded(cid.lower())
                pkt = PKG.replace(col_old, col_new)
                set_pkg(r, pkt)
                tamper(r, col_old, col_new)
                g_bad(cid, "해시 · 행을 맞춘 손 블록이라도 H# 질문(" + ("추천" if cid == "G19a" else "대안") + ")", r)

            r = expanded("g20a")
            set_pkg(r, PKG.replace("# 일괄 질문 B-01", "# 일괄 질문 B-02"))
            g_bad("G20a", "패키지 제목 ID ≠ 블록 ID(해시는 맞춤)", r)
            r = expanded("g20b")
            set_pkg(r, PKG.replace("| Q3 |", "| Q4 |"))
            tamper(r, "| Q3 |", "| Q4 |")
            g_bad("G20b", "패키지 Q 번호 빈칸(행 · 해시는 맞춤)", r)
            r = expanded("g20c")
            set_pkg(r, PKG.replace("| 사용자는 최신 시청 위치만 필요 |", "|  |"))
            tamper(r, "| 사용자는 최신 시청 위치만 필요 |", "|  |")
            g_bad("G20c", "패키지 빈 칸(행 · 해시는 맞춤)", r)
            r = expanded("g20d")
            set_pkg(r, PKG.replace("| 허용하고 \"(제목 없음)\" 표시 |", "| 허용하고 \"(제목 없음)\" 표시 | 덤 |"))
            g_bad("G20d", "패키지 칸 수 다름(해시는 맞춤)", r)

            r = expanded("g21", "--skip", "Q2")
            tamper(r, "건너뜀: Q2", "건너뜀: Q99")
            g_bad("G21", "건너뜀 Q 를 없는 번호로 바꿈(개수는 같음)", r)

            r = expanded("g22")
            line = next(l for l in dec(r).splitlines() if l.startswith("| **D-12** |"))
            tamper(r, line, line + "\n" + line.replace("D-12", "D-15"))
            g_bad("G22", "같은 Q1 행을 다른 D 로 복제(원문은 모두 맞음)", r)

            r = expanded("g23")
            tamper(r, "| **D-12** | Q1 |", "| **X-12** | Q1 |")
            g_bad("G23", "행 ID 가 D 형식 아님", r)

            r = expanded("g24")
            f = r / "reports" / "decisions.md"
            f.write_text(f.read_text(encoding="utf-8") + "\n## 별도 기록\n\n| ID | Q | 질문 | 결정 | 고려한 대안 | 근거 | 추천과 다름 | 지키는 테스트 |\n"
                         "|:---|:---|:---|:---|:---|:---|:---|:---|\n| **D-20** | Q9 | 다른 질문 | 다른 답 | 대안 | 이유 | 아니오 | t |\n",
                         encoding="utf-8")
            ok, bad = gate(r)
            case("G24 블록은 다음 '## ' 제목에서 끝남 — 뒤 절의 비슷한 표는 일괄 검사 대상 아님", ok, str(bad))

            two = {"decisions": ["reports/decisions.md", "reports/decisions-2.md"]}
            r = project("g25a")
            (r / "reports" / "decisions-2.md").write_text("# 결정 기록 2\n", encoding="utf-8")
            p, _ = expand(r, "--decisions", "reports/decisions-2.md")
            tamper(r, "| 거부(오류 코드 EMPTY) |", "| 거부 |", rel="reports/decisions-2.md")
            ok, bad = gate(r, extra_cfg=two)
            case("G25a 설정된 둘째 결정 파일의 변조 블록도 [일괄] 위반", p.returncode == 0 and not ok and tagged(bad), str(bad))
            r = expanded("g25b")
            t = dec(r)
            blk = t[t.index("## 일괄 승인 B-01"):]
            (r / "reports" / "decisions-2.md").write_text(
                "# 결정 기록 2\n\n" + blk.replace("D-12", "D-31").replace("D-13", "D-32").replace("D-14", "D-33"),
                encoding="utf-8")
            ok, bad = gate(r, extra_cfg=two)
            case("G25b 같은 일괄 ID 가 두 파일에 나뉘어 있어도 [일괄] 위반", not ok and tagged(bad), str(bad))

            r = expanded("g26")
            f = r / "reports" / "decisions.md"
            f.write_text(f.read_text(encoding="utf-8") + "\n<!--\n## 일괄 승인 B-07\n패키지: `nope.md` · sha256 `x`\n-->\n\n"
                         "```\n## 일괄 승인 B-08\n| **D-50** | Q1 | a | b | c | d | 아니오 | t |\n```\n", encoding="utf-8")
            ok, bad = gate(r)
            case("G26 주석 · 코드 블록 속 가짜 일괄 블록은 검사하지 않음(헛위반 없음)", ok, str(bad))

            # 기본 checks(키 생략)로도 실제로 검사한다
            def default_gate(r: Path):
                cfg = {"requirements": ["specs/*/spec.md"], "decisions": ["reports/decisions.md"],
                       "modules": ["tests/test_*.py"]}
                try:
                    return trace.check_traceability(cfg, r)[1]
                except Exception as e:
                    return [f"예외: {e!r}"]
            r = expanded("g27")
            (r / "specs" / "s1").mkdir(parents=True)
            (r / "specs" / "s1" / "spec.md").write_text("# S1\n", encoding="utf-8")
            clean = default_gate(r)
            tamper(r, "| 거부(오류 코드 EMPTY) |", "| 거부 |")
            dirty = default_gate(r)
            case("G27 checks 키를 생략한 기본 실행에서도 변조 블록은 [일괄] 위반 · 정상 블록은 [일괄] 없음",
                 not tagged(clean) and tagged(dirty) and not any(b.startswith("예외") for b in clean + dirty),
                 f"clean={clean[:3]} dirty={dirty[:3]}")

            for cid, old, new in (("G28a", ' · 승인 원문: "OK"', ""),
                                  ("G28b", "결정자: 사람, 2026-10-30", "결정자: 사람, 2026/10/30")):
                r = expanded(cid.lower())
                tamper(r, old, new)
                g_bad(cid, "머리 줄 " + ("승인 원문 없음" if cid == "G28a" else "날짜 형식 틀림"), r)
            r = expanded("g28c")
            hl = next(l for l in dec(r).splitlines() if l.startswith("패키지:"))
            tamper(r, hl + "\n", "")
            g_bad("G28c", "머리 줄 자체가 없음", r)

            # 마일스톤 검토 A01: Q 가 Q숫자 아니고 ID 가 굵지 않은 추가 행도 검사 대상
            r = expanded("g29")
            line = next(l for l in dec(r).splitlines() if l.startswith("| **D-14** |"))
            tamper(r, line, line + "\n| D-99 | X1 | 아무 질문 | 임의 내용 | 대안 | 근거 | 아니오 | t |")
            g_bad("G29", "패키지에 없는 행(ID 굵지 않음 · Q 가 X1)을 끼워 넣음", r)
            # 마일스톤 검토 A03: 해시는 CRLF 만 정규화 — 단독 CR · 잘못된 UTF-8 은 다른 판
            r = expanded("g30")
            pk = r / "reports" / "clarify" / "B-01.md"
            b = pk.read_bytes().replace(b"\r\n", b"\n")  # LF 판(CRLF 정규화라 해시 그대로)에서 LF 하나만 단독 CR 로
            pk.write_bytes(b)
            assert gate(r)[0], "LF 판은 통과해야 함"
            i = b.index(b"\n| Q2 |")
            pk.write_bytes(b[:i] + b"\r" + b[i + 1:])
            g_bad("G30", "패키지의 LF 하나를 단독 CR 로 바꿈(해시 · 기록은 그대로)", r)
            r = expanded("g31")
            pk = r / "reports" / "clarify" / "B-01.md"
            pk.write_bytes(pk.read_bytes().replace("근거".encode("utf-8"), b"\xff\xfe", 1))
            g_bad("G31", "패키지에 잘못된 UTF-8 바이트", r)
            r = expanded("g33")
            line = next(l for l in dec(r).splitlines() if l.startswith("| **D-14** |"))
            tamper(r, line, line + "\n| ID | X1 | 임의 질문 | 임의 결정 | 대안 | 근거 | 아니오 | t |")
            g_bad("G33", "첫 칸만 'ID' 인 가짜 머리 행에 임의 결정(재확인 A01)", r)
            r = expanded("g34")
            pk = r / "reports" / "clarify" / "B-01.md"
            b = pk.read_bytes().replace(b"\r\n", b"\n")
            pk.write_bytes(b)
            i = b.index(b"\n| Q2 |")
            pk.write_bytes(b[:i] + b"\r\r\n" + b[i + 1:])
            g_bad("G34", "LF 하나를 \\r\\r\\n 으로 — 두 번 정규화하면 같은 해시(재확인 A03)", r)
            r = project("g32", pkg=PKG.replace("빈 항목은 목록에서 구분 불가", "빈 항목은 \ufffd 로 표시됨"))
            p32, _ = expand(r)
            assert p32.returncode == 0, p32.stderr
            pk = r / "reports" / "clarify" / "B-01.md"
            pk.write_bytes(pk.read_bytes().replace("\ufffd".encode("utf-8"), b"\xff", 1))
            g_bad("G32", "원래 '\ufffd' 문자 자리를 잘못된 바이트로 바꿈(대체 문자로 읽으면 같은 해시)", r)
        except AssertionError as e:
            case("G00b 보강 게이트 사례 준비 실패", False, str(e)[-300:])

        # 번호: 최댓값 · 주석 · 코드 블록
        r = project("e15", existing="# 결정\n\n| ID | 결정 |\n|:---|:---|\n| **D-90** | 큰 번호 |\n\n## D-11 나중 줄\n")
        p, j = expand(r)
        case("E15 번호는 마지막이 아니라 가장 큰 D(D-90) 다음부터", p.returncode == 0
             and j.get("decisions", {}).get("Q1") == "D-91", f"{j} {p.stderr[-200:]}")
        r = project("e16", existing=EXISTING + "\n<!--\n## D-900 주석 속\n-->\n\n```\n## D-901 코드 속\n```\n")
        p, j = expand(r)
        case("E16 주석 · 코드 블록 속 정의 형태 D-900 · D-901 은 번호 계산에서 뺌", p.returncode == 0
             and j.get("decisions", {}).get("Q1") == "D-12", f"{j}")

        # 바이트 보존 (성공 · 거부)
        crlf = EXISTING.replace("\n", "\r\n").encode("utf-8")
        r = project("e17")
        (r / "reports" / "decisions.md").write_bytes(crlf)
        p, _ = expand(r)
        case("E17 CRLF 결정 파일: 전개 뒤 기존 바이트가 그대로 앞에 남음", p.returncode == 0
             and (r / "reports" / "decisions.md").read_bytes().startswith(crlf), p.stderr[-200:])
        r = project("x17", pkg=PKG.replace("위험도: low", "위험도: high"))
        (r / "reports" / "decisions.md").write_bytes(crlf)
        p, _ = expand(r)
        case("X17 거부 경로: CRLF 결정 파일 바이트 전체 그대로", p.returncode == 2
             and (r / "reports" / "decisions.md").read_bytes() == crlf, f"rc={p.returncode}")

        r = project("x18")
        out = r.parent / "outside"
        out.mkdir(exist_ok=True)
        (out / "B-01.md").write_text(PKG, encoding="utf-8")
        before = dec(r)
        p, _ = expand(r, pkg="../outside/B-01.md")
        case("X18 실제로 있는 저장소 밖 패키지도 거부 · 결정 파일 그대로", p.returncode == 2 and dec(r) == before,
             f"rc={p.returncode}")
        r = project("x19")
        before = dec(r)
        p, _ = expand(r, pkg="reports/clarify/B-09.md")
        case("X19 저장소 안이지만 없는 패키지 → 종료 2", p.returncode == 2 and dec(r) == before, f"rc={p.returncode}")
        r = project("x20")
        before = dec(r)
        p, _ = expand(r, "--skip", "Q99")
        case("X20 --skip 의 Q 가 패키지에 없음 → 종료 2", p.returncode == 2 and dec(r) == before, f"rc={p.returncode}")
        r = project("x21")
        (r / "reports" / "clarify" / "B-01.md").write_bytes(PKG.encode("utf-8").replace("근거".encode("utf-8"), b"\xff", 1))
        before = dec(r)
        p, _ = expand(r)
        case("X21 잘못된 UTF-8 패키지 → 종료 2(대체 문자로 읽지 않음, 마일스톤 검토 A03)",
             p.returncode == 2 and dec(r) == before, f"rc={p.returncode}")

        # 허용 입력 범위: medium · 슬라이스 생략 · B-100 · 다섯 질문 · 여러 alt/skip · 다른 결정 경로
        big = ("# 일괄 질문 B-100\n\n위험도: medium\n\n| Q | 질문 | 추천 | 근거 | 대안 |\n|:---|:---|:---|:---|:---|\n"
               + "".join(f"| Q{i} | 질문 {i} | 추천 {i} | 근거 {i} | 대안 {i} |\n" for i in range(1, 6)))
        r = project("e18", pkg=big, pkg_name="B-100", existing=None)
        (r / "docs").mkdir()
        p, j = expand(r, "--alt", "Q2", "--alt", "Q4", "--skip", "Q3", "--skip", "Q5", "--decisions", "docs/decisions.md",
                      pkg="reports/clarify/B-100.md")
        ok, bad = gate(r, extra_cfg={"decisions": ["docs/decisions.md"]})
        case("E18 medium · 슬라이스 생략 · B-100 · 5 질문 · alt Q2 Q4 · skip Q3 Q5 · docs/decisions.md → D-01~03, 게이트 통과",
             p.returncode == 0 and j.get("decisions") == {"Q1": "D-01", "Q2": "D-02", "Q4": "D-03"}
             and j.get("skipped") == ["Q3", "Q5"] and ok and (r / "docs" / "decisions.md").is_file(),
             f"rc={p.returncode} {j} {bad} {p.stderr[-200:]}")

        r = project("e19")
        p, _ = expand(r, approval="OK\n진행해")
        ok, bad = gate(r)
        case("E19 승인 원문의 줄바꿈은 공백 하나 — 머리 줄 한 줄 · 게이트 통과",
             p.returncode == 0 and '승인 원문: "OK 진행해"' in dec(r) and ok, f"{bad} {dec(r)[-300:]}")
    finally:
        shutil.rmtree(base, ignore_errors=True)

    bad_ = [x for x in results if not x[1]]
    for name, good, detail in results:
        print(f"{'OK ' if good else 'BAD'} {name}{('  — ' + detail) if detail and not good else ''}")
    print(f"\n{len(results) - len(bad_)}/{len(results)} 통과")
    return 1 if bad_ else 0


if __name__ == "__main__":
    sys.exit(main())
