"""WO-H84 fixture helpers: harness selftests run safely in consumer repositories."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "templates" / "config"
HARNESS_TOP = ("harness", "adapters", "templates", "docs")
ROOT_FILES = (".gitattributes", ".gitignore")


def harness_files(root: Path = ROOT) -> list[str]:
    root_path = Path(root)
    p = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard"],
        cwd=root_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    out: list[str] = []
    for line in p.stdout.splitlines():
        rel = line.strip().replace("\\", "/")
        if not rel:
            continue
        if "__pycache__" in rel or rel == "harness/UPSTREAM" or rel.startswith("harness/UPSTREAM/"):
            continue
        top = rel.split("/", 1)[0]
        if top in HARNESS_TOP or rel in ROOT_FILES:
            out.append(rel)
    return out


def copy_default_config(dst_config_dir: Path | str, default_config: Path = DEFAULT_CONFIG) -> None:
    dst_dir = Path(dst_config_dir)
    dst_dir.mkdir(parents=True, exist_ok=True)
    cfg_src = Path(default_config)
    for p in sorted(cfg_src.glob("*.toml")):
        if p.is_file():
            shutil.copy2(p, dst_dir / p.name)


def copy_harness_tree(dst: Path | str, root: Path = ROOT) -> None:
    dst_path = Path(dst)
    root_path = Path(root)
    for rel in harness_files(root_path):
        src = root_path / rel
        if src.is_file():
            target = dst_path / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
    cfg_src = root_path / "templates" / "config"
    if not cfg_src.is_dir():
        cfg_src = DEFAULT_CONFIG
    copy_default_config(dst_path / "config", default_config=cfg_src)
