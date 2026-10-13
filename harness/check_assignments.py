"""GA: 역할 배정 제약 검사 (HD-17, HD-18).

하네스는 '어떤 모델이 어떤 역할을 맡는가'를 정하지 않는다. 정하는 것은 관계뿐이다:
같은 슬라이스 안에서 roles.toml [separation] 의 두 역할은 다른 모델 계열이어야 한다.

검사 1. config/assignments.toml 의 모든 슬라이스가 분리 제약을 지키는가
검사 2. 배정에 쓴 에이전트와 역할이 등록되어 있는가 (agents.toml, roles.toml)
검사 3. work_orders/*.md 머리말의 agent 가 그 슬라이스 · 역할의 배정과 같은가
검사 4. work_orders/*.md 머리말의 family 가 agent 의 등록 계열과 같은가 (실제 계열 대조)
검사 5. config/delegation.toml 이 있을 때: 지시서의 job 위임 기록에서 실제 계열을 읽어 대조

사용:
  python harness/check_assignments.py
  python harness/check_assignments.py --assignments <다른 파일>   # 하네스 자체 시험용
종료 코드: 0 통과, 1 위반
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
from paths import AGENTS, ASSIGNMENTS, CONFIG, ROLES  # noqa: E402
WORK_ORDERS = ROOT / "work_orders"
DELEGATION = CONFIG / "delegation.toml"

META_KEYS = {"status", "reason"}


def header(path: Path) -> dict[str, str]:
    """작업 지시서 첫 ```yaml 블록에서 key: value 를 읽는다 (주석 제거, 첫 단어만)."""
    text = path.read_text(encoding="utf-8")
    m = re.search(r"```yaml\n(.*?)```", text, re.S)
    out: dict[str, str] = {}
    if not m:
        return out
    for line in m.group(1).splitlines():
        km = re.match(r"^([a-z_]+):\s*(\S+)", line)
        if km:
            out[km.group(1)] = km.group(2)
    return out


def check(assign_path: Path) -> list[str]:
    roles = tomllib.loads(ROLES.read_text(encoding="utf-8"))
    pairs = roles.get("separation", {}).get("pairs", [])
    known_roles = set(roles.get("roles", {})) | {r for p in pairs for r in p}
    families = {k: v["family"] for k, v in tomllib.loads(AGENTS.read_text(encoding="utf-8"))["agents"].items()}
    slices = tomllib.loads(assign_path.read_text(encoding="utf-8")).get("slice", {})

    violations: list[str] = []
    if not pairs:
        violations.append("[설정] roles.toml 에 [separation] pairs 가 없다")
    if not slices:
        violations.append(f"[설정] {assign_path.name} 에 슬라이스 배정이 없다")

    for sid, a in slices.items():
        assigned = {k: v for k, v in a.items() if k not in META_KEYS}
        for role, agent in assigned.items():
            if role not in known_roles:
                violations.append(f"[등록] {sid}: 알 수 없는 역할 '{role}'")
            if agent not in families:
                violations.append(f"[등록] {sid}: agents.toml 에 없는 에이전트 '{agent}' ({role})")
        for r1, r2 in pairs:
            if r1 in assigned and r2 in assigned:
                f1, f2 = families.get(assigned[r1]), families.get(assigned[r2])
                if f1 is not None and f1 == f2:
                    violations.append(
                        f"[분리] {sid}: {r1}({assigned[r1]}) 와 {r2}({assigned[r2]}) 가 같은 계열 '{f1}'"
                    )

    # G1: config/delegation.toml 검사
    delegation_info: dict | None = None
    if DELEGATION.is_file():
        try:
            del_doc = tomllib.loads(DELEGATION.read_text(encoding="utf-8"))
            del_sec = del_doc.get("delegation", {})
            if not isinstance(del_sec, dict):
                violations.append(f"[설정] {DELEGATION.name}: [delegation] 섹션이 없다")
                return violations
            record = del_sec.get("record")
            family_key = del_sec.get("family_key")
            require_family = del_sec.get("require_family", False)

            if not isinstance(record, str) or "{job}" not in record:
                violations.append(f"[설정] {DELEGATION.name}: record 에 '{{job}}' 이 없다")
                return violations

            if (
                Path(record).is_absolute()
                or record.startswith("/")
                or record.startswith("\\")
                or bool(re.match(r"^[A-Za-z]:", record))
            ):
                violations.append(f"[설정] {DELEGATION.name}: record 가 절대 경로이다")
                return violations

            if ".." in Path(record).parts or ".." in record.replace("\\", "/").split("/"):
                violations.append(f"[설정] {DELEGATION.name}: record 에 '..' 구성 요소가 포함되어 있다")
                return violations

            if not isinstance(family_key, str) or not family_key:
                violations.append(f"[설정] {DELEGATION.name}: family_key 가 비어 있거나 문자열이 아니다")
                return violations

            delegation_info = {
                "record": record,
                "family_key": family_key,
                "require_family": bool(require_family),
            }
        except Exception as e:
            violations.append(f"[설정] {DELEGATION.name}: TOML 파싱 실패: {e}")
            return violations

    for wo in sorted(WORK_ORDERS.glob("*.md")) if WORK_ORDERS.is_dir() else []:
        h = header(wo)
        agent = h.get("agent")
        actual_family = h.get("family")
        job = h.get("job")

        if agent and actual_family:
            registered_family = families.get(agent)
            if actual_family != registered_family:
                violations.append(
                    f"[계열] {wo.name}: agent '{agent}' 실제 계열 '{actual_family}' ≠ 등록 '{registered_family}'"
                )

        if delegation_info is not None:
            # G4: require_family = true 이고 agent 가 있는 지시서에 family 도 job 도 없으면
            if delegation_info["require_family"] and agent and not actual_family and not job:
                violations.append(f"[계열] {wo.name}: family 또는 job 이 없다")

            # G2: 머리말에 agent 와 job 이 있으면
            if agent and job:
                if not re.fullmatch(r"[A-Za-z0-9._-]+", job):
                    violations.append(f"[계열] {wo.name}: job '{job}' 형식이 올바르지 않다")
                elif job in (".", ".."):
                    violations.append(f"[계열] {wo.name}: job '{job}' 은 올바르지 않다")
                else:
                    rel_record = delegation_info["record"].replace("{job}", job)
                    rec_path = ROOT / rel_record
                    try:
                        resolved_path = rec_path.resolve()
                        root_resolved = ROOT.resolve()
                        is_inside = (
                            resolved_path.is_relative_to(root_resolved)
                            and resolved_path != root_resolved
                        )
                    except Exception:
                        is_inside = False

                    if not is_inside:
                        violations.append(f"[계열] {wo.name}: 위임 기록 경로가 프로젝트 루트 밖이다")
                    elif not resolved_path.is_file():
                        violations.append(f"[계열] {wo.name}: 위임 기록 {rel_record} 파일이 없다")
                    else:
                        try:
                            rec_text = resolved_path.read_bytes().decode("utf-8-sig")
                            rec_data = json.loads(rec_text)
                            if not isinstance(rec_data, dict):
                                violations.append(f"[계열] {wo.name}: 위임 기록 {rel_record} JSON 객체가 아니다")
                            else:
                                fam_k = delegation_info["family_key"]
                                if fam_k not in rec_data:
                                    violations.append(f"[계열] {wo.name}: 위임 기록 {rel_record} 에 '{fam_k}' 키가 없다")
                                else:
                                    rec_fam = str(rec_data[fam_k])
                                    registered_family = families.get(agent)
                                    # G3: 기록 계열 ≠ 등록 계열
                                    if rec_fam != registered_family:
                                        violations.append(
                                            f"[계열] {wo.name}: agent '{agent}' 위임 기록 계열 '{rec_fam}' ≠ 등록 '{registered_family}'"
                                        )
                                    # G3: 머리말 family 도 있으면 머리말 ≠ 기록
                                    if actual_family and actual_family != rec_fam:
                                        violations.append(
                                            f"[계열] {wo.name}: 머리말 family '{actual_family}' ≠ 위임 기록 '{rec_fam}'"
                                        )
                        except Exception as e:
                            violations.append(f"[계열] {wo.name}: 위임 기록 {rel_record} JSON 파싱 실패: {e}")

        sid, role = h.get("slice"), h.get("role")
        if not (sid and role and agent):
            continue  # 슬라이스에 묶이지 않은 지시서는 검사하지 않는다
        expected = slices.get(sid, {}).get(role)
        if expected is None:
            violations.append(f"[지시서] {wo.name}: 슬라이스 {sid} 에 역할 {role} 의 배정이 없다")
        elif agent != expected:
            violations.append(f"[지시서] {wo.name}: agent '{agent}' ≠ 배정 '{expected}' (슬라이스 {sid}, {role})")
    return violations


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--assignments", type=Path, default=ASSIGNMENTS)
    args = ap.parse_args()
    violations = check(args.assignments)
    print(json.dumps({"gate": "GA", "violations": violations, "passed": not violations}, indent=2, ensure_ascii=False))
    return 0 if not violations else 1


if __name__ == "__main__":
    sys.exit(main())
