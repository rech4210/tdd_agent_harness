"""역할 프리셋 적용 및 대화형 마법사 (HD-17, HD-18).

templates/roles/<이름>/ 의 프리셋을 읽거나 대화형 마법사(--ask)를 통해:
1. config/agents.toml 에 없는 에이전트를 등록한다.
2. --slice <ID> 가 있으면 config/assignments.toml 에 슬라이스 배정을 추가한다.
3. 프리셋의 안내 문서(*.md)를 프로젝트 루트의 같은 파일에 <!-- harness:roles:begin --> 구간으로 반영한다.

사용:
  python harness/roles.py --list
  python harness/roles.py --preset <이름> [--slice <ID>] [--dry-run]
  python harness/roles.py --ask
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

HARNESS_DIR = Path(__file__).resolve().parent
if str(HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(HARNESS_DIR))
from paths import AGENTS, ASSIGNMENTS, CONFIG, ROLES, ROOT

TEMPLATES_ROLES = ROOT / "templates" / "roles"
BEGIN = "<!-- harness:roles:begin -->"
END = "<!-- harness:roles:end -->"


def list_presets() -> int:
    if not TEMPLATES_ROLES.is_dir():
        print("프리셋 디렉터리가 없습니다.")
        return 0
    try:
        for pdir in sorted(TEMPLATES_ROLES.iterdir()):
            preset_file = pdir / "preset.toml"
            if pdir.is_dir() and preset_file.is_file():
                try:
                    data = tomllib.loads(preset_file.read_text(encoding="utf-8"))
                    about = data.get("about", "")
                    lead = data.get("lead", "")
                    lead_part = f" (lead: {lead})" if lead else ""
                    print(f"{pdir.name}: {about}{lead_part}")
                except Exception as e:
                    print(f"오류: {pdir.name} 프리셋 읽기 실패: {e}", file=sys.stderr)
                    return 1
    except Exception as e:
        print(f"오류: {e}", file=sys.stderr)
        return 1
    return 0


def get_role_order(roles_data: dict) -> list[str]:
    order = list(roles_data.get("roles", {}).keys())
    for pair in roles_data.get("separation", {}).get("pairs", []):
        if isinstance(pair, (list, tuple)):
            for r in pair:
                if r not in order:
                    order.append(r)
    return order


def validate_roles_setup(
    roles_data: dict,
    existing_agents: dict,
    existing_slices: dict,
    virtual_agents: dict,
    assigned_roles: dict,
    slice_id: str | None,
) -> list[str]:
    errors: list[str] = []
    pairs = roles_data.get("separation", {}).get("pairs", [])
    valid_roles = set(roles_data.get("roles", {}).keys()) | {r for p in pairs for r in p}

    # 에이전트 이름 및 계열 검사
    for aname, ainfo in virtual_agents.items():
        if not re.fullmatch(r"[A-Za-z0-9_-]+", aname):
            errors.append(f"에이전트 이름 '{aname}' 형식이 올바르지 않습니다 (^[A-Za-z0-9_-]+$)")
        if not isinstance(ainfo, dict) or "family" not in ainfo:
            errors.append(f"에이전트 '{aname}' 에 family 가 없습니다")
            continue
        fam = ainfo.get("family")
        if not isinstance(fam, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", fam):
            errors.append(f"에이전트 '{aname}' 의 family 값 '{fam}' 형식이 올바르지 않습니다 (^[A-Za-z0-9_-]+$)")
        if aname in existing_agents:
            reg_fam = existing_agents[aname].get("family")
            if reg_fam != fam:
                errors.append(f"에이전트 '{aname}' 계열 '{fam}' 이 등록된 계열 '{reg_fam}' 과 다릅니다")

    # M7, Q2: 배정 역할 및 담당 에이전트 검사
    known_agents = set(existing_agents.keys()) | set(virtual_agents.keys())
    for role, agent in assigned_roles.items():
        if not re.fullmatch(r"[A-Za-z0-9_-]+", role):
            errors.append(f"역할 이름 '{role}' 형식이 올바르지 않습니다 (^[A-Za-z0-9_-]+$)")
        if role not in valid_roles:
            errors.append(f"배정의 역할 '{role}' 이 roles.toml 에 정의되지 않았습니다")
        if not isinstance(agent, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", agent):
            errors.append(f"배정 에이전트 '{agent}' 형식이 올바르지 않습니다 (^[A-Za-z0-9_-]+$)")
        elif agent not in known_agents:
            errors.append(f"배정의 에이전트 '{agent}' ({role}) 가 등록된 에이전트 목록에 없습니다")

    combined_families: dict[str, str] = {
        k: v["family"] for k, v in existing_agents.items() if isinstance(v, dict) and "family" in v
    }
    for k, v in virtual_agents.items():
        if isinstance(v, dict) and "family" in v:
            combined_families[k] = v["family"]

    for r1, r2 in pairs:
        if r1 in assigned_roles and r2 in assigned_roles:
            a1, a2 = assigned_roles[r1], assigned_roles[r2]
            f1, f2 = combined_families.get(a1), combined_families.get(a2)
            if f1 is not None and f1 == f2:
                errors.append(f"분리 제약 위반: {r1}({a1}) 와 {r2}({a2}) 가 같은 계열 '{f1}' 입니다")

    if slice_id is not None:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", slice_id):
            errors.append(f"슬라이스 ID '{slice_id}' 형식이 올바르지 않습니다 (^[A-Za-z0-9_-]+$)")
        elif slice_id in existing_slices:
            errors.append(f"슬라이스 '{slice_id}' 가 이미 assignments.toml 에 있습니다")

    return errors


def execute_planned_writes(
    preset_name: str | None,
    all_virtual_agents: dict,
    existing_agents: dict,
    assigned_roles: dict,
    slice_id: str | None,
    write_docs: bool,
    dry_run: bool,
) -> int:
    new_agents_to_add = {k: v for k, v in all_virtual_agents.items() if k not in existing_agents}

    md_files: list[Path] = []
    if write_docs and preset_name:
        preset_dir = TEMPLATES_ROLES / preset_name
        md_files = sorted(f for f in preset_dir.glob("*.md") if f.is_file())

    # B6: 대상 파일이 폴더인지, 읽기 가능한지 등 검사
    for mf in md_files:
        target_path = ROOT / mf.name
        if target_path.exists() and not target_path.is_file():
            print(f"오류: 대상 파일 '{target_path.name}' 이 일반 파일이 아닙니다 (디렉터리 등)", file=sys.stderr)
            return 1
        if target_path.is_file():
            try:
                target_path.read_bytes()
            except Exception as e:
                print(f"오류: 대상 파일 '{target_path.name}' 읽기 실패: {e}", file=sys.stderr)
                return 1

    planned_writes: list[tuple[Path, bytes]] = []

    if new_agents_to_add:
        append_text = ""
        for name, info in new_agents_to_add.items():
            append_text += f"\n[agents.{name}]\nfamily = \"{info['family']}\"\n"
        curr_b = AGENTS.read_bytes() if AGENTS.is_file() else b""
        new_b = curr_b + append_text.encode("utf-8")
        if new_b != curr_b:
            planned_writes.append((AGENTS, new_b))

    if slice_id is not None:
        slice_text = f"\n[slice.{slice_id}]\nstatus = \"open\"\n"
        for role, agent in assigned_roles.items():
            slice_text += f"{role} = \"{agent}\"\n"
        reason_str = f"역할 프리셋 {preset_name}" if preset_name else "역할 마법사"
        slice_text += f"reason = \"{reason_str}\"\n"
        curr_b = ASSIGNMENTS.read_bytes() if ASSIGNMENTS.is_file() else b""
        new_b = curr_b + slice_text.encode("utf-8")
        if new_b != curr_b:
            planned_writes.append((ASSIGNMENTS, new_b))

    changed_docs: list[str] = []
    for mf in md_files:
        target_path = ROOT / mf.name
        doc_content = mf.read_text(encoding="utf-8").strip()
        section_b = f"{BEGIN}\n{doc_content}\n{END}".encode("utf-8")
        if not target_path.exists():
            new_b = section_b + b"\n"
            planned_writes.append((target_path, new_b))
            changed_docs.append(mf.name)
        else:
            curr_b = target_path.read_bytes()
            BEGIN_B = BEGIN.encode("utf-8")
            END_B = END.encode("utf-8")
            idx_begin = curr_b.find(BEGIN_B)
            idx_end = curr_b.find(END_B)
            if idx_begin != -1 and idx_end != -1 and idx_end >= idx_begin:
                prefix_b = curr_b[:idx_begin]
                suffix_b = curr_b[idx_end + len(END_B):]
                new_b = prefix_b + section_b + suffix_b
            else:
                work_b = curr_b
                if not work_b.endswith(b"\n"):
                    work_b += b"\n"
                if work_b and not work_b.endswith(b"\n\n"):
                    work_b += b"\n"
                new_b = work_b + section_b + b"\n"
            if new_b != curr_b:
                planned_writes.append((target_path, new_b))
                changed_docs.append(mf.name)

    if dry_run:
        target_label = f"역할 프리셋 '{preset_name}'" if preset_name else "역할 마법사"
        print(f"[dry-run] {target_label} 적용 시 변경될 내용:")
        if new_agents_to_add:
            print(f"  - agents.toml 등록 에이전트: {', '.join(new_agents_to_add)}")
        if slice_id is not None:
            print(f"  - assignments.toml 추가 슬라이스: {slice_id}")
        for doc_name in changed_docs:
            print(f"  - 문서 반영: {doc_name}")
        return 0

    # 모든 쓰기 대상 최종 확인
    for path, _ in planned_writes:
        if path.exists() and not path.is_file():
            print(f"오류: 대상 파일 '{path.name}' 이 일반 파일이 아닙니다", file=sys.stderr)
            return 1

    # 쓰기 실행
    for path, content_b in planned_writes:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content_b)

    if preset_name and write_docs:
        print(f"역할 프리셋 '{preset_name}' 적용 완료.")
    elif preset_name:
        print(f"역할 프리셋 '{preset_name}' (사용자 배정) 적용 완료.")
    else:
        print("역할 마법사 적용 완료.")
    return 0


def apply_preset(preset_name: str, slice_id: str | None, dry_run: bool) -> int:
    try:
        # B3: 프리셋 이름 형식 검사 ([A-Za-z0-9_-]+ 전체 일치)
        if not re.fullmatch(r"[A-Za-z0-9_-]+", preset_name):
            print(f"오류: 프리셋 이름 '{preset_name}' 형식이 올바르지 않습니다 (^[A-Za-z0-9_-]+$)", file=sys.stderr)
            return 1

        preset_dir = TEMPLATES_ROLES / preset_name
        preset_file = preset_dir / "preset.toml"
        if not preset_file.is_file():
            print(f"오류: 프리셋 '{preset_name}' 이 templates/roles 에 없습니다", file=sys.stderr)
            return 1

        try:
            preset_data = tomllib.loads(preset_file.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"오류: 프리셋 파일 '{preset_file}' 파싱 실패: {e}", file=sys.stderr)
            return 1

        preset_agents: dict = preset_data.get("agents", {})
        if not isinstance(preset_agents, dict):
            print(f"오류: 프리셋 '{preset_name}' 의 agents 가 딕셔너리가 아닙니다", file=sys.stderr)
            return 1

        preset_assign: dict = preset_data.get("assign", {})
        if not isinstance(preset_assign, dict):
            print(f"오류: 프리셋 '{preset_name}' 의 assign 이 딕셔너리가 아닙니다", file=sys.stderr)
            return 1

        # B5, B7: roles.toml 검사
        if not ROLES.is_file():
            print(f"오류: 역할 설정 파일 '{ROLES}' 이 없습니다", file=sys.stderr)
            return 1
        try:
            roles_data = tomllib.loads(ROLES.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"오류: 역할 설정 '{ROLES}' 파싱 실패: {e}", file=sys.stderr)
            return 1

        pairs = roles_data.get("separation", {}).get("pairs")
        if not isinstance(pairs, list) or len(pairs) == 0:
            print(f"오류: {ROLES.name} 에 [separation] pairs 가 없거나 비어 있습니다", file=sys.stderr)
            return 1
        for pair in pairs:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                print(f"오류: {ROLES.name} 의 separation pair 형식이 올바르지 않습니다", file=sys.stderr)
                return 1

        # AGENTS 검사
        existing_agents: dict = {}
        if AGENTS.is_file():
            try:
                agents_doc = tomllib.loads(AGENTS.read_text(encoding="utf-8"))
                existing_agents = agents_doc.get("agents", {})
                if not isinstance(existing_agents, dict):
                    print(f"오류: {AGENTS.name} 의 agents 가 딕셔너리가 아닙니다", file=sys.stderr)
                    return 1
            except Exception as e:
                print(f"오류: {AGENTS.name} 파싱 실패: {e}", file=sys.stderr)
                return 1

        for aname, ainfo in existing_agents.items():
            if not isinstance(ainfo, dict) or "family" not in ainfo or not isinstance(ainfo.get("family"), str) or not ainfo.get("family"):
                print(f"오류: {AGENTS.name} 의 등록 에이전트 '{aname}' 에 family 가 없습니다", file=sys.stderr)
                return 1

        # ASSIGNMENTS 검사
        existing_slices: dict = {}
        if ASSIGNMENTS.is_file():
            try:
                assignments_data = tomllib.loads(ASSIGNMENTS.read_text(encoding="utf-8"))
                existing_slices = assignments_data.get("slice", {})
            except Exception as e:
                print(f"오류: {ASSIGNMENTS.name} 파싱 실패: {e}", file=sys.stderr)
                return 1

        errors = validate_roles_setup(
            roles_data=roles_data,
            existing_agents=existing_agents,
            existing_slices=existing_slices,
            virtual_agents=preset_agents,
            assigned_roles=preset_assign,
            slice_id=slice_id,
        )
        if errors:
            for err in errors:
                print(f"오류: {err}", file=sys.stderr)
            return 1

        return execute_planned_writes(
            preset_name=preset_name,
            all_virtual_agents=preset_agents,
            existing_agents=existing_agents,
            assigned_roles=preset_assign,
            slice_id=slice_id,
            write_docs=True,
            dry_run=dry_run,
        )

    except Exception as e:
        print(f"오류: {e}", file=sys.stderr)
        return 1


def safe_input(prompt: str = "") -> str:
    try:
        return input(prompt)
    except EOFError:
        print("오류: 입력이 중간에 끝났습니다", file=sys.stderr)
        raise


def ask_wizard(dry_run: bool = False) -> int:
    try:
        # roles.toml 로드
        if not ROLES.is_file():
            print(f"오류: 역할 설정 파일 '{ROLES}' 이 없습니다", file=sys.stderr)
            return 1
        try:
            roles_data = tomllib.loads(ROLES.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"오류: 역할 설정 '{ROLES}' 파싱 실패: {e}", file=sys.stderr)
            return 1

        pairs = roles_data.get("separation", {}).get("pairs")
        if not isinstance(pairs, list) or len(pairs) == 0:
            print(f"오류: {ROLES.name} 에 [separation] pairs 가 없거나 비어 있습니다", file=sys.stderr)
            return 1
        for pair in pairs:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                print(f"오류: {ROLES.name} 의 separation pair 형식이 올바르지 않습니다", file=sys.stderr)
                return 1

        # AGENTS 로드
        existing_agents: dict = {}
        if AGENTS.is_file():
            try:
                agents_doc = tomllib.loads(AGENTS.read_text(encoding="utf-8"))
                existing_agents = agents_doc.get("agents", {})
                if not isinstance(existing_agents, dict):
                    print(f"오류: {AGENTS.name} 의 agents 가 딕셔너리가 아닙니다", file=sys.stderr)
                    return 1
            except Exception as e:
                print(f"오류: {AGENTS.name} 파싱 실패: {e}", file=sys.stderr)
                return 1

        for aname, ainfo in existing_agents.items():
            if not isinstance(ainfo, dict) or "family" not in ainfo or not isinstance(ainfo.get("family"), str) or not ainfo.get("family"):
                print(f"오류: {AGENTS.name} 의 등록 에이전트 '{aname}' 에 family 가 없습니다", file=sys.stderr)
                return 1

        # ASSIGNMENTS 로드
        existing_slices: dict = {}
        if ASSIGNMENTS.is_file():
            try:
                assignments_data = tomllib.loads(ASSIGNMENTS.read_text(encoding="utf-8"))
                existing_slices = assignments_data.get("slice", {})
            except Exception as e:
                print(f"오류: {ASSIGNMENTS.name} 파싱 실패: {e}", file=sys.stderr)
                return 1

        # 1단계: 프리셋 선택
        preset_list: list[Path] = []
        if TEMPLATES_ROLES.is_dir():
            for pdir in sorted(TEMPLATES_ROLES.iterdir()):
                if pdir.is_dir() and (pdir / "preset.toml").is_file():
                    preset_list.append(pdir)

        print("프리셋 목록:")
        for idx, pdir in enumerate(preset_list, 1):
            try:
                pdata = tomllib.loads((pdir / "preset.toml").read_text(encoding="utf-8"))
                about = pdata.get("about", "")
                lead = pdata.get("lead", "")
                lead_str = f" (lead: {lead})" if lead else ""
                print(f"  {idx}. {pdir.name}: {about}{lead_str}")
            except Exception:
                print(f"  {idx}. {pdir.name}")

        preset_raw = safe_input("프리셋 (이름 또는 번호, 빈 줄=없음): ")
        preset_name: str | None = None
        preset_data: dict = {}
        preset_agents: dict = {}
        preset_assign: dict = {}

        if preset_raw == "":
            preset_name = None
        elif preset_raw.isdigit():
            idx = int(preset_raw)
            if 1 <= idx <= len(preset_list):
                preset_name = preset_list[idx - 1].name
            else:
                print(f"오류: 범위 밖 프리셋 번호 '{preset_raw}'", file=sys.stderr)
                return 1
        else:
            matched = [p.name for p in preset_list if p.name == preset_raw]
            if matched:
                preset_name = matched[0]
            else:
                print(f"오류: 없는 프리셋 이름 '{preset_raw}'", file=sys.stderr)
                return 1

        if preset_name is not None:
            preset_file = TEMPLATES_ROLES / preset_name / "preset.toml"
            try:
                preset_data = tomllib.loads(preset_file.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"오류: 프리셋 파일 '{preset_file}' 파싱 실패: {e}", file=sys.stderr)
                return 1
            preset_agents = preset_data.get("agents", {})
            preset_assign = preset_data.get("assign", {})
            if not isinstance(preset_agents, dict) or not isinstance(preset_assign, dict):
                print(f"오류: 프리셋 '{preset_name}' 의 설정 형식이 올바르지 않습니다", file=sys.stderr)
                return 1

        # 2단계: 추가할 에이전트
        current_agents = dict(existing_agents)
        if preset_name:
            current_agents.update(preset_agents)

        print("현재 에이전트 목록:")
        for aname, ainfo in sorted(current_agents.items()):
            fam = ainfo.get("family", "") if isinstance(ainfo, dict) else ""
            print(f"  - {aname}: {fam}")

        user_new_agents: dict[str, dict[str, str]] = {}
        while True:
            name_line = safe_input("추가할 에이전트 이름 (빈 줄=끝): ")
            if name_line == "":
                break
            if not re.fullmatch(r"[A-Za-z0-9_-]+", name_line):
                print(f"오류: 새 에이전트 이름 '{name_line}' 형식이 올바르지 않습니다 (^[A-Za-z0-9_-]+$)", file=sys.stderr)
                return 1

            fam_line = safe_input(f"'{name_line}' 의 계열: ")
            if not re.fullmatch(r"[A-Za-z0-9_-]+", fam_line):
                print(f"오류: 새 에이전트 계열 '{fam_line}' 형식이 올바르지 않습니다 (^[A-Za-z0-9_-]+$)", file=sys.stderr)
                return 1

            all_so_far = dict(existing_agents)
            if preset_name:
                all_so_far.update(preset_agents)
            all_so_far.update(user_new_agents)

            if name_line in all_so_far:
                existing_fam = all_so_far[name_line].get("family")
                if existing_fam != fam_line:
                    print(f"오류: 이미 등록된 에이전트 '{name_line}' 의 계열 '{existing_fam}' 과 다른 계열 '{fam_line}' 으로 추가할 수 없습니다", file=sys.stderr)
                    return 1
                # M2: 같은 계열이면 아무것도 안 함
            else:
                user_new_agents[name_line] = {"family": fam_line}

        # 3단계: 역할마다 담당
        role_order = get_role_order(roles_data)
        assigned_roles: dict[str, str] = {}
        for r in role_order:
            default_agent = preset_assign.get(r, "") if preset_name else ""
            hint = f" [{default_agent}]" if default_agent else " [배정 안 함]"
            ans = safe_input(f"역할 '{r}' 담당{hint}: ")
            if ans == "":
                if default_agent:
                    assigned_roles[r] = default_agent
            elif ans == "-":
                pass  # 배정 안 함
            else:
                if not re.fullmatch(r"[A-Za-z0-9_-]+", ans):
                    print(f"오류: 역할 담당 에이전트 '{ans}' 형식이 올바르지 않습니다 (^[A-Za-z0-9_-]+$)", file=sys.stderr)
                    return 1
                assigned_roles[r] = ans

        # 4단계: 슬라이스 ID
        slice_line = safe_input("슬라이스 ID (빈 줄=배정 파일 변경 안 함): ")
        if slice_line == "":
            slice_id = None
        else:
            if not re.fullmatch(r"[A-Za-z0-9_-]+", slice_line):
                print(f"오류: 슬라이스 ID '{slice_line}' 형식이 올바르지 않습니다 (^[A-Za-z0-9_-]+$)", file=sys.stderr)
                return 1
            slice_id = slice_line

        # M1: 확인 단계 전에 검사 (가상 프리셋)
        all_virtual_agents = dict(preset_agents) if preset_name else {}
        all_virtual_agents.update(user_new_agents)

        errors = validate_roles_setup(
            roles_data=roles_data,
            existing_agents=existing_agents,
            existing_slices=existing_slices,
            virtual_agents=all_virtual_agents,
            assigned_roles=assigned_roles,
            slice_id=slice_id,
        )
        if errors:
            for err in errors:
                print(f"오류: {err}", file=sys.stderr)
            return 1

        # 5단계: 확인
        print("\n=== 적용 요약 ===")
        to_add = {k: v for k, v in all_virtual_agents.items() if k not in existing_agents}
        if to_add:
            print(f"추가할 에이전트: {', '.join(to_add.keys())}")
        else:
            print("추가할 에이전트: 없음")
        print(f"역할 배정: {assigned_roles}")
        print(f"슬라이스: {slice_id if slice_id else '(없음)'}")
        print("분리 제약 검사: 통과")

        confirm = safe_input("위 설정을 적용하시겠습니까? (y/N): ")
        if confirm.strip().lower() != "y":
            print("적용 안 함")
            return 0

        # M3: 안내 문서 처리
        write_docs = False
        if preset_name is not None:
            if assigned_roles == preset_assign:
                write_docs = True
            else:
                print("배정이 프리셋과 달라 안내 문서는 쓰지 않음")

        return execute_planned_writes(
            preset_name=preset_name,
            all_virtual_agents=all_virtual_agents,
            existing_agents=existing_agents,
            assigned_roles=assigned_roles,
            slice_id=slice_id,
            write_docs=write_docs,
            dry_run=dry_run,
        )

    except EOFError:
        return 1
    except Exception as e:
        print(f"오류: {e}", file=sys.stderr)
        return 1


def main() -> int:
    try:
        ap = argparse.ArgumentParser(description="역할 프리셋 적용 및 대화형 마법사")
        ap.add_argument("--list", action="store_true", help="프리셋 목록 보기")
        ap.add_argument("--preset", help="적용할 역할 프리셋 이름")
        ap.add_argument("--slice", help="슬라이스 ID (지정하면 assignments.toml 에 배정 추가)")
        ap.add_argument("--dry-run", action="store_true", help="바뀔 내용만 출력하고 파일을 바꾸지 않음")
        ap.add_argument("--ask", action="store_true", help="대화형 마법사로 역할 설정")
        ap.add_argument("--activate", action="store_true", help="역할 상태 활성화 (WO-H63)")
        ap.add_argument("--handover", action="store_true", help="진행 중 lead 교체 (WO-H64)")
        ap.add_argument("--approval-ref", help="승인 참조 (<commit>:<path>#<id>)")
        ap.add_argument("--recover", action="store_true", help="실패/중단된 상태 복구 활성화")
        ap.add_argument("--check", action="store_true", help="현재 역할 상태 정합성 검사")
        args = ap.parse_args()

        if args.check:
            import role_state
            return role_state.check(ROOT)

        if args.activate:
            if not args.preset:
                print("오류: --activate 에는 --preset <이름> 이 필수입니다", file=sys.stderr)
                return 1
            if not args.approval_ref:
                print("오류: --activate 에는 --approval-ref <참조> 가 필수입니다", file=sys.stderr)
                return 1
            import role_state
            return role_state.activate(
                root=ROOT,
                preset_name=args.preset,
                approval_ref=args.approval_ref,
                dry_run=args.dry_run,
                recover=args.recover,
                handover=args.handover,
            )

        if args.list:
            return list_presets()

        state_file = ROOT / "config" / "role-state.json"
        if state_file.is_file() and (args.preset or args.ask):
            print(
                "오류: 활성 역할 상태(config/role-state.json)가 존재합니다. "
                "레거시 --preset/--ask 쓰기 우회가 금지됩니다. "
                "--activate --approval-ref 를 사용하십시오.",
                file=sys.stderr,
            )
            return 1

        if args.ask:
            return ask_wizard(dry_run=args.dry_run)
        if not args.preset:
            ap.print_help()
            return 1
        return apply_preset(args.preset, args.slice, args.dry_run)
    except Exception as e:
        print(f"오류: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
