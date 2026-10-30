"""요구 추적성 평가기 (Core, SDD 통합 E1 · E3). 언어를 모른다.

명세(spec.md 등)의 요구 ID 와 테스트 모듈 머리말의 guards 를 양방향으로 대조한다.
머리말 형식: templates/test-skeletons/HEADER_CONVENTION.md (파일 첫 부분의 `키: 값` 줄. 주석 기호는 무시한다)

검사 (checks 로 고른다)
- header      : 모든 테스트 모듈에 guards · technique · checklist 줄이 있다
- id_format   : guards 의 각 항목이 요구 · 불변식 · 결정 ID 형식이다. 열린 질문(O-)은 쓸 수 없다
- undecided   : 테스트에 미결정 표시(기본 "[사람 결정")가 없다
- forward     : 범위 안의 모든 요구가 어느 테스트 모듈의 guards 에 있다 (deferred 에 이유와 함께 적은 것은 제외)
- backward    : guards 에 인용한 ID 가 명세 · 결정 기록에 정의되어 있다
- assumptions : 명세의 가정(Assumptions) 항목마다 결정(D-) 승격 또는 사람 구간(H) 표시가 있다 (E3)
- duplicate   : 명세 · 결정 기록에 같은 ID 가 두 번 이상 정의되지 않았다
- boundaries  : 경계 표의 마지막 칸(승격 근거)이 유효한 근거(D-## · INV-G# · 표본 · 피해 · 승인된 DEF-## · 계약 밖)이다
- defaults_scope : 명세 파일 안에서 공통 기본값(DEF-##)을 직접 정의하지 않았다 (docs/DEFAULTS.md · D-##)
- batch_integrity : 일괄 승인 패키지의 기계적 전개 블록이 패키지 원문 및 해시와 일치한다

모든 검사는 대상이 0개이면 실패한다 (공허한 참 방지).
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

try:
    from harness.decisions import norm_hash, parse_clarify_package, read_package_text
except ImportError:
    from decisions import norm_hash, parse_clarify_package, read_package_text

try:
    from harness.workspace import is_valid_exact_path
except ImportError:
    from workspace import is_valid_exact_path

HEADER_LINES = 60
# 일괄 승인 블록 표의 머리 행 — 정확히 같은 행만 건너뛴다 (첫 칸만 'ID' 인 행으로 결정을 끼워 넣지 못하게)
BATCH_HEADER = ["ID", "Q", "질문", "결정", "고려한 대안", "근거", "추천과 다름", "지키는 테스트"]
KEY = re.compile(r"^[\s#/*\"'`;!-]*(guards|technique|checklist)\s*:\s*(.*)$", re.IGNORECASE)
REQUIRED_KEYS = ("guards", "technique", "checklist")
DEFAULT_IDS = {
    "requirement": r"FR-\d{3}",
    "invariant": r"INV-\d+",
    "decision": r"D-\d+",
    "open": r"O-\d+",
    "product_invariant": r"INV-G\d+",
}
DEFAULT_ASSUMPTIONS_HEADING = r"^#+\s*(Assumptions|가정)\b"
DEFAULT_RESOLVED = r"(?:→|->)\s*(D-\d+|H\d+)\b"
DEFAULT_CLARIFICATIONS_HEADING = r"^#+\s*(Clarifications|명확화)\b"
DEFAULT_CLARIFICATION_MARKER = "[NEEDS CLARIFICATION"
DEFAULT_DECISION_DEF = (
    r"^\s*(?:#+\s*\*{0,2}(D-\d+)\*{0,2}(?=\s|:|\.|$|[^\w-])|\|\s*\*{0,2}(D-\d+)\*{0,2}\s*\|)"
)
DEFAULT_REQUIREMENT_DEF = (
    r"^\s*(?:(?:[-*+]|\d+\.)\s+\*{0,2}(FR-\d{3}|INV-\d+)\*{0,2}(?=\s|:|\.|$|[^\w-])|\|\s*\*{0,2}(FR-\d{3}|INV-\d+)\*{0,2}\s*\|)"
)
DEFAULT_DEFAULTS_DEF = (
    r"^\s*(?:#+\s*\*{0,2}(DEF-\d+)\*{0,2}(?=\s|:|\.|$|[^\w-])|\|\s*\*{0,2}(DEF-\d+)\*{0,2}\s*\|)"
)
DEFAULT_PRODUCT_INVARIANT_DEF = (
    r"^\s*(?:#+\s*\*{0,2}(INV-G\d+)\*{0,2}(?=\s|:|\.|$|[^\w-])|\|\s*\*{0,2}(INV-G\d+)\*{0,2}\s*\|)"
)


def _glob(root: Path, patterns: list[str]) -> list[Path]:
    out: set[Path] = set()
    for pattern in patterns:
        out |= {p for p in root.glob(pattern) if p.is_file()}
    return sorted(out)


def module_header(path: Path) -> dict[str, str]:
    head: dict[str, str] = {}
    with path.open(encoding="utf-8", errors="replace") as f:
        for _, line in zip(range(HEADER_LINES), f):
            m = KEY.match(line.rstrip())
            if m and m.group(1).lower() not in head:
                head[m.group(1).lower()] = m.group(2).strip().rstrip("\"'`*/ ").strip()
    return head


def _ids(text: str, pattern: str) -> set[str]:
    return set(re.findall(rf"\b{pattern}\b", text))


def assumption_items(text: str, heading: str) -> list[tuple[int, str]]:
    """가정 · 명확화 절의 목록 항목 (줄 번호, 내용). 절은 같은 수준 이상의 다음 제목에서 끝난다."""
    items: list[tuple[int, str]] = []
    inside, level = False, 0
    current_head_n: int | None = None
    current_parts: list[str] = []

    def flush() -> None:
        nonlocal current_head_n, current_parts
        if current_head_n is not None:
            items.append((current_head_n, " ".join(p.strip() for p in current_parts)))
            current_head_n = None
            current_parts = []

    for n, line in enumerate(text.splitlines(), 1):
        h = re.match(r"^(#+)\s", line)
        if re.match(heading, line, re.IGNORECASE):
            flush()
            inside, level = True, len(h.group(1)) if h else 1
            continue
        if inside and h and len(h.group(1)) <= level:
            flush()
            inside = False
        if not inside:
            continue
        is_list_head = bool(re.match(r"^\s*([-*+]|\d+\.)\s+\S", line))
        if is_list_head:
            flush()
            current_head_n = n
            current_parts = [line]
        elif current_head_n is not None:
            stripped = line.strip()
            if not stripped or h or not (line and line[0].isspace()):
                flush()
            else:
                current_parts.append(line)
    flush()
    return items


def strip_code_blocks(text: str) -> tuple[str, int | None]:
    """펜스 코드 블록(``` 또는 ~~~) 안과 펜스 줄을 빈 줄로 치환하여 줄 수를 보존한다."""
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    fence_char: str | None = None
    fence_len = 0
    fence_open_line: int | None = None
    for n, line in enumerate(lines, 1):
        nl = "\r\n" if line.endswith("\r\n") else ("\n" if line.endswith("\n") else "")
        if fence_char is None:
            m = re.match(r"^[ ]{0,3}(`{3,}|~{3,})(.*)$", line)
            if m:
                char = m.group(1)[0]
                rest = m.group(2)
                if char not in rest:
                    fence_char = char
                    fence_len = len(m.group(1))
                    fence_open_line = n
                    out.append(nl)
                    continue
            out.append(line)
        else:
            m = re.match(rf"^[ ]{{0,3}}\{fence_char}{{{fence_len},}}\s*$", line)
            out.append(nl)
            if m:
                fence_char = None
                fence_len = 0
                fence_open_line = None
    return "".join(out), fence_open_line


def strip_html_comments(text: str) -> str:
    """HTML 주석(<!-- ... -->)을 제거하되 줄바꿈 수는 유지하여 원래 파일 줄 번호를 보존한다."""
    return re.sub(r"<!--.*?-->", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.DOTALL)


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


def check_traceability(cfg: dict, root: Path) -> tuple[bool, list[str], list[str]]:
    """반환: (통과 여부, 위반 목록, 참고 목록)"""
    checks = cfg.get(
        "checks",
        [
            "header",
            "id_format",
            "undecided",
            "forward",
            "backward",
            "assumptions",
            "clarifications",
            "needs_clarification",
            "duplicate",
            "boundaries",
            "defaults_scope",
            "batch_integrity",
        ],
    )
    ids = {**DEFAULT_IDS, **cfg.get("id_patterns", {})}
    spec_files = _glob(root, cfg.get("requirements", []))
    decision_files = _glob(root, cfg.get("decisions", []))
    modules = _glob(root, cfg.get("modules", []))
    bad: list[str] = []

    if not modules:
        return False, ["[추적] 테스트 모듈이 없다 (공허한 참 방지)"], []
    if not spec_files and any(
        c in checks for c in ("forward", "backward", "assumptions", "clarifications", "needs_clarification", "duplicate", "defaults_scope")
    ):
        return False, [f"[추적] 명세 파일이 없다: {cfg.get('requirements', [])} (공허한 참 방지)"], []

    spec_text: dict[Path, str] = {}
    for p in spec_files:
        rel = p.relative_to(root).as_posix()
        s_clean, unclosed = strip_code_blocks(strip_html_comments(p.read_text(encoding="utf-8", errors="replace")))
        spec_text[p] = s_clean
        if unclosed is not None:
            bad.append(f"[형식] {rel}:{unclosed} 닫히지 않은 코드 블록")
    req_def_pattern = re.compile(cfg.get("requirement_definition", DEFAULT_REQUIREMENT_DEF))
    defined_req: set[str] = set()
    defined_inv: set[str] = set()
    file_defs: dict[Path, dict[str, list[str]]] = {}
    req_by_feature: dict[str, set[str]] = {}
    inv_by_feature: dict[str, set[str]] = {}
    features_defining_id: dict[str, set[str]] = {}
    all_features: set[str] = {p.parent.name for p in spec_files}

    for p in spec_files:
        rel = p.relative_to(root).as_posix()
        feature = p.parent.name
        f_defs = file_defs.setdefault(p, {})
        req_set = req_by_feature.setdefault(feature, set())
        inv_set = inv_by_feature.setdefault(feature, set())
        text = spec_text[p]
        for n, line in enumerate(text.splitlines(), 1):
            m = req_def_pattern.search(line)
            if m:
                item_id = m.group(m.lastindex) if m.lastindex else m.group(0)
                if item_id:
                    item_id = item_id.strip("* ")
                    if re.fullmatch(ids["requirement"], item_id):
                        defined_req.add(item_id)
                        req_set.add(item_id)
                        features_defining_id.setdefault(item_id, set()).add(feature)
                        f_defs.setdefault(item_id, []).append(f"{rel}:{n}")
                    elif re.fullmatch(ids["invariant"], item_id):
                        defined_inv.add(item_id)
                        inv_set.add(item_id)
                        features_defining_id.setdefault(item_id, set()).add(feature)
                        f_defs.setdefault(item_id, []).append(f"{rel}:{n}")

    dec_def_pattern = re.compile(cfg.get("decision_definition", DEFAULT_DECISION_DEF))
    defined_dec: set[str] = set()
    dec_defs: dict[str, list[str]] = {}
    for p in decision_files:
        rel = p.relative_to(root).as_posix()
        d_text, unclosed = strip_code_blocks(strip_html_comments(p.read_text(encoding="utf-8", errors="replace")))
        if unclosed is not None:
            bad.append(f"[형식] {rel}:{unclosed} 닫히지 않은 코드 블록")
        for n, line in enumerate(d_text.splitlines(), 1):
            m = dec_def_pattern.search(line)
            if m:
                dec_id = m.group(m.lastindex) if m.lastindex else m.group(0)
                if dec_id:
                    dec_id = dec_id.strip("* ")
                    if re.fullmatch(ids["decision"], dec_id):
                        defined_dec.add(dec_id)
                        dec_defs.setdefault(dec_id, []).append(f"{rel}:{n}")

    default_files = _glob(root, cfg.get("defaults", []))
    def_records: dict[str, list[bool]] = {}
    title_def_pattern = re.compile(r"^\s*#+\s*\*{0,2}(DEF-\d+)\*{0,2}(?=\s|:|\.|$)")
    for p in default_files:
        rel = p.relative_to(root).as_posix()
        d_text, unclosed = strip_code_blocks(strip_html_comments(p.read_text(encoding="utf-8", errors="replace")))
        if unclosed is not None:
            bad.append(f"[형식] {rel}:{unclosed} 닫히지 않은 코드 블록")
        for line in d_text.splitlines():
            s = line.strip()
            if s.startswith("|"):
                raw_cells = _split_table_row(s)
                cells = [c.strip("*").strip() for c in raw_cells]
                if not cells:
                    continue
                first_cell = cells[0]
                if re.fullmatch(r"DEF-\d+", first_cell):
                    rule = cells[1] if len(cells) > 1 else ""
                    if not rule or re.fullmatch(r"\(.*\)", rule):
                        continue
                    # 승인 칸은 원문이 정확히 D-## 또는 **D-##** — 별표를 모두 벗기면 'D-01*' 같은 칸도 통과한다 (마일스톤 검토 A02)
                    raw_last = raw_cells[-1].strip()
                    m_bold = re.fullmatch(r"\*\*(.+)\*\*", raw_last)
                    last_cell = m_bold.group(1) if m_bold else raw_last
                    is_approved = (
                        len(cells) >= 3
                        and bool(re.fullmatch(ids["decision"], last_cell))
                        and last_cell in defined_dec
                    )
                    def_records.setdefault(first_cell, []).append(is_approved)
            elif s.startswith("#"):
                m = title_def_pattern.match(line)
                if m:
                    def_id = m.group(1)
                    def_records.setdefault(def_id, []).append(False)

    defined_def: set[str] = set(def_records.keys())
    approved_def: set[str] = {def_id for def_id, approvals in def_records.items() if approvals and all(approvals)}

    product_files = _glob(root, cfg.get("product_invariants", []))
    defined_pi: set[str] = set()
    pi_pat = ids["product_invariant"]
    if "product_invariant_definition" in cfg:
        pi_def_str = cfg["product_invariant_definition"]
    else:
        pi_def_str = rf"^\s*(?:#+\s*\*{{0,2}}({pi_pat})\*{{0,2}}(?=\s|:|\.|$|[^\w-])|\|\s*\*{{0,2}}({pi_pat})\*{{0,2}}\s*\|)"
    pi_def_pattern = re.compile(pi_def_str)
    for p in product_files:
        rel = p.relative_to(root).as_posix()
        p_text, unclosed = strip_code_blocks(strip_html_comments(p.read_text(encoding="utf-8", errors="replace")))
        if unclosed is not None:
            bad.append(f"[형식] {rel}:{unclosed} 닫히지 않은 코드 블록")
        for line in p_text.splitlines():
            m = pi_def_pattern.search(line)
            if m:
                pi_id = m.group(m.lastindex) if m.lastindex else m.group(0)
                if pi_id:
                    pi_id = pi_id.strip("* ")
                    if re.fullmatch(pi_pat, pi_id):
                        defined_pi.add(pi_id)

    if "duplicate" in checks:
        for p, f_defs in file_defs.items():
            for item_id, locs in sorted(f_defs.items()):
                if len(locs) > 1:
                    bad.append(f"[중복 정의] {item_id}: {', '.join(locs)}")
        for dec_id, locs in sorted(dec_defs.items()):
            if len(locs) > 1:
                bad.append(f"[중복 정의] {dec_id}: {', '.join(locs)}")

    multi_spec = len(spec_files) > 1
    guarded: set[str] = set()
    item_ok = re.compile(rf"^({ids['requirement']}|{ids['invariant']}|{ids['decision']})$")
    feat_name_re = re.compile(r"^[A-Za-z0-9_.-]+$")

    for path in modules:
        rel = path.relative_to(root).as_posix()
        head = module_header(path)
        if "header" in checks:
            missing = [k for k in REQUIRED_KEYS if not head.get(k)]
            if missing:
                bad.append(f"[머리말] {rel}: {', '.join(missing)} 없음")
        raw_items = [x.strip() for x in head.get("guards", "").split(",") if x.strip()]
        for raw_item in raw_items:
            if ":" in raw_item:
                parts = raw_item.split(":")
                if len(parts) != 2:
                    if "id_format" in checks:
                        bad.append(f"[ID] {rel}: 형식이 아닌 guards 항목 '{raw_item}'")
                    continue
                feat_part, bare_id = parts[0].strip(), parts[1].strip()
                if "id_format" in checks:
                    if re.fullmatch(ids["open"], bare_id):
                        bad.append(f"[ID] {rel}: 열린 질문 {raw_item} 을 지킬 수 없다 (결정이 먼저다)")
                    elif not (feat_name_re.fullmatch(feat_part) and (re.fullmatch(ids["requirement"], bare_id) or re.fullmatch(ids["invariant"], bare_id))):
                        bad.append(f"[ID] {rel}: 형식이 아닌 guards 항목 '{raw_item}'")

                if "backward" in checks:
                    if feat_part not in all_features:
                        bad.append(f"[역방향] {raw_item}: 명세에 없는 요구 ({rel})")
                    else:
                        is_req = bool(re.fullmatch(ids["requirement"], bare_id))
                        is_inv = bool(re.fullmatch(ids["invariant"], bare_id))
                        if is_req:
                            if bare_id not in req_by_feature.get(feat_part, set()):
                                bad.append(f"[역방향] {raw_item}: 명세에 없는 요구 ({rel})")
                            else:
                                guarded.add(raw_item)
                                guarded.add(bare_id)
                        elif is_inv:
                            if bare_id not in inv_by_feature.get(feat_part, set()):
                                bad.append(f"[역방향] {raw_item}: 명세에 없는 불변식 ({rel})")
                            else:
                                guarded.add(raw_item)
                                guarded.add(bare_id)
                        else:
                            bad.append(f"[역방향] {raw_item}: 명세에 없는 항목 ({rel})")
                else:
                    guarded.add(raw_item)
                    guarded.add(bare_id)

            else:
                bare_id = raw_item
                if "id_format" in checks:
                    if re.fullmatch(ids["open"], bare_id):
                        bad.append(f"[ID] {rel}: 열린 질문 {bare_id} 을 지킬 수 없다 (결정이 먼저다)")
                    elif not item_ok.match(bare_id):
                        bad.append(f"[ID] {rel}: 형식이 아닌 guards 항목 '{bare_id}'")

                is_dec = bool(re.fullmatch(ids["decision"], bare_id))
                is_req = bool(re.fullmatch(ids["requirement"], bare_id))
                is_inv = bool(re.fullmatch(ids["invariant"], bare_id))

                if is_dec:
                    if "backward" in checks and bare_id not in defined_dec:
                        bad.append(f"[역방향] {bare_id}: 결정 기록에 없는 결정 ({rel})")
                    else:
                        guarded.add(bare_id)
                elif is_req or is_inv:
                    defining_feats = features_defining_id.get(bare_id, set())
                    if len(defining_feats) == 0:
                        if "backward" in checks:
                            if is_req:
                                bad.append(f"[역방향] {bare_id}: 명세에 없는 요구 ({rel})")
                            else:
                                bad.append(f"[역방향] {bare_id}: 명세에 없는 불변식 ({rel})")
                    elif len(defining_feats) > 1:
                        bad.append(f"[모호] {rel}: '{bare_id}' 가 여러 명세에 있다 — <기능 폴더>:{bare_id} 로 한정")
                    else:
                        feat = next(iter(defining_feats))
                        guarded.add(f"{feat}:{bare_id}")
                        guarded.add(bare_id)
                else:
                    if "backward" in checks and not is_dec:
                        bad.append(f"[역방향] {bare_id}: 명세에 없는 요구 ({rel})")

        if "undecided" in checks:
            marker = cfg.get("undecided_marker", "[사람 결정")
            text = path.read_text(encoding="utf-8", errors="replace")
            for n, line in enumerate(text.splitlines(), 1):
                if marker in line:
                    bad.append(f"[미결정] {rel}:{n} {line.strip()[:80]}")

    if "forward" in checks:
        scope = cfg.get("in_scope")
        deferred = cfg.get("deferred", {})
        if multi_spec:
            all_req_qualified = {
                f"{feat}:{req}"
                for feat, reqs in req_by_feature.items()
                for req in reqs
            }
            if scope is not None:
                targets = set()
                for s in scope:
                    if ":" in s:
                        targets.add(s)
                    else:
                        feats = features_defining_id.get(s, set())
                        for f in feats:
                            targets.add(f"{f}:{s}")
            else:
                targets = all_req_qualified
        else:
            targets = set(scope) if scope is not None else defined_req

        if not targets:
            bad.append("[정방향] 범위 안의 요구가 0개다 (공허한 참 방지)")
        if scope is None and deferred:
            for req, why in sorted(deferred.items()):
                bad.append(f"[미룸] 전체 게이트에서는 미룬 요구를 허용하지 않는다: {req} ({why})")
        for req in sorted(targets):
            bare = req.split(":", 1)[1] if ":" in req else req
            if req in deferred or bare in deferred:
                continue
            if multi_spec:
                if req not in guarded:
                    bad.append(f"[정방향] {req}: 지키는 테스트 모듈이 없다")
            else:
                if req not in guarded and bare not in guarded:
                    bad.append(f"[정방향] {req}: 지키는 테스트 모듈이 없다")
        for req in sorted(set(deferred) - (targets if not multi_spec else {t.split(':', 1)[1] if ':' in t else t for t in targets})):
            bad.append(f"[정방향] deferred 의 {req} 가 명세에 없다")

    resolved_pattern = re.compile(cfg.get("assumption_resolved", DEFAULT_RESOLVED))

    if "assumptions" in checks:
        heading = cfg.get("assumptions_heading", DEFAULT_ASSUMPTIONS_HEADING)
        for path, text in spec_text.items():
            rel = path.relative_to(root).as_posix()
            for n, item in assumption_items(text, heading):
                matches = list(resolved_pattern.finditer(item))
                if not matches:
                    bad.append(f"[가정] {rel}:{n} 결정(D-) 승격 또는 사람 구간(H) 표시 없음: {item[:70]}")
                else:
                    for m in matches:
                        resolved_id = m.group(1)
                        if re.fullmatch(ids["decision"], resolved_id) and resolved_id not in defined_dec:
                            bad.append(f"[가정] {rel}:{n} 결정 기록에 없는 {resolved_id}: {item[:70]}")

    if "clarifications" in checks:
        c_heading = cfg.get("clarifications_heading", DEFAULT_CLARIFICATIONS_HEADING)
        for path, text in spec_text.items():
            rel = path.relative_to(root).as_posix()
            for n, item in assumption_items(text, c_heading):
                matches = list(resolved_pattern.finditer(item))
                if not matches:
                    bad.append(f"[명확화] {rel}:{n} 결정(D-) 또는 사람 구간(H) 표시 없음: {item[:70]}")
                else:
                    for m in matches:
                        resolved_id = m.group(1)
                        if re.fullmatch(ids["decision"], resolved_id) and resolved_id not in defined_dec:
                            bad.append(f"[명확화] {rel}:{n} 결정 기록에 없는 {resolved_id}: {item[:70]}")

    if "needs_clarification" in checks:
        marker = cfg.get("clarification_marker", DEFAULT_CLARIFICATION_MARKER)
        norm_marker = unicodedata.normalize("NFKC", marker).casefold()
        scope = cfg.get("in_scope")
        for path, text in spec_text.items():
            rel = path.relative_to(root).as_posix()
            for n, line in enumerate(text.splitlines(), 1):
                clean_line = re.sub(r"(`+).*?\1", "", line)
                norm_line = unicodedata.normalize("NFKC", clean_line).casefold()
                if norm_marker in norm_line:
                    if scope is not None:
                        scope_bare = {s.split(":", 1)[1] if ":" in s else s for s in scope}
                        line_reqs = set(re.findall(rf"\b{ids['requirement']}\b", line))
                        if line_reqs and line_reqs.isdisjoint(scope_bare):
                            continue
                    bad.append(f"[명확화 필요] {rel}:{n} {line.strip()[:80]}")

    boundary_notes: list[str] = []
    if "boundaries" in checks:
        heading_re = re.compile(r"^#+\s*(?:Boundary\s+tables|경계)", re.IGNORECASE)
        for p in spec_files:
            rel = p.relative_to(root).as_posix()
            spec_dir = p.parent
            samples_dir = (spec_dir / "samples").resolve()
            text = spec_text[p]

            in_section = False
            section_level = 0
            in_count = 0
            out_count = 0
            has_boundary_table = False

            table_lines: list[tuple[int, str]] = []

            def _is_delim_row(cells: list[str], raw_line: str) -> bool:
                return "|" in raw_line and len(cells) > 0 and all(bool(re.fullmatch(r":?-+:?", c)) for c in cells)

            def process_table(rows: list[tuple[int, str]]) -> None:
                nonlocal in_count, out_count, has_boundary_table
                i = 0
                while i < len(rows):
                    d_idx = -1
                    for j in range(i + 1, len(rows)):
                        cells = _split_table_row(rows[j][1])
                        if _is_delim_row(cells, rows[j][1]):
                            d_idx = j
                            break
                    if d_idx == -1:
                        break

                    header_lineno, header_line = rows[d_idx - 1]
                    header_cells = _split_table_row(header_line)
                    header_col_count = len(header_cells)
                    target_col = header_col_count - 1

                    has_boundary_table = True

                    r_idx = d_idx + 1
                    while r_idx < len(rows):
                        if "|" not in rows[r_idx][1]:
                            break
                        if r_idx + 1 < len(rows) and _is_delim_row(_split_table_row(rows[r_idx + 1][1]), rows[r_idx + 1][1]):
                            break
                        lineno, line_str = rows[r_idx]
                        r_idx += 1
                        cells = _split_table_row(line_str)
                        if len(cells) != header_col_count:
                            bad.append(f"[경계] {rel}:{lineno} 칸 수가 머리와 다르다")
                            continue

                        last_cell = cells[target_col]

                        if "[결정 대기]" in last_cell:
                            bad.append(f"[경계] {rel}:{lineno} 승격 근거 올바르지 않음: '{last_cell}'")
                            continue

                        # ① D-##
                        if re.fullmatch(ids["decision"], last_cell):
                            if last_cell in defined_dec:
                                in_count += 1
                            else:
                                bad.append(f"[경계] {rel}:{lineno} 결정 기록에 정의되지 않은 결정: '{last_cell}'")
                            continue

                        # ② 표본:<경로>
                        if last_cell.startswith("표본:"):
                            sample_rel = last_cell[len("표본:"):].strip()
                            sample_target = (spec_dir / sample_rel).resolve()
                            is_in_samples = False
                            try:
                                is_in_samples = (
                                    sample_target.is_relative_to(samples_dir)
                                    and sample_target != samples_dir
                                )
                            except Exception:
                                is_in_samples = False

                            is_derived = False
                            if is_in_samples:
                                try:
                                    rel_parts = sample_target.relative_to(samples_dir).parts
                                    is_derived = any(part.lower().startswith("derived") for part in rel_parts)
                                except Exception:
                                    is_derived = False

                            if is_in_samples and sample_target.is_file() and not is_derived:
                                in_count += 1
                            else:
                                bad.append(f"[경계] {rel}:{lineno} 유효하지 않은 표본: '{last_cell}'")
                            continue

                        # ③ 피해:<시나리오>
                        if last_cell.startswith("피해:"):
                            scenario = last_cell[len("피해:"):].strip()
                            if scenario:
                                in_count += 1
                            else:
                                bad.append(f"[경계] {rel}:{lineno} 피해 시나리오 비어 있음: '{last_cell}'")
                            continue

                        # ④ DEF-##
                        if re.fullmatch(r"DEF-\d+", last_cell):
                            if last_cell not in defined_def:
                                bad.append(f"[경계] {rel}:{lineno} 공통 기본값에 정의되지 않은 DEF: '{last_cell}'")
                            elif last_cell not in approved_def:
                                bad.append(f"[경계] {rel}:{lineno} 승인되지 않은 DEF: '{last_cell}' (승인 칸에 결정 D-## 필요)")
                            else:
                                in_count += 1
                            continue

                        # ⑤ 계약 밖
                        if last_cell == "계약 밖" or (last_cell.startswith("계약 밖:") and bool(last_cell[len("계약 밖:"):].strip())):
                            out_count += 1
                            continue

                        # ⑥ INV-G#
                        if re.fullmatch(ids["product_invariant"], last_cell):
                            if last_cell in defined_pi:
                                in_count += 1
                            else:
                                bad.append(f"[경계] {rel}:{lineno} 정의되지 않은 불변식: '{last_cell}'")
                            continue

                        # ⑦ D-## · INV-G# (쉼표 구분 복수)
                        if "," in last_cell:
                            raw_parts = last_cell.split(",")
                            items = [p.strip() for p in raw_parts]
                            if any(not p for p in items) or not all(
                                re.fullmatch(ids["decision"], p) or re.fullmatch(ids["product_invariant"], p)
                                for p in items
                            ):
                                bad.append(f"[경계] {rel}:{lineno} 승격 근거 올바르지 않음: '{last_cell}'")
                                continue
                            has_missing = False
                            for item in items:
                                if re.fullmatch(ids["decision"], item):
                                    if item not in defined_dec:
                                        bad.append(f"[경계] {rel}:{lineno} 결정 기록에 정의되지 않은 결정: '{item}'")
                                        has_missing = True
                                elif re.fullmatch(ids["product_invariant"], item):
                                    if item not in defined_pi:
                                        bad.append(f"[경계] {rel}:{lineno} 정의되지 않은 불변식: '{item}'")
                                        has_missing = True
                            if not has_missing:
                                in_count += 1
                            continue

                        bad.append(f"[경계] {rel}:{lineno} 승격 근거 올바르지 않음: '{last_cell}'")

                    i = r_idx

            for n, line in enumerate(text.splitlines(), 1):
                h = re.match(r"^(#+)\s", line)
                if h:
                    if table_lines:
                        process_table(table_lines)
                        table_lines = []
                    if heading_re.match(line):
                        in_section = True
                        section_level = len(h.group(1))
                        continue
                    elif in_section and len(h.group(1)) <= section_level:
                        in_section = False
                        continue
                if not in_section:
                    continue

                stripped = line.strip()
                if stripped:
                    table_lines.append((n, stripped))
                else:
                    if table_lines:
                        process_table(table_lines)
                        table_lines = []

            if table_lines:
                process_table(table_lines)
                table_lines = []

            if has_boundary_table:
                boundary_notes.append(f"(경계) {rel}: 계약 안 {in_count} · 계약 밖 {out_count}")

    if "defaults_scope" in checks:
        scope_patterns = (
            re.compile(r"^\s*(?:[-*+]|\d+\.)\s+\*{0,2}(DEF-\d+)\*{0,2}\s*:"),
            re.compile(r"^\s*#+\s*\*{0,2}(DEF-\d+)\*{0,2}(?=\s|:|\.|$)"),
            re.compile(r"^\s*\|\s*\*{0,2}(DEF-\d+)\*{0,2}\s*\|"),
        )
        for p, text in spec_text.items():
            rel = p.relative_to(root).as_posix()
            for n, line in enumerate(text.splitlines(), 1):
                for pat in scope_patterns:
                    m = pat.match(line)
                    if m:
                        def_id = m.group(1)
                        bad.append(
                            f"[기본값] {rel}:{n} 명세 안 DEF 정의 '{def_id}' — 공통 기본값은 docs/DEFAULTS.md, 슬라이스 기본값은 결정 기록 D-##"
                        )
                        break

    if "batch_integrity" in checks:
        batch_heading_re = re.compile(r"^##\s+일괄\s+승인\s+(B-\d{2,})\s*$")
        head_re = re.compile(
            r"^패키지:\s*`([^`]+)`\s*·\s*sha256\s*`([0-9a-fA-F]{64})`\s*·\s*승인\s*원문:\s*\"(.*)\"\s*·\s*결정자:\s*사람,\s*(\d{4}-\d{2}-\d{2})$"
        )
        seen_batches: dict[str, str] = {}

        for p in decision_files:
            rel = p.relative_to(root).as_posix()
            raw_text = p.read_text(encoding="utf-8", errors="replace")
            clean_text, _ = strip_code_blocks(strip_html_comments(raw_text))

            blocks: list[tuple[str, int, list[tuple[int, str]]]] = []
            cur_batch_id: str | None = None
            cur_start_line = 0
            cur_lines: list[tuple[int, str]] = []

            for n, line in enumerate(clean_text.splitlines(), 1):
                h2_match = re.match(r"^##\s+(.*)$", line)
                if h2_match:
                    if cur_batch_id is not None:
                        blocks.append((cur_batch_id, cur_start_line, cur_lines))
                        cur_batch_id = None
                        cur_lines = []
                    b_match = batch_heading_re.match(line)
                    if b_match:
                        cur_batch_id = b_match.group(1)
                        cur_start_line = n
                        cur_lines = []
                elif cur_batch_id is not None:
                    cur_lines.append((n, line))

            if cur_batch_id is not None:
                blocks.append((cur_batch_id, cur_start_line, cur_lines))

            for batch_id, start_line, b_lines in blocks:
                if batch_id in seen_batches:
                    bad.append(
                        f"[일괄] {rel}:{start_line} 중복된 일괄 승인 블록 '{batch_id}' (이전 정의: {seen_batches[batch_id]})"
                    )
                    continue
                seen_batches[batch_id] = f"{rel}:{start_line}"

                head_tuple: tuple[int, str] | None = None
                for ln, lstr in b_lines:
                    if lstr.strip().startswith("패키지:"):
                        head_tuple = (ln, lstr.strip())
                        break

                if head_tuple is None:
                    bad.append(f"[일괄] {rel}:{start_line} 머리 줄(패키지: ...)이 없습니다")
                    continue

                head_n, head_str = head_tuple
                m_head = head_re.match(head_str)
                if not m_head:
                    bad.append(
                        f"[일괄] {rel}:{head_n} 머리 줄 형식이 올바르지 않습니다: '{head_str}'"
                    )
                    continue

                pkg_rel = m_head.group(1)
                pkg_expected_hash = m_head.group(2)
                approval_text = m_head.group(3)
                decision_date = m_head.group(4)

                try:
                    import datetime
                    datetime.date.fromisoformat(decision_date)
                except ValueError:
                    bad.append(f"[일괄] {rel}:{head_n} 날짜 형식이 올바르지 않습니다: '{decision_date}'")
                    continue

                if not is_valid_exact_path(pkg_rel):
                    bad.append(f"[일괄] {rel}:{head_n} 패키지 경로가 저장소 안 상대 경로가 아닙니다: '{pkg_rel}'")
                    continue

                pkg_file = (root / pkg_rel).resolve()
                try:
                    if not pkg_file.is_relative_to(root.resolve()):
                        bad.append(f"[일괄] {rel}:{head_n} 패키지 경로가 저장소 밖에 있습니다: '{pkg_rel}'")
                        continue
                except (ValueError, AttributeError):
                    pass

                if not pkg_file.is_file():
                    bad.append(f"[일괄] {rel}:{head_n} 패키지 파일이 존재하지 않습니다: '{pkg_rel}'")
                    continue

                try:
                    pkg_text = read_package_text(pkg_file)
                except UnicodeDecodeError as e:
                    bad.append(f"[일괄] {rel}:{head_n} 패키지가 올바른 UTF-8 이 아닙니다: {e}")
                    continue
                actual_hash = norm_hash(pkg_text)
                if actual_hash.lower() != pkg_expected_hash.lower():
                    bad.append(
                        f"[일괄] {rel}:{head_n} 패키지 해시가 일치하지 않습니다 (기록: {pkg_expected_hash}, 실제: {actual_hash})"
                    )
                    continue

                pkg_stem = Path(pkg_rel).stem
                pkg, pkg_err = parse_clarify_package(pkg_text, pkg_stem)
                if pkg_err or pkg is None:
                    bad.append(f"[일괄] {rel}:{head_n} 패키지 파일 오류: {pkg_err}")
                    continue

                if pkg.batch_id != batch_id:
                    bad.append(
                        f"[일괄] {rel}:{start_line} 블록 일괄 ID('{batch_id}')와 패키지 ID('{pkg.batch_id}')가 다릅니다"
                    )
                    continue

                table_seen_q: set[str] = set()
                skip_seen_q: set[str] = set()

                for ln, lstr in b_lines:
                    s = lstr.strip()
                    if s.startswith("|"):
                        cells = _split_table_row(s)
                        if not cells:
                            continue
                        if cells == BATCH_HEADER or all(re.fullmatch(r":?-+:?", c) for c in cells):
                            continue

                        # 머리 · 구분선 밖의 표 행은 모두 일괄 결정 행이다 — 형식이 다른 행을 건너뛰면
                        # 패키지에 없는 결정을 끼워 넣을 수 있다 (마일스톤 검토 A01)
                        if not re.fullmatch(r"\*\*D-\d+\*\*", cells[0]):
                            bad.append(f"[일괄] {rel}:{ln} 행 ID가 **D-##** 형식이 아닙니다: '{cells[0]}'")
                            continue
                        if len(cells) != 8:
                            bad.append(f"[일괄] {rel}:{ln} 행의 칸 수가 8칸이 아닙니다: {len(cells)}칸")
                            continue

                        d_id = cells[0].strip("* ")
                        q_id = cells[1]
                        q_title = cells[2]
                        q_dec = cells[3]
                        q_alt = cells[4]
                        q_rat = cells[5]
                        q_diff = cells[6]
                        q_guard = cells[7]

                        if q_id not in pkg.questions_by_id:
                            bad.append(f"[일괄] {rel}:{ln} 패키지에 없는 질문 ID입니다: '{q_id}'")
                            continue

                        if q_id in table_seen_q:
                            bad.append(f"[일괄] {rel}:{ln} 중복된 질문 행입니다: '{q_id}'")
                            continue
                        table_seen_q.add(q_id)

                        orig_q = pkg.questions_by_id[q_id]
                        if q_title != orig_q.question:
                            bad.append(f"[일괄] {rel}:{ln} 질문 원문 불일치: '{q_title}' != '{orig_q.question}'")
                        if q_rat != orig_q.rationale:
                            bad.append(f"[일괄] {rel}:{ln} 근거 원문 불일치: '{q_rat}' != '{orig_q.rationale}'")

                        if q_diff == "아니오":
                            if q_dec != orig_q.recommendation:
                                bad.append(f"[일괄] {rel}:{ln} 결정 칸 불일치 (추천 채택): '{q_dec}' != '{orig_q.recommendation}'")
                            if q_alt != orig_q.alternative:
                                bad.append(f"[일괄] {rel}:{ln} 고려한 대안 칸 불일치 (추천 채택): '{q_alt}' != '{orig_q.alternative}'")
                        elif q_diff == "예":
                            if q_dec != orig_q.alternative:
                                bad.append(f"[일괄] {rel}:{ln} 결정 칸 불일치 (대안 채택): '{q_dec}' != '{orig_q.alternative}'")
                            if q_alt != orig_q.recommendation:
                                bad.append(f"[일괄] {rel}:{ln} 고려한 대안 칸 불일치 (대안 채택): '{q_alt}' != '{orig_q.recommendation}'")
                        else:
                            bad.append(f"[일괄] {rel}:{ln} '추천과 다름' 칸이 '아니오' 또는 '예'가 아닙니다: '{q_diff}'")

                    elif s.startswith("건너뜀:"):
                        rest = s[len("건너뜀:"):].strip()
                        if rest:
                            for part in rest.split(","):
                                sq = part.strip()
                                if not sq:
                                    continue
                                if sq not in pkg.questions_by_id:
                                    bad.append(f"[일괄] {rel}:{ln} 건너뜀의 질문 ID가 패키지에 없습니다: '{sq}'")
                                if sq in skip_seen_q:
                                    bad.append(f"[일괄] {rel}:{ln} 건너뜀 목록에 중복 질문: '{sq}'")
                                skip_seen_q.add(sq)

                overlap = table_seen_q & skip_seen_q
                if overlap:
                    bad.append(f"[일괄] {rel}:{start_line} 질문이 표와 건너뜀에 동시에 존재합니다: {sorted(overlap)}")

                union_q = table_seen_q | skip_seen_q
                expected_q = set(pkg.questions_by_id.keys())
                if union_q != expected_q:
                    missing = expected_q - union_q
                    extra = union_q - expected_q
                    bad.append(f"[일괄] {rel}:{start_line} 질문 목록 불일치 (누락: {sorted(missing)}, 초과: {sorted(extra)})")

    bad = sorted(set(bad))
    # 미뤄 둔 요구는 위반이 아니지만 보고서에 남긴다 (조용히 사라지지 않게)
    notes = boundary_notes + [f"(미룸) {req}: {why}" for req, why in sorted(cfg.get("deferred", {}).items())]
    return not bad, bad, notes
