"""작업 공간 내용 정책 및 계획 생성기 (WO-H25, HD-29).

지정된 기준 커밋(base)에서 정책 설정(기본: config/workspace.toml)을 읽고,
프로필에 따라 제공할 파일 목록(path, blob, mode)을 결정하는 읽기 전용 최소 계획을 생성한다.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class WorkspaceArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        sys.stderr.write(f"error: {message}\n")
        sys.exit(2)


def is_valid_exact_path(p: str) -> bool:
    if not isinstance(p, str) or not p:
        return False
    if "\\" in p or ":" in p:
        return False
    if p.startswith("/") or p.endswith("/"):
        return False
    if any(ch in p for ch in ("*", "?", "[", "]")):
        return False
    parts = p.split("/")
    for part in parts:
        if part == "" or part == "." or part == "..":
            return False
        if part.lower() == ".git":
            return False
    return True


def is_valid_pattern(p: str) -> bool:
    if not isinstance(p, str) or not p:
        return False
    if p.endswith("/**"):
        dir_part = p[:-3]
        return is_valid_exact_path(dir_part)
    return is_valid_exact_path(p)


def pattern_matches(pattern: str, file_path: str) -> bool:
    if pattern.endswith("/**"):
        dir_prefix = pattern[:-3]
        return file_path.startswith(dir_prefix + "/")
    return file_path == pattern


def plan_subcommand(args: argparse.Namespace) -> int:
    config_path = args.config
    if not is_valid_exact_path(config_path):
        sys.stderr.write(f"error: invalid config path: '{config_path}'\n")
        return 2

    # 1. Resolve base commit-ish (must resolve to commit)
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", f"{args.base}^{{commit}}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        sys.stderr.write(f"error: cannot resolve base commit '{args.base}': {proc.stderr.strip()}\n")
        return 2
    base_sha = proc.stdout.strip()

    # 2. Verify and read policy file from base_sha in Git
    type_proc = subprocess.run(
        ["git", "cat-file", "-t", f"{base_sha}:{config_path}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if type_proc.returncode != 0 or type_proc.stdout.strip() != "blob":
        sys.stderr.write(f"error: config '{config_path}' not found as blob in commit {base_sha}\n")
        return 2

    blob_proc = subprocess.run(
        ["git", "rev-parse", "--verify", f"{base_sha}:{config_path}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if blob_proc.returncode != 0:
        sys.stderr.write(f"error: cannot resolve blob id for '{config_path}' in commit {base_sha}\n")
        return 2
    policy_blob = blob_proc.stdout.strip()

    content_proc = subprocess.run(
        ["git", "cat-file", "blob", policy_blob],
        cwd=ROOT,
        capture_output=True,
    )
    if content_proc.returncode != 0:
        sys.stderr.write(f"error: cannot read config blob {policy_blob}\n")
        return 2

    try:
        policy_text = content_proc.stdout.decode("utf-8")
    except UnicodeDecodeError as e:
        sys.stderr.write(f"error: config file is not valid UTF-8: {e}\n")
        return 2

    # 3. Parse TOML and validate schema
    try:
        data = tomllib.loads(policy_text)
    except Exception as e:
        sys.stderr.write(f"error: failed to parse config TOML: {e}\n")
        return 2

    if not isinstance(data, dict):
        sys.stderr.write("error: config TOML root must be a table\n")
        return 2

    if set(data.keys()) != {"schema", "profiles"}:
        sys.stderr.write(f"error: unexpected top-level keys in config: {sorted(data.keys())}\n")
        return 2

    schema_val = data.get("schema")
    if type(schema_val) is not int or isinstance(schema_val, bool) or schema_val != 1:
        sys.stderr.write(f"error: schema must be integer 1, got {schema_val!r} of type {type(schema_val).__name__}\n")
        return 2

    profiles_table = data.get("profiles")
    if not isinstance(profiles_table, dict):
        sys.stderr.write("error: profiles must be a table\n")
        return 2

    for prof_name, prof_data in profiles_table.items():
        if not isinstance(prof_name, str) or not isinstance(prof_data, dict):
            sys.stderr.write(f"error: profile '{prof_name}' must be a table\n")
            return 2
        if set(prof_data.keys()) != {"include", "exclude", "required"}:
            sys.stderr.write(f"error: profile '{prof_name}' keys must be exactly include, exclude, required\n")
            return 2
        for field in ("include", "exclude", "required"):
            val = prof_data[field]
            if not isinstance(val, list):
                sys.stderr.write(f"error: profile '{prof_name}'.{field} must be a list\n")
                return 2
            for item in val:
                if not isinstance(item, str):
                    sys.stderr.write(f"error: profile '{prof_name}'.{field} item must be a string: {item!r}\n")
                    return 2
        if len(prof_data["include"]) == 0:
            sys.stderr.write(f"error: profile '{prof_name}'.include cannot be empty\n")
            return 2
        for pat in prof_data["include"]:
            if not is_valid_pattern(pat):
                sys.stderr.write(f"error: invalid pattern in profile '{prof_name}'.include: '{pat}'\n")
                return 2
        for pat in prof_data["exclude"]:
            if not is_valid_pattern(pat):
                sys.stderr.write(f"error: invalid pattern in profile '{prof_name}'.exclude: '{pat}'\n")
                return 2
        for req in prof_data["required"]:
            if not is_valid_exact_path(req):
                sys.stderr.write(f"error: invalid path in profile '{prof_name}'.required: '{req}'\n")
                return 2

    if args.profile not in profiles_table:
        sys.stderr.write(f"error: profile '{args.profile}' not found in config\n")
        return 2

    # 4. Read Git tree at base_sha
    tree_proc = subprocess.run(
        ["git", "ls-tree", "-r", "-z", base_sha],
        cwd=ROOT,
        capture_output=True,
    )
    if tree_proc.returncode != 0:
        sys.stderr.write(f"error: git ls-tree failed: {tree_proc.stderr.decode('utf-8', 'replace')}\n")
        return 2

    tree_entries = []
    for item in tree_proc.stdout.split(b"\0"):
        if not item:
            continue
        try:
            text = item.decode("utf-8")
        except UnicodeDecodeError as e:
            sys.stderr.write(f"error: tree path decode error: {e}\n")
            return 2
        meta, sep, path = text.partition("\t")
        if not sep:
            continue
        parts = meta.split(" ")
        if len(parts) != 3:
            continue
        mode, type_, blob = parts
        tree_entries.append({"mode": mode, "type": type_, "blob": blob, "path": path})

    # 5. Filter files for active profile
    active_profile = profiles_table[args.profile]
    include_patterns = active_profile["include"]
    exclude_patterns = active_profile["exclude"]
    required_paths = active_profile["required"]

    selected_files = []
    for entry in tree_entries:
        path = entry["path"]
        inc = any(pattern_matches(pat, path) for pat in include_patterns)
        if not inc:
            continue
        exc = any(pattern_matches(pat, path) for pat in exclude_patterns)
        if exc:
            continue

        mode = entry["mode"]
        if mode not in ("100644", "100755"):
            sys.stderr.write(f"error: selected special entry (mode {mode}): {path}\n")
            return 2

        selected_files.append({
            "path": path,
            "blob": entry["blob"],
            "mode": mode,
        })

    if len(selected_files) == 0:
        sys.stderr.write("error: final selected file list is empty\n")
        return 2

    selected_path_set = {f["path"] for f in selected_files}
    for req in required_paths:
        if req not in selected_path_set:
            sys.stderr.write(f"error: required path '{req}' missing from selection\n")
            return 2

    selected_files.sort(key=lambda x: x["path"])

    result = {
        "schema": 1,
        "base_sha": base_sha,
        "profile": args.profile,
        "policy_path": config_path,
        "policy_blob": policy_blob,
        "files": selected_files,
    }

    sys.stdout.write(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    return 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", newline="\n")
        except Exception:
            pass
    if hasattr(sys.stderr, "reconfigure"):
        try:
            sys.stderr.reconfigure(encoding="utf-8", newline="\n")
        except Exception:
            pass

    parser = WorkspaceArgumentParser(prog="workspace.py", description="Workspace policy and planning")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    plan_parser = subparsers.add_parser("plan", help="Generate workspace plan")
    plan_parser.add_argument("--base", required=True, help="Base commit-ish")
    plan_parser.add_argument("--profile", required=True, help="Profile name")
    plan_parser.add_argument("--config", default="config/workspace.toml", help="Policy configuration path")

    args = parser.parse_args()
    try:
        if args.subcommand == "plan":
            return plan_subcommand(args)
        sys.stderr.write(f"error: unknown subcommand '{args.subcommand}'\n")
        return 2
    except Exception as e:
        sys.stderr.write(f"error: {e}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
