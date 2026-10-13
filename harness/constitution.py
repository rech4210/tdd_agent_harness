"""헌법 생성기 및 검사기 (Core, SDD 통합 E5). 언어를 모른다.

헌법(.specify/memory/constitution.md)은 에이전트가 읽는 규칙 사본이다.
유일한 권위는 설정(config/*.toml)이고 헌법은 거기서 생성한다.
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

from paths import CONFIG, ROOT


def render(config_dir: Path) -> str:
    """config_dir/gates.toml 과 config_dir/roles.toml 을 읽어 헌법 텍스트를 생성한다."""
    gates_path = config_dir / "gates.toml"
    roles_path = config_dir / "roles.toml"

    if not gates_path.is_file():
        raise FileNotFoundError(f"gates.toml 설정 파일이 없습니다: {gates_path}")
    if not roles_path.is_file():
        raise FileNotFoundError(f"roles.toml 설정 파일이 없습니다: {roles_path}")

    gates_data = tomllib.loads(gates_path.read_text(encoding="utf-8"))
    roles_data = tomllib.loads(roles_path.read_text(encoding="utf-8"))

    # 설정 검사 (WO-H04 §4.3)
    gates = gates_data.get("gate", [])
    if not isinstance(gates, list):
        raise ValueError("gates.toml 의 gate 는 목록이어야 합니다")
    for g in gates:
        if not isinstance(g, dict):
            raise ValueError(f"게이트 항목이 올바르지 않습니다: {g}")
        for field in ("id", "name", "why"):
            val = g.get(field)
            if not isinstance(val, str) or not val.strip():
                raise ValueError(f"게이트 '{g.get('id', '?')}'에 {field} 필드가 없거나 비어 있습니다")

    roles = roles_data.get("roles", {})
    if not isinstance(roles, dict):
        raise ValueError("roles.toml 의 roles 는 테이블이어야 합니다")
    for rname, rinfo in roles.items():
        if not isinstance(rinfo, dict):
            raise ValueError(f"역할 '{rname}' 설정이 올바르지 않습니다")
        if "allow" not in rinfo or not isinstance(rinfo["allow"], list):
            raise ValueError(f"역할 '{rname}'에 allow 목록이 없습니다")
        if "deny" not in rinfo:
            rinfo["deny"] = []
        elif not isinstance(rinfo["deny"], list):
            raise ValueError(f"역할 '{rname}'의 deny 가 목록이 아닙니다")

    pairs = roles_data.get("separation", {}).get("pairs", [])
    if not isinstance(pairs, list):
        raise ValueError("roles.toml 의 separation.pairs 는 목록이어야 합니다")
    for p in pairs:
        if not isinstance(p, list) or len(p) != 2 or not all(isinstance(x, str) and x.strip() for x in p):
            raise ValueError(f"separation.pairs 항목은 문자열 두 개짜리 목록이어야 합니다: {p}")

    lines: list[str] = [
        "<!-- 생성 파일: config/*.toml 에서 python harness/constitution.py --write 로 만든다. 직접 고치지 않는다 -->",
        "",
        "# Project Constitution",
        "",
        "## Core Principles",
        "",
        "### 권위 순서",
        "잠긴 테스트 > 결정(D-##) > 불변식(INV-#) > 명세 산문",
        "",
    ]

    # 잠금 대상: [harness] locked (없으면 tests/**)을 한 줄에
    harness_cfg = gates_data.get("harness", {})
    locked = harness_cfg.get("locked", ["tests/**"])
    lines.append("### 잠금 대상")
    lines.append(f"- 잠금 대상: {', '.join(locked)}")
    lines.append("")

    # 게이트: id · 이름 · 이유(why)
    lines.append("### 게이트")
    gates = gates_data.get("gate", [])
    for g in gates:
        gid = g.get("id", "")
        gname = g.get("name", "")
        gwhy = g.get("why", "")
        if gwhy:
            lines.append(f"- {gid} ({gname}): {gwhy}")
        else:
            lines.append(f"- {gid} ({gname})")
    lines.append("")

    # 역할: 이름과 허용(allow) · 금지(deny) 경로
    lines.append("### 역할 권한")
    roles = roles_data.get("roles", {})
    for rname in sorted(roles):
        rinfo = roles[rname]
        allow = ", ".join(rinfo.get("allow", []))
        deny = ", ".join(rinfo.get("deny", []))
        lines.append(f"- {rname}: 허용({allow}) / 금지({deny})")
    lines.append("")

    # 분리 쌍: 한 줄에 두 역할
    pairs = roles_data.get("separation", {}).get("pairs", [])
    if pairs:
        lines.append("### 역할 분리 제약")
        for p in pairs:
            if len(p) >= 2:
                lines.append(f"- {p[0]} ≠ {p[1]}: 다른 모델 계열")
        lines.append("")

    # Governance: 규칙 변경 방법
    lines.extend([
        "## Governance",
        "",
        "규칙 변경은 사람이 config/*.toml 을 고친 뒤 python harness/constitution.py --write 로 다시 만든다. 헌법 파일을 직접 고치지 않는다.",
        "",
    ])

    return "\n".join(lines)


def check(target: Path, config_dir: Path) -> tuple[bool, str]:
    """헌법 파일이 config_dir 의 설정에서 생성한 결과와 일치하는지 검사한다."""
    if not target.is_file():
        return False, f"헌법 파일이 없습니다: {target}"
    expected = render(config_dir)
    actual = target.read_text(encoding="utf-8")
    if actual != expected:
        return False, "헌법 파일의 내용이 설정(config/*.toml)과 일치하지 않습니다"
    return True, ""


def main() -> int:
    default_out = ROOT / ".specify" / "memory" / "constitution.md"

    parser = argparse.ArgumentParser(description="헌법 생성 및 검사 도구")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", action="store_true", help="헌법 파일 생성 및 기록")
    group.add_argument("--check", action="store_true", help="헌법 파일 일치 여부 검사")
    parser.add_argument("--out", type=Path, default=default_out, help="헌법 파일 경로")
    parser.add_argument("--config", type=Path, default=CONFIG, help="설정 디렉토리 경로")
    args = parser.parse_args()

    if args.write:
        text = render(args.config)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8", newline="\n")
        print(f"헌법 파일 작성 완료: {args.out}")
        return 0
    elif args.check:
        ok, reason = check(args.out, args.config)
        if not ok:
            print(f"헌법 검사 실패: {reason}", file=sys.stderr)
            return 1
        print("헌법 검사 통과: 일치함")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
