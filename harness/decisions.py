"""결정 기록 관리 및 일괄 승인 전개 도구 (WO-H93, HD-83).

계약: dev/WO-H93.md §계약 2
- expand: clarify 옵트아웃 패키지를 읽어 결정 기록에 D-## 블록으로 기계적 전개
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import NamedTuple

try:
    from harness.workspace import is_valid_exact_path
except ImportError:
    from workspace import is_valid_exact_path


class DecisionsArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        sys.stderr.write(f"오류: {message}\n")
        sys.exit(2)


class ClarifyQuestion(NamedTuple):
    q_id: str
    question: str
    recommendation: str
    rationale: str
    alternative: str


class ClarifyPackage(NamedTuple):
    batch_id: str
    risk: str
    slice: str | None
    questions: list[ClarifyQuestion]
    questions_by_id: dict[str, ClarifyQuestion]
    raw_text: str


def _split_table_row(line_str: str) -> list[str]:
    s = line_str.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        bs = 0
        for ch in reversed(s[:-1]):
            if ch == "\\":
                bs += 1
            else:
                break
        if bs % 2 == 0:
            s = s[:-1]
    raw_cells = re.split(r"(?<!\\)\|", s)
    return [c.strip() for c in raw_cells]


def norm_hash(text: str) -> str:
    """read_package_text 로 읽은(CRLF 를 한 번 LF 로 바꾼) 텍스트의 sha256. 여기서 다시 정규화하지 않는다 —
    두 번 바꾸면 '\\r\\r\\n' 이 '\\n' 과 같은 해시가 된다 (재확인 A03)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_package_text(path: Path) -> str:
    """패키지를 바이트로 읽어 엄격한 UTF-8 로 풀고 CRLF 만 LF 로 바꾼다 — 단독 CR · 잘못된 바이트를 같은 판으로 보지 않게
    (마일스톤 검토 A03). 잘못된 UTF-8 은 UnicodeDecodeError."""
    return path.read_bytes().decode("utf-8").replace("\r\n", "\n")


def format_approval(approval: str) -> str:
    s = re.sub(r"\r\n|\r|\n", " ", approval)
    s = re.sub(r"(?<!\\)\|", lambda m: r"\|", s)
    s = re.sub(r'(?<!\\)"', lambda m: r'\"', s)
    return s


def parse_clarify_package(text: str, filename_stem: str | None = None) -> tuple[ClarifyPackage | None, str | None]:
    lines = text.splitlines()
    non_empty = [l.strip() for l in lines if l.strip()]
    if not non_empty:
        return None, "패키지 내용이 비어 있습니다"

    m_title = re.match(r"^#\s*일괄\s*질문\s+(B-\d{2,})\s*$", non_empty[0])
    if not m_title:
        return None, f"첫 줄 제목이 '# 일괄 질문 B-<번호>' 형식이 아닙니다: '{non_empty[0]}'"

    batch_id = m_title.group(1)
    if filename_stem is not None and batch_id != filename_stem:
        return None, f"제목 ID('{batch_id}')와 파일 이름('{filename_stem}')이 일치하지 않습니다"

    risk: str | None = None
    slice_val: str | None = None
    table_lines: list[str] = []
    in_table = False

    for line in lines:
        stripped = line.strip()
        if not stripped:
            if in_table:
                break
            continue
        if stripped.startswith("#"):
            continue
        if not in_table and stripped.startswith("|"):
            in_table = True
            table_lines.append(stripped)
            continue
        if in_table:
            if stripped.startswith("|"):
                table_lines.append(stripped)
            else:
                break
        else:
            m_risk = re.match(r"^위험도:\s*(\S+)\s*$", stripped)
            if m_risk:
                risk = m_risk.group(1)
                continue
            m_slice = re.match(r"^슬라이스:\s*(.*?)\s*$", stripped)
            if m_slice:
                slice_val = m_slice.group(1)
                continue

    if risk is None:
        return None, "위험도 줄이 없습니다"
    if risk not in ("low", "medium"):
        return None, f"위험도는 'low' 또는 'medium'이어야 합니다: '{risk}'"

    if len(table_lines) < 3:
        return None, "표 형식이 올바르지 않거나 데이터 행이 없습니다"

    header_cells = _split_table_row(table_lines[0])
    if header_cells != ["Q", "질문", "추천", "근거", "대안"]:
        return None, f"표 헤더가 일치하지 않습니다: {header_cells}"

    delim_cells = _split_table_row(table_lines[1])
    if len(delim_cells) != 5 or not all(re.fullmatch(r":?-+:?", c) for c in delim_cells):
        return None, "표 구분선이 올바르지 않습니다"

    questions: list[ClarifyQuestion] = []
    questions_by_id: dict[str, ClarifyQuestion] = {}

    for row_idx, row_str in enumerate(table_lines[2:], 1):
        cells = _split_table_row(row_str)
        if len(cells) != 5:
            return None, f"표 행의 칸 수가 5칸이 아닙니다 ({len(cells)}칸): '{row_str}'"
        if any(not c for c in cells):
            return None, f"표에 빈 칸이 있습니다: '{row_str}'"

        q_id, question, rec, rat, alt = cells
        expected_q = f"Q{row_idx}"
        if q_id != expected_q:
            return None, f"Q 번호가 누락되었거나 순서가 올바르지 않습니다 (기대: '{expected_q}', 실제: '{q_id}')"

        if re.search(r"\bH\d+\b", rec):
            return None, f"추천 칸에 사람 개입 구간 ID(H#)가 있습니다: '{rec}'"
        if re.search(r"\bH\d+\b", alt):
            return None, f"대안 칸에 사람 개입 구간 ID(H#)가 있습니다: '{alt}'"

        q_obj = ClarifyQuestion(q_id=q_id, question=question, recommendation=rec, rationale=rat, alternative=alt)
        questions.append(q_obj)
        questions_by_id[q_id] = q_obj

    if not questions:
        return None, "패키지에 질문이 없습니다"

    return (
        ClarifyPackage(
            batch_id=batch_id,
            risk=risk,
            slice=slice_val,
            questions=questions,
            questions_by_id=questions_by_id,
            raw_text=text,
        ),
        None,
    )


def generate_batch_block(
    pkg: ClarifyPackage,
    pkg_rel_path: str,
    raw_approval: str,
    date_str: str,
    alt_q_set: set[str],
    skip_q_set: set[str],
    decisions_map: dict[str, str],
) -> str:
    pkg_h = norm_hash(pkg.raw_text)
    app_esc = format_approval(raw_approval)
    lines = [
        f"## 일괄 승인 {pkg.batch_id}",
        "",
        f"패키지: `{pkg_rel_path}` · sha256 `{pkg_h}` · 승인 원문: \"{app_esc}\" · 결정자: 사람, {date_str}",
        "",
        "| ID | Q | 질문 | 결정 | 고려한 대안 | 근거 | 추천과 다름 | 지키는 테스트 |",
        "|:---|:---|:---|:---|:---|:---|:---|:---|",
    ]
    for q in pkg.questions:
        if q.q_id in skip_q_set:
            continue
        d_id = decisions_map[q.q_id]
        if q.q_id in alt_q_set:
            dec = q.alternative
            alt = q.recommendation
            diff = "예"
        else:
            dec = q.recommendation
            alt = q.alternative
            diff = "아니오"
        test_guard = f"(보강 예정: 일괄 {pkg.batch_id} {q.q_id})"
        lines.append(
            f"| **{d_id}** | {q.q_id} | {q.question} | {dec} | {alt} | {q.rationale} | {diff} | {test_guard} |"
        )

    skipped_questions = [q.q_id for q in pkg.questions if q.q_id in skip_q_set]
    if skipped_questions:
        lines.append("")
        lines.append(f"건너뜀: {', '.join(skipped_questions)}")

    lines.append("")
    return "\n".join(lines)


def find_max_decision_id(text: str) -> int:
    try:
        from harness.traceability import DEFAULT_DECISION_DEF, strip_code_blocks, strip_html_comments
    except ImportError:
        from traceability import DEFAULT_DECISION_DEF, strip_code_blocks, strip_html_comments

    clean_text, _ = strip_code_blocks(strip_html_comments(text))
    pat = re.compile(DEFAULT_DECISION_DEF)
    max_num = 0
    for line in clean_text.splitlines():
        m = pat.search(line)
        if m:
            item_id = m.group(m.lastindex) if m.lastindex else m.group(0)
            if item_id:
                item_id = item_id.strip("* ")
                m_num = re.fullmatch(r"D-(\d+)", item_id)
                if m_num:
                    num = int(m_num.group(1))
                    if num > max_num:
                        max_num = num
    return max_num


def flatten_args(items: list) -> list[str]:
    res: list[str] = []
    for x in items:
        if isinstance(x, list):
            res.extend(flatten_args(x))
        elif isinstance(x, str):
            res.append(x)
    return res


def cmd_expand(args: argparse.Namespace) -> int:
    if args.by != "user":
        sys.stderr.write("오류: --by 는 'user' 만 허용됩니다.\n")
        return 2

    if args.approval is None or not args.approval.strip():
        sys.stderr.write("오류: --approval 은 비어 있을 수 없습니다.\n")
        return 2

    if args.date is None:
        date_str = datetime.date.today().isoformat()
    else:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.date):
            sys.stderr.write(f"오류: 날짜 형식이 올바르지 않습니다: '{args.date}'\n")
            return 2
        try:
            datetime.date.fromisoformat(args.date)
            date_str = args.date
        except ValueError:
            sys.stderr.write(f"오류: 유효한 날짜가 아닙니다: '{args.date}'\n")
            return 2

    if not is_valid_exact_path(args.package):
        sys.stderr.write(f"오류: 패키지 경로가 유효한 상대 경로가 아닙니다: '{args.package}'\n")
        return 2

    root = Path(args.root).resolve()
    pkg_file = (root / args.package).resolve()
    try:
        if not pkg_file.is_relative_to(root):
            sys.stderr.write(f"오류: 패키지 경로가 저장소 밖에 있습니다: '{args.package}'\n")
            return 2
    except (ValueError, AttributeError):
        pass

    if not pkg_file.is_file():
        sys.stderr.write(f"오류: 패키지 파일을 찾을 수 없습니다: '{args.package}'\n")
        return 2

    try:
        pkg_text = read_package_text(pkg_file)
    except Exception as e:
        sys.stderr.write(f"오류: 패키지 파일을 읽을 수 없습니다: {e}\n")
        return 2

    pkg_stem = Path(args.package).stem
    pkg, err = parse_clarify_package(pkg_text, pkg_stem)
    if err or pkg is None:
        sys.stderr.write(f"오류: 패키지 형식 오류: {err}\n")
        return 2

    alt_list = flatten_args(args.alt)
    skip_list = flatten_args(args.skip)

    all_q_set = {q.q_id for q in pkg.questions}
    alt_set = set(alt_list)
    skip_set = set(skip_list)

    diff_alt = alt_set - all_q_set
    if diff_alt:
        sys.stderr.write(f"오류: --alt 의 질문이 패키지에 없습니다: {sorted(diff_alt)}\n")
        return 2

    diff_skip = skip_set - all_q_set
    if diff_skip:
        sys.stderr.write(f"오류: --skip 의 질문이 패키지에 없습니다: {sorted(diff_skip)}\n")
        return 2

    overlap = alt_set & skip_set
    if overlap:
        sys.stderr.write(f"오류: --alt 와 --skip 에 동일한 질문이 있습니다: {sorted(overlap)}\n")
        return 2

    if not is_valid_exact_path(args.decisions):
        sys.stderr.write(f"오류: 결정 파일 경로가 유효한 상대 경로가 아닙니다: '{args.decisions}'\n")
        return 2

    dec_file = (root / args.decisions).resolve()
    try:
        if not dec_file.is_relative_to(root):
            sys.stderr.write(f"오류: 결정 파일 경로가 저장소 밖에 있습니다: '{args.decisions}'\n")
            return 2
    except (ValueError, AttributeError):
        pass

    dec_raw_bytes = b""
    max_d = 0
    if dec_file.is_file():
        dec_raw_bytes = dec_file.read_bytes()
        dec_text = dec_raw_bytes.decode("utf-8", errors="replace")

        try:
            from harness.traceability import strip_code_blocks, strip_html_comments
        except ImportError:
            from traceability import strip_code_blocks, strip_html_comments

        clean_dec_text, _ = strip_code_blocks(strip_html_comments(dec_text))
        batch_header_re = re.compile(rf"^##\s+일괄\s+승인\s+{re.escape(pkg.batch_id)}\b", re.M)
        if batch_header_re.search(clean_dec_text):
            sys.stderr.write(f"오류: 결정 파일에 동일한 일괄 ID('{pkg.batch_id}') 블록이 이미 존재합니다.\n")
            return 2

        max_d = find_max_decision_id(dec_text)

    curr_d = max_d + 1
    decisions_map: dict[str, str] = {}
    for q in pkg.questions:
        if q.q_id in skip_set:
            continue
        d_str = f"D-{curr_d:02d}"
        decisions_map[q.q_id] = d_str
        curr_d += 1

    skipped_list = [q.q_id for q in pkg.questions if q.q_id in skip_set]

    block_str = generate_batch_block(
        pkg=pkg,
        pkg_rel_path=args.package,
        raw_approval=args.approval,
        date_str=date_str,
        alt_q_set=alt_set,
        skip_q_set=skip_set,
        decisions_map=decisions_map,
    )

    if dec_raw_bytes:
        sep = b""
        if not dec_raw_bytes.endswith(b"\n"):
            sep += b"\n"
        if not (dec_raw_bytes + sep).endswith(b"\n\n"):
            sep += b"\n"
        final_bytes = dec_raw_bytes + sep + block_str.encode("utf-8")
    else:
        final_bytes = block_str.encode("utf-8")

    dec_file.parent.mkdir(parents=True, exist_ok=True)
    dec_file.write_bytes(final_bytes)

    out_data = {
        "batch": pkg.batch_id,
        "decisions": decisions_map,
        "skipped": skipped_list,
    }
    sys.stdout.write(json.dumps(out_data, ensure_ascii=False) + "\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = DecisionsArgumentParser(prog="decisions")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    expand_parser = subparsers.add_parser("expand")
    expand_parser.add_argument("--package", required=True)
    expand_parser.add_argument("--approval", default=None)
    expand_parser.add_argument("--by", default=None)
    expand_parser.add_argument("--alt", nargs="+", action="append", default=[])
    expand_parser.add_argument("--skip", nargs="+", action="append", default=[])
    expand_parser.add_argument("--decisions", default="reports/decisions.md")
    expand_parser.add_argument("--date", default=None)
    expand_parser.add_argument("--root", default=".")

    args = parser.parse_args(argv)
    if args.subcommand == "expand":
        return cmd_expand(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
