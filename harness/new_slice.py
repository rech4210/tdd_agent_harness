"""슬라이스 초안 생성 도구 (WO-H46).

슬라이스마다 리드가 설정 블록 · 배정 · 지시서 · 자동 승인 근거를 만들 수 있도록 초안을 제공한다.
설정 파일은 사람 소유이므로 config/ 를 직접 수정하지 않는다.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path

HARNESS_DIR = Path(__file__).resolve().parent
if str(HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(HARNESS_DIR))

from adapter_loader import harness_settings, load_adapter
from paths import ASSIGNMENTS, ROOT, SLICES

SLICE_ID_RE = r"([A-Za-z][A-Za-z0-9]*-[A-Za-z]*\d+[a-z]?|(?:S\d+|\d{2,})-[a-z0-9-]+)"


def toml_str(val: str) -> str:
    return json.dumps(val, ensure_ascii=False)


def toml_key(key: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_-]+", key):
        return key
    return toml_str(key)


def toml_value(val: object) -> str:
    if isinstance(val, bool):
        return "true" if val else "false"
    elif isinstance(val, str):
        return toml_str(val)
    elif isinstance(val, (int, float)):
        return str(val)
    elif isinstance(val, list):
        return "[" + ", ".join(toml_value(x) for x in val) + "]"
    return toml_str(str(val))


def toml_list(vals: list[str]) -> str:
    return "[" + ", ".join(toml_str(v) for v in vals) + "]"


def format_table_blocks(header_parts: list[str], data: dict) -> list[str]:
    scalars: list[tuple[str, object]] = []
    subtables: list[tuple[str, dict]] = []

    for k, v in data.items():
        if isinstance(v, dict):
            subtables.append((k, v))
        else:
            scalars.append((k, v))

    lines: list[str] = []
    if scalars:
        header = ".".join(toml_key(p) for p in header_parts)
        lines.append(f"\n[{header}]")
        for k, v in scalars:
            lines.append(f"{toml_key(k)} = {toml_value(v)}")

    for k, v in subtables:
        lines.extend(format_table_blocks(header_parts + [k], v))

    return lines


def format_slices_section(
    slice_id: str,
    title: str,
    files: list[str],
    tests: list[str],
    risk: str,
    depends: list[str],
) -> str:
    adapter = load_adapter()
    mutation_config = adapter.mutation_config_draft(files, tests)

    lines = [
        f"[slice.{slice_id}]",
        f"title = {toml_str(title)}",
        f"files = {toml_list(files)}",
        f"risk = {toml_str(risk)}",
        f"depends = {toml_list(depends)}",
    ]
    if mutation_config:
        lines.append(f"\n[slice.{slice_id}.mutation_config]")
        for k, v in mutation_config.items():
            if isinstance(v, list):
                lines.append(f"{k} = {toml_list(v)}")
            elif isinstance(v, str):
                lines.append(f"{k} = {toml_str(v)}")
            elif isinstance(v, bool):
                lines.append(f"{k} = {'true' if v else 'false'}")
            elif isinstance(v, (int, float)):
                lines.append(f"{k} = {v}")

    override_fn = getattr(adapter, "slice_override_draft", None)
    override = override_fn(files, tests) if callable(override_fn) else {}
    if override:
        lines.extend(format_table_blocks(["slice", slice_id, "override"], override))

    return "\n".join(lines) + "\n"



def format_assignments_section(slice_id: str, last_slice: dict | None) -> tuple[str, str]:
    if not last_slice:
        msg = "# 기존 배정이 없습니다. python harness/roles.py --preset 으로 배정을 설정하세요.\n"
        return msg, ""

    role_pairs = {k: v for k, v in last_slice.items() if k not in ("status", "reason")}
    implementer = str(role_pairs.get("implementer", ""))

    lines = [f"[slice.{slice_id}]", 'status = "open"']
    for k, v in role_pairs.items():
        lines.append(f"{k} = {toml_str(str(v))}")
    lines.append('reason = "new_slice 초안"')

    return "\n".join(lines) + "\n", implementer


def format_assignments_from_defaults(slice_id: str, defaults: dict) -> tuple[str, str]:
    if not defaults:
        msg = "# 기본 배정이 없습니다. python harness/roles.py --preset 으로 배정을 설정하세요.\n"
        return msg, ""

    role_pairs = {k: v for k, v in defaults.items() if k not in ("status", "reason")}
    implementer = str(role_pairs.get("implementer", ""))

    lines = [f"[slice.{slice_id}]", 'status = "open"']
    for k, v in role_pairs.items():
        lines.append(f"{k} = {toml_str(str(v))}")
    lines.append('reason = "new_slice 초안"')

    return "\n".join(lines) + "\n", implementer


def format_work_order(
    slice_id: str,
    title: str,
    files: list[str],
    tests: list[str],
    implementer: str,
    base_tag: str,
) -> str:
    files_md = "\n".join(f"- `{f}`" for f in files)
    tests_md = "\n".join(f"- `{t}`" for t in tests)

    content = f"""# {slice_id} {title} 구현

> 이 문서가 당신(구현자)과의 유일한 계약이다. 여기 적히지 않은 것은 하지 않는다.
> 막히면 추측해서 진행하지 말고 규약대로 멈추고 보고한다.

```yaml
id: {slice_id}
role: implementer
agent: {implementer}
base: {base_tag}
slice: {slice_id}
shell: none
feedback: python harness/run_gates.py --feedback --role implementer --base {base_tag} --wo {slice_id}
max_rounds: 3
```

## 0. 대화 규약
- 작업 중 막히거나 명세가 모호하면 `QUESTION:` 줄을 남기고 기다린다.
- 작업이 끝나면 `DONE` 줄을 남긴다.

## 1. 목표
테스트가 통과하는 제품 코드를 구현한다.

## 2. 읽을 수 있는 파일
{tests_md}
- 이 문서: 계약

## 3. 만들 파일 (쓸 수 있는 파일)
{files_md}

## 4. 완료 조건
`python harness/run_gates.py --role implementer --base {base_tag} --slice {slice_id}`
"""
    return content


def format_auto_approve_ref(slice_id: str, policy: str, risk: str) -> str:
    if risk == "high":
        return "자동 승인 범위 밖 (risk=high 슬라이스는 사람 H3 승인이 필요합니다)\n"
    # 잡힌 수 · 후보 수는 실제 게으른 후보 실행 결과로 채운다. 숫자를 미리 넣지 않는다 (리드 보정, N10)
    return (f"python harness/mark.py auto-approve {policy} --wo {slice_id} --gt pass --lazy-caught <잡힌 수>/<후보 수> "
            "--residual-defects 0 --base-types unbounded,reject-preserve --by lead\n")


def make_config_draft(slices_sec: str, assign_sec: str) -> str:
    commented_assign = "\n".join(
        f"# {line}" if line.strip() and not line.startswith("#") else line
        for line in assign_sec.strip().splitlines()
    )
    return f"{slices_sec.strip()}\n\n# config/assignments.toml\n{commented_assign}\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="슬라이스 초안 생성 도구")
    ap.add_argument("--slice", required=True, help="슬라이스 ID")
    ap.add_argument("--title", required=True, help="슬라이스 제목")
    ap.add_argument("--files", required=True, help="소스 파일 목록 (쉼표 구분)")
    ap.add_argument("--tests", required=True, help="테스트 파일 목록 (쉼표 구분)")
    ap.add_argument("--risk", required=True, help="위험도 (low|medium|high)")
    ap.add_argument("--depends", default="", help="의존 슬라이스 목록 (쉼표 구분)")
    ap.add_argument("--policy", default="D-28", help="자동 승인 정책 ID (기본: D-28)")
    ap.add_argument("--write", action="store_true", help="초안 파일 생성")
    args = ap.parse_args()

    # E01 유효성 검사
    if not re.fullmatch(SLICE_ID_RE, args.slice):
        print(f"오류: 슬라이스 ID 형식이 올바르지 않습니다: {args.slice}", file=sys.stderr)
        return 2

    slices_data: dict = {}
    if SLICES.is_file():
        try:
            slices_data = tomllib.loads(SLICES.read_text(encoding="utf-8")).get("slice", {})
        except Exception as err:
            # 깨진 설정을 빈 설정으로 보면 중복 ID · depends 검사가 사라진다 (리드 보정, E02)
            print(f"오류: config/slices.toml 을 읽을 수 없습니다: {err}", file=sys.stderr)
            return 2

    if args.slice in slices_data:
        print(f"오류: 이미 존재하는 슬라이스 ID 입니다: {args.slice}", file=sys.stderr)
        return 2

    depends_list: list[str] = []
    if args.depends:
        depends_list = [d.strip() for d in args.depends.split(",") if d.strip()]
        for dep in depends_list:
            if dep not in slices_data:
                print(f"오류: 존재하지 않는 depends 슬라이스 입니다: {dep}", file=sys.stderr)
                return 2

    if args.risk not in ("low", "medium", "high"):
        print(f"오류: --risk 는 low, medium, high 중 하나여야 합니다: {args.risk}", file=sys.stderr)
        return 2

    files_list = [f.strip() for f in args.files.split(",") if f.strip()]
    if not files_list:
        print("오류: --files 가 비어 있습니다", file=sys.stderr)
        return 2

    tests_list = [t.strip() for t in args.tests.split(",") if t.strip()]
    if not tests_list:
        print("오류: --tests 가 비어 있습니다", file=sys.stderr)
        return 2

    if not re.fullmatch(r"D-\d+", args.policy):
        print(f"오류: --policy 형식이 올바르지 않습니다: {args.policy} (예: D-28)", file=sys.stderr)
        return 2

    # --write 시 대상 파일 중복 검사 (덮어쓰기 금지)
    wo_path = ROOT / "work_orders" / f"{args.slice}.md"
    cfg_draft_path = ROOT / "reports" / f"{args.slice}-config-draft.toml"
    auto_ref_path = ROOT / "reports" / f"{args.slice}-h3-auto-ref.md"

    if args.write:
        if wo_path.exists() or cfg_draft_path.exists() or auto_ref_path.exists():
            print("오류: 대상 파일이 이미 존재합니다 (덮어쓰기 금지)", file=sys.stderr)
            return 2

    # 섹션 내용 생성
    slices_sec = format_slices_section(
        slice_id=args.slice,
        title=args.title,
        files=files_list,
        tests=tests_list,
        risk=args.risk,
        depends=depends_list,
    )

    state_file = ROOT / "config" / "role-state.json"
    if state_file.is_file():
        import role_state

        chk = role_state.check(ROOT)
        if chk != 0:
            print("오류: 활성 역할 상태 정합성 검사 실패 (roles.py --check 확인 필요)", file=sys.stderr)
            return 2
        try:
            state_data = json.loads(state_file.read_text(encoding="utf-8"))
            defaults = state_data.get("default_assignments", {})
            assign_sec, implementer = format_assignments_from_defaults(args.slice, defaults)
        except Exception as err:
            print(f"오류: 역할 상태 파일 읽기 실패: {err}", file=sys.stderr)
            return 2
    else:
        sys.stderr.write("경고: 활성 역할 상태(config/role-state.json)가 없습니다. 기존 슬라이스 배정을 사용합니다.\n")
        asg_data: dict = {}
        if ASSIGNMENTS.is_file():
            try:
                asg_data = tomllib.loads(ASSIGNMENTS.read_text(encoding="utf-8")).get("slice", {})
            except Exception:
                asg_data = {}

        last_slice = list(asg_data.values())[-1] if asg_data else None
        assign_sec, implementer = format_assignments_section(args.slice, last_slice)

    pattern = harness_settings().get("base_tag", "{compact}-base")
    compact = args.slice.lower().replace("-", "")
    base_tag = pattern.format(compact=compact, wo=args.slice)

    wo_sec = format_work_order(
        slice_id=args.slice,
        title=args.title,
        files=files_list,
        tests=tests_list,
        implementer=implementer,
        base_tag=base_tag,
    )

    ref_sec = format_auto_approve_ref(
        slice_id=args.slice,
        policy=args.policy,
        risk=args.risk,
    )

    stdout_text = (
        f"### config/slices.toml\n{slices_sec}\n"
        f"### config/assignments.toml\n{assign_sec}\n"
        f"### work_orders/{args.slice}.md\n{wo_sec}\n"
        f"### 자동 승인 참조\n{ref_sec}"
    )
    sys.stdout.write(stdout_text)

    if args.write:
        draft_toml = make_config_draft(slices_sec, assign_sec)

        wo_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_draft_path.parent.mkdir(parents=True, exist_ok=True)
        auto_ref_path.parent.mkdir(parents=True, exist_ok=True)

        wo_path.write_text(wo_sec.strip() + "\n", encoding="utf-8", newline="\n")
        cfg_draft_path.write_text(draft_toml, encoding="utf-8", newline="\n")
        auto_ref_path.write_text(ref_sec.strip() + "\n", encoding="utf-8", newline="\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
