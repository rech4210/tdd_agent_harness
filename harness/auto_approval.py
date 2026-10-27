"""자동 승인 유효성 판정 (WO-H80, HD-81 Q2).

D-## 정책과 additive 정책의 유효성을 단일 함수 is_valid_auto 로 판정한다.
"""

from __future__ import annotations

import re
from typing import Any

MUTATION_FIELDS = ("total", "killed", "survived", "timeout", "no_tests", "excluded")


def undetected(m: dict) -> int:
    """미검출 변이 수 = survived + no_tests - excluded"""
    return m["survived"] + m["no_tests"] - m["excluded"]


def is_valid_mutation_dict(m: Any) -> bool:
    """여섯 필드가 모두 int(bool 아님)인 유효한 변이 집계 dict 인지 확인한다."""
    if not isinstance(m, dict):
        return False
    return all(k in m and type(m[k]) is int for k in MUTATION_FIELDS)


def is_valid_auto(e: dict, risk: str | None) -> bool:
    """auto_approval 이벤트의 유효성을 판정한다."""
    if not isinstance(e, dict) or e.get("kind") != "auto_approval":
        return False
    if risk == "high":
        return False

    policy = e.get("policy")
    if policy == "additive":
        additive_kind = e.get("additive_kind")
        if additive_kind not in ("mutant-kill", "regression"):
            return False
        base_sha = e.get("base_sha")
        head_sha = e.get("head_sha")
        hex40 = r"[0-9a-f]{40}"
        if not (isinstance(base_sha, str) and re.fullmatch(hex40, base_sha)):
            return False
        if not (isinstance(head_sha, str) and re.fullmatch(hex40, head_sha)):
            return False
        if base_sha == head_sha:
            return False
        files = e.get("files")
        if not (isinstance(files, list) and len(files) > 0 and all(isinstance(f, str) and f for f in files)):
            return False
        review_note = e.get("review_note")
        if not (isinstance(review_note, str) and review_note.strip()):
            return False
        by = e.get("by")
        if not (isinstance(by, str) and by.strip()):
            return False

        if additive_kind == "mutant-kill":
            mut = e.get("mutation")
            if not isinstance(mut, dict):
                return False
            base_m = mut.get("base")
            head_m = mut.get("head")
            if not (is_valid_mutation_dict(base_m) and is_valid_mutation_dict(head_m)):
                return False
            if base_m["total"] != head_m["total"]:
                return False
            if undetected(head_m) >= undetected(base_m):
                return False

        return True

    # 기존 D-## 규칙
    if not (isinstance(policy, str) and re.fullmatch(r"D-\d+", policy)):
        return False
    if e.get("gt") != "pass":
        return False
    caught = e.get("lazy_caught")
    total = e.get("lazy_total")
    if not (type(caught) is int and type(total) is int and caught == total and caught > 0):
        return False
    res_defects = e.get("residual_defects")
    if not (type(res_defects) is int and res_defects == 0):
        return False
    btypes = e.get("base_types")
    if not (isinstance(btypes, list) and "unbounded" in btypes and "reject-preserve" in btypes):
        return False
    return True
