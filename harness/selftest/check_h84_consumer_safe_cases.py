"""WO-H84 cases: harness selftests also pass inside a consumer-shaped repository.

Run: python harness/selftest/check_h84_consumer_safe_cases.py
Builds a synthetic consumer from this repository's files (project-owned config,
active role state, no dev/ · public/ · export tool, an upgrade record), then runs
the selftests that used to fail in consumer projects from inside it.
Never mutates ROOT.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable
TEMPLATE_CONFIG = ROOT / "templates" / "config"
CONFIG_FILES = ["agents.toml", "architecture.toml", "assignments.toml", "gates.toml",
                "roles.toml", "slices.toml", "workspace.toml"]
TARGETS = ["check_project_cases", "check_roles_wizard_cases", "check_sdd_flow",
           "check_template_defaults_cases", "check_upgrade_cases", "check_upgrade_docs_cases",
           "check_upgrade_history_cases", "check_v04_cases"]
SKIP_TOP = {"dev", "public", ".jobs", "reports"}
# Cases that failed in real consumer projects; each must now run and pass (not be skipped).
MUST_PASS = {
    "check_project_cases": ["N08b", "R10", "R03a", "R04", "R05b", "R06a", "R06b"],
    "check_roles_wizard_cases": ["W01", "W02", "W04", "W08", "W13"],
    "check_sdd_flow": ["F02", "F04", "A01", "A04"],
    "check_template_defaults_cases": ["H01-01"],
    "check_upgrade_cases": ["U02", "U05"],
    "check_upgrade_docs_cases": ["U01", "U06"],
    "check_upgrade_history_cases": ["U10", "U11"],
    "check_v04_cases": ["F08"],
}
ALLOWED_TOP = {"harness", "adapters", "templates", "docs", "config", ".gitattributes", ".gitignore"}
PROBE = r'''
import json, sys, tempfile
from pathlib import Path
sys.path.insert(0, "harness/selftest")
out = {}
import check_project_cases, check_upgrade_cases
for name, mod in (("project", check_project_cases), ("upgrade", check_upgrade_cases)):
    base = Path(tempfile.mkdtemp(prefix="h84-probe-"))
    up = mod.make_upstream(base)
    files = sorted(p.relative_to(up).as_posix() for p in up.rglob("*") if p.is_file() and ".git" not in p.relative_to(up).parts)
    cfg = {p.name: p.read_bytes().hex() for p in (up / "config").glob("*") if p.is_file()}
    out[name] = {"files": files, "config": cfg}
import check_sdd_flow
flow = check_sdd_flow.Flow()
flow.setup()
d = flow.dir
out["sdd"] = {"files": sorted(p.relative_to(d).as_posix() for p in d.rglob("*") if p.is_file() and ".git" not in p.relative_to(d).parts),
              "agents": (d / "AGENTS.md").read_text(encoding="utf-8") if (d / "AGENTS.md").is_file() else "",
              "events": (d / "reports/events.jsonl").read_text(encoding="utf-8") if (d / "reports/events.jsonl").is_file() else ""}
print("PROBE" + json.dumps(out))
'''


def sh(cwd, *args, check=True):
    p = subprocess.run(list(args), cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and p.returncode:
        raise RuntimeError(f"{args}: {p.stdout}{p.stderr}")
    return p


def is_harness_dev_repo(root: Path) -> bool:
    return (root / "public" / "export.toml").is_file()


def project_gates(text: str) -> str:
    """A consumer's own gates.toml: no [env] table, no feedback gates (like a JS project)."""
    text = re.sub(r"(?ms)^\[env\]\n.*?(?=^\[)", "", text)
    return re.sub(r"(?m)^feedback_gates\s*=.*$", "feedback_gates = []", text)


def build_consumer(dst: Path) -> None:
    files = sh(ROOT, "git", "ls-files", "-co", "--exclude-standard").stdout.split("\n")
    for rel in filter(None, files):
        top = rel.split("/", 1)[0]
        if top in SKIP_TOP or rel == "Instruction.md" or "__pycache__" in rel:
            continue
        if rel.startswith("scripts/"):
            continue
        src = ROOT / rel
        if src.is_file():
            (dst / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst / rel)
    cfg = dst / "config"
    (cfg / "gates.toml").write_text(project_gates((TEMPLATE_CONFIG / "gates.toml").read_text(encoding="utf-8")),
                                    encoding="utf-8")
    (cfg / "agents.toml").write_text(
        (TEMPLATE_CONFIG / "agents.toml").read_text(encoding="utf-8") + '\n[agents.local]\nfamily = "local"\n',
        encoding="utf-8")
    (cfg / "role-state.json").write_text(
        '{"schema": 1, "generation": 3, "phase": "ready", "preset": "claude-lead", "lead": "claude"}\n',
        encoding="utf-8")
    (cfg / "test_lock.json").write_text('{"locked": {}}\n', encoding="utf-8")
    # Built at run time: a 40-hex literal in source trips the public export leak check.
    (dst / "harness" / "UPSTREAM").write_text("1" * 40 + "\n", encoding="utf-8")
    # Project-owned files a real consumer has and the harness repository does not.
    project_files = {
        "scripts/dev-server.sh": "#!/bin/sh\necho project tool\n",
        "src/app.js": "export const app = 1;\n",
        "src/library/__init__.py": "",
        "src/library/core.py": "def shelve(book):\n    return missing_helper(book)\n",
        "tests/unit/test_library.py": "from library.core import shelve\n\n\ndef test_shelve():\n    assert shelve('b') == 'b'\n",
        "specs/900-project/spec.md": "# Project feature\n\n- FR-901: 시스템은 책을 서가에 둔다.\n- FR-902: 시스템은 대출을 기록한다.\n",
        ".specify/memory/constitution.md": "# Project constitution\n\nproject-specific rules\n",
        "pyproject.toml": '[project]\nname = "library-tracker"\nversion = "0.1.0"\n',
        "work_orders/WO-1.md": "# WO-1 project work order\n",
        "reports/events.jsonl": '{"ts": "2026-10-01T00:00:00Z", "event": "gate_run", "wo": "WO-1", "passed": false, "gates": {"G2": false}}\n',
        "AGENTS.md": "Project agents\n\n<!-- harness:roles:begin -->\nproject roles\n<!-- harness:roles:end -->\n",
        "CLAUDE.md": "Project claude\n\n<!-- harness:roles:begin -->\nproject roles\n<!-- harness:roles:end -->\n",
        "PROMPT.md": "project prompt\n",
        "README.md": "# Library tracker (project README)\n",
        "docs/PRODUCT.md": "# Project product\n\nproject-owned product description\n",
    }
    for rel, text in project_files.items():
        (dst / rel).parent.mkdir(parents=True, exist_ok=True)
        (dst / rel).write_text(text, encoding="utf-8")
    with (cfg / "assignments.toml").open("a", encoding="utf-8") as f:
        f.write('\n[slice.PROJ-1]\nstatus = "open"\nspec_author = "claude"\ntest_author = "claude"\n'
                'implementer = "local"\nadversary = "codex"\nmutation_auditor = "codex"\nreason = "project slice"\n')
    sh(dst, "git", "init", "-q")
    sh(dst, "git", "add", "-A")
    sh(dst, "git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "consumer")


class ConsumerSafeCases(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="h84-consumer-")
        cls.consumer = Path(cls.tmp.name) / "consumer"
        cls.consumer.mkdir()
        build_consumer(cls.consumer)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_in_consumer(self, name, root=None):
        root = root or self.consumer
        return subprocess.run([PY, "-B", f"harness/selftest/{name}.py"], cwd=root, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=1800,
                              env=dict(os.environ))

    # --- shipped defaults ---

    def test_s00_template_config_shipped(self):
        got = sorted(p.name for p in TEMPLATE_CONFIG.glob("*") if p.is_file())
        self.assertEqual(got, sorted(CONFIG_FILES))

    def test_s01_template_config_matches_harness_config(self):
        if not is_harness_dev_repo(ROOT):
            self.skipTest("하네스 개발 저장소에서만 동기 검사")
        for name in CONFIG_FILES:
            self.assertEqual((TEMPLATE_CONFIG / name).read_bytes(), (ROOT / "config" / name).read_bytes(), name)

    def test_s02_fixture_precondition(self):
        c = self.consumer
        self.assertTrue((c / "config/role-state.json").is_file())
        self.assertNotIn("[env]", (c / "config/gates.toml").read_text(encoding="utf-8"))
        self.assertFalse((c / "public").exists())
        self.assertFalse((c / "scripts/export_public.py").exists())
        self.assertTrue((c / "harness/UPSTREAM").is_file())

    # --- previously failing selftests now pass inside a consumer ---

    def snapshot(self):
        c = self.consumer
        return {p.relative_to(c).as_posix(): p.read_bytes() for p in c.rglob("*")
                if p.is_file() and ".git" not in p.relative_to(c).parts and "__pycache__" not in p.parts}

    def _assert_passes(self, name):
        before = self.snapshot()
        p = self.run_in_consumer(name)
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 0, f"{name}\n{out[-3000:]}")
        lines = out.splitlines()
        self.assertFalse(any(l.startswith("SKIP") for l in lines), f"{name} must run, not skip")
        for case_id in MUST_PASS[name]:
            self.assertTrue(any(re.match(rf"^(OK|PASS)\s+{re.escape(case_id)}\s", l) for l in lines),
                            f"{name}: case {case_id} did not run and pass\n{out[-2000:]}")
        self.assertEqual(self.snapshot(), before, f"{name} changed the consumer repository")

    # --- fixture copies only what the harness gives a new project ---

    @classmethod
    def probe(cls):
        if not hasattr(cls, "_probe"):
            tmp = Path(cls.tmp.name) / "probe.py"
            tmp.write_text(PROBE, encoding="utf-8")
            p = subprocess.run([PY, "-B", str(tmp)], cwd=cls.consumer, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=900)
            line = next((l for l in p.stdout.splitlines() if l.startswith("PROBE")), None)
            if line is None:
                raise AssertionError(f"probe failed\n{(p.stdout + p.stderr)[-3000:]}")
            import json
            cls._probe = json.loads(line[len("PROBE"):])
        return cls._probe

    def test_f01_upstream_copies_only_harness_paths(self):
        # check_project_cases additionally plants fixed upstream-only files (see G03).
        planted = {"README.md", "HARNESS_STATUS.md", "HARNESS_QA.md", "dev/NOTE.md"}
        for name in ("project", "upgrade"):
            files = self.probe()[name]["files"]
            extra = planted if name == "project" else set()
            bad = [f for f in files if f.split("/", 1)[0] not in ALLOWED_TOP and f not in extra]
            self.assertEqual(bad, [], f"{name} upstream copied project-owned files")

    def test_g03_project_upstream_keeps_exclusion_targets(self):
        # new_project must still be tested against harness-only files (N01c · N01d),
        # and the upstream README must not be the consumer's own README.
        files = set(self.probe()["project"]["files"])
        for rel in ("README.md", "HARNESS_STATUS.md", "dev/NOTE.md"):
            self.assertIn(rel, files)

    def test_f02_upstream_config_is_shipped_default(self):
        want = {n: (TEMPLATE_CONFIG / n).read_bytes().hex() for n in CONFIG_FILES} if TEMPLATE_CONFIG.is_dir() else None
        for name in ("project", "upgrade"):
            self.assertEqual(self.probe()[name]["config"], want, f"{name} upstream config/")

    def test_f03_sdd_flow_repo_has_no_project_files(self):
        sdd = self.probe()["sdd"]
        leaked = [f for f in sdd["files"] if f.startswith(("specs/900-project", "src/library", "work_orders/",
                                                          "scripts/", "PROMPT.md", "tests/unit/test_library.py"))
                  or f in ("config/role-state.json", "config/test_lock.json")]
        self.assertEqual(leaked, [])
        self.assertNotIn("Project agents", sdd["agents"])
        self.assertNotIn("WO-1", sdd["events"])

    def test_c01_project_cases(self):
        self._assert_passes("check_project_cases")

    def test_c02_roles_wizard(self):
        self._assert_passes("check_roles_wizard_cases")

    def test_c03_sdd_flow(self):
        self._assert_passes("check_sdd_flow")

    def test_c04_template_defaults(self):
        self._assert_passes("check_template_defaults_cases")

    def test_c05_upgrade(self):
        self._assert_passes("check_upgrade_cases")

    def test_c06_upgrade_docs(self):
        self._assert_passes("check_upgrade_docs_cases")

    def test_c07_upgrade_history(self):
        self._assert_passes("check_upgrade_history_cases")

    def test_c08_v04(self):
        self._assert_passes("check_v04_cases")

    def test_c09_export_skips_in_consumer(self):
        p = self.run_in_consumer("check_export_cases")
        out = p.stdout + p.stderr
        self.assertEqual(p.returncode, 0, out[-2000:])
        self.assertTrue(any(line.startswith("SKIP") for line in out.splitlines()), out[-2000:])

    # --- guards ---

    def test_g01_export_half_missing_still_fails(self):
        # Export config present but tool missing: a broken harness repo, not a consumer.
        tmp = tempfile.TemporaryDirectory(prefix="h84-half-")
        self.addCleanup(tmp.cleanup)
        half = Path(tmp.name) / "half"
        shutil.copytree(self.consumer, half, ignore=shutil.ignore_patterns(".git"))
        (half / "public").mkdir()
        # A stand-in is enough: only presence decides SKIP, and this suite must also run in consumers.
        (half / "public" / "export.toml").write_text('include = ["harness/**"]\n', encoding="utf-8")
        p = self.run_in_consumer("check_export_cases", root=half)
        self.assertNotEqual(p.returncode, 0, (p.stdout + p.stderr)[-2000:])

    def test_g01b_export_tool_only_still_fails(self):
        # Export tool present but config missing: also a broken harness repo.
        tmp = tempfile.TemporaryDirectory(prefix="h84-half2-")
        self.addCleanup(tmp.cleanup)
        half = Path(tmp.name) / "half"
        shutil.copytree(self.consumer, half, ignore=shutil.ignore_patterns(".git"))
        (half / "scripts" / "export_public.py").write_text("raise SystemExit(2)\n", encoding="utf-8")
        p = self.run_in_consumer("check_export_cases", root=half)
        self.assertNotEqual(p.returncode, 0, (p.stdout + p.stderr)[-2000:])

    def test_g02_export_skip_leaves_consumer_untouched(self):
        before = self.snapshot()
        self.run_in_consumer("check_export_cases")
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
