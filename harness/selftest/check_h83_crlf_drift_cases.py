"""WO-H83 cases: role-state managed-section check ignores CRLF-only differences.

Run: python harness/selftest/check_h83_crlf_drift_cases.py
Copied repositories only, never mutate ROOT. The approval fixture is synthetic.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("h63_cases", HERE / "check_role_transition_cases.py")
h63 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h63)

PY = sys.executable
BEGIN_ROLES = b"<!-- harness:roles:begin -->"
BEGIN_GUIDE = b"<!-- harness:role-guide:begin -->"
END_GUIDE = b"<!-- harness:role-guide:end -->"


def to_crlf(b: bytes) -> bytes:
    return b.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")


class CrlfDriftCases(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="h83-crlf-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "repo"
        self.root.mkdir()
        h63.fixture(self.root)
        self.ref = h63.git(self.root, "rev-parse", "HEAD") + ":reports/role-approvals.json#A1"
        p = h63.command(self.root, PY, "-B", "harness/roles.py", "--preset", "codex-lead",
                        "--activate", "--approval-ref", self.ref)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.check().returncode, 0, "precondition: clean check after activation")

    def check(self):
        return h63.command(self.root, PY, "-B", "harness/roles.py", "--check")

    def rewrite(self, rel, fn):
        p = self.root / rel
        p.write_bytes(fn(p.read_bytes()))

    def assert_clean(self):
        before = h63.snap(self.root)
        p = self.check()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(h63.snap(self.root), before, "--check must not write")

    def assert_drift(self, expect=None):
        before = h63.snap(self.root)
        p = self.check()
        out = p.stdout + p.stderr
        self.assertNotEqual(p.returncode, 0, out)
        self.assertEqual(h63.snap(self.root), before, "--check must not write on rejection")
        if expect:
            self.assertIn(expect, out)
        return out

    # --- CRLF-only differences are not drift (Red before the fix) ---

    def test_c01_handoff_whole_file_crlf(self):
        self.rewrite("docs/HANDOFF.md", to_crlf)
        self.assert_clean()

    def test_c02_agents_whole_file_crlf(self):
        self.rewrite("AGENTS.md", to_crlf)
        self.assert_clean()

    def test_c03_claude_whole_file_crlf(self):
        self.rewrite("CLAUDE.md", to_crlf)
        self.assert_clean()

    def test_c04_all_managed_docs_crlf(self):
        for rel in ("docs/HANDOFF.md", "AGENTS.md", "CLAUDE.md"):
            self.rewrite(rel, to_crlf)
        self.assert_clean()

    def test_c05_mixed_endings_only_inside_section(self):
        def fn(b):
            i = b.index(BEGIN_GUIDE)
            j = b.index(END_GUIDE)
            return b[:i] + to_crlf(b[i:j]) + b[j:]
        self.rewrite("docs/HANDOFF.md", fn)
        self.assert_clean()

    def test_c06_check_is_read_only_on_crlf(self):
        self.rewrite("docs/HANDOFF.md", to_crlf)
        before = h63.snap(self.root)
        self.check()
        self.assertEqual(h63.snap(self.root), before)

    # --- guards: real differences still fail ---

    def test_c07_template_crlf_before_activation_docs_lf(self):
        # Expected side is CRLF (template saved with CRLF), document side is LF.
        # Both sides must be normalized, not only the document.
        tmp = tempfile.TemporaryDirectory(prefix="h83-tpl-")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name) / "repo"
        root.mkdir()
        h63.fixture(root)
        for t in sorted((root / "templates/roles/codex-lead").glob("*.md")):
            t.write_bytes(to_crlf(t.read_bytes()))
        ref = h63.git(root, "rev-parse", "HEAD") + ":reports/role-approvals.json#A1"
        p = h63.command(root, PY, "-B", "harness/roles.py", "--preset", "codex-lead",
                        "--activate", "--approval-ref", ref)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn(b"\r\n", (root / "AGENTS.md").read_bytes(), "precondition: CRLF template written")
        for rel in ("AGENTS.md", "CLAUDE.md"):
            f = root / rel
            f.write_bytes(f.read_bytes().replace(b"\r\n", b"\n"))
        before = h63.snap(root)
        p = h63.command(root, PY, "-B", "harness/roles.py", "--check")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(h63.snap(root), before)

    def test_c08_extract_managed_section_unchanged(self):
        # The shared extractor (also used around activation) keeps CRLF bytes as-is;
        # normalization belongs to the check comparison only.
        sys.path.insert(0, str(self.root / "harness"))
        try:
            import role_state
            got = role_state.extract_managed_section(
                b"pre\r\n" + BEGIN_GUIDE + b"\r\nx\r\ny\r\n" + END_GUIDE + b"\r\n", BEGIN_GUIDE, END_GUIDE)
        finally:
            sys.path.remove(str(self.root / "harness"))
            sys.modules.pop("role_state", None)
        self.assertEqual(got, b"x\r\ny")

    # --- guards: real differences still fail ---

    def test_g01_crlf_plus_content_change_handoff(self):
        self.rewrite("docs/HANDOFF.md", lambda b: to_crlf(b.replace(BEGIN_GUIDE, BEGIN_GUIDE + b"\nInjected line")))
        self.assert_drift("HANDOFF")

    def test_g02_crlf_plus_content_change_agents(self):
        self.rewrite("AGENTS.md", lambda b: to_crlf(b.replace(BEGIN_ROLES, BEGIN_ROLES + b"\nInjected instruction")))
        self.assert_drift("AGENTS.md")

    @staticmethod
    def _mid_line(b, begin):
        # Offset in the middle of the first non-empty line after the marker.
        i = b.index(begin) + len(begin)
        while b[i:i + 1] in (b"\r", b"\n"):
            i += 1
        j = b.index(b"\n", i)
        if b[j - 1:j] == b"\r":
            j -= 1
        assert j - i >= 2, "fixture line too short"
        return i + (j - i) // 2

    def test_g03_lone_cr_is_drift_handoff(self):
        # Only the CRLF pair is a line ending; a bare CR alone inside a line is content.
        def fn(b):
            k = self._mid_line(b, BEGIN_GUIDE)
            return b[:k] + b"\r" + b[k:]
        self.rewrite("docs/HANDOFF.md", fn)
        self.assert_drift("HANDOFF")

    def test_g03b_lone_cr_is_drift_agents(self):
        def fn(b):
            k = self._mid_line(b, BEGIN_ROLES)
            return b[:k] + b"\r" + b[k:]
        self.rewrite("AGENTS.md", fn)
        self.assert_drift("AGENTS.md")

    def test_g04_trailing_space_is_drift(self):
        def fn(b):
            i = b.index(BEGIN_ROLES) + len(BEGIN_ROLES)
            j = b.index(b"\n", i + 1)
            return b[:j] + b"  " + b[j:]
        self.rewrite("CLAUDE.md", fn)
        self.assert_drift("CLAUDE.md")

    def test_g04b_trailing_space_is_drift_handoff(self):
        def fn(b):
            i = b.index(BEGIN_GUIDE) + len(BEGIN_GUIDE)
            j = b.index(b"\n", i + 1)
            return b[:j] + b"  " + b[j:]
        self.rewrite("docs/HANDOFF.md", fn)
        self.assert_drift("HANDOFF")

    def test_g05_template_change_still_detected(self):
        p = self.root / "templates/roles/codex-lead/AGENTS.md"
        p.write_bytes(p.read_bytes() + b"\nChanged template\n")
        self.assert_drift()

    def test_g05b_config_version_alone_still_detected(self):
        # Template and its managed section both become CRLF: section content matches
        # after normalization, so only the config_version hash comparison can reject.
        self.rewrite("templates/roles/codex-lead/AGENTS.md", to_crlf)
        self.rewrite("AGENTS.md", to_crlf)
        out = self.assert_drift("config_version")
        self.assertNotIn("drift)", out.replace("config_version", ""))

    def test_g06_crlf_lead_mismatch_still_detected(self):
        # Swap lead name inside the CRLF guide: the content differs, not only endings.
        state = json.loads((self.root / "config/role-state.json").read_text(encoding="utf-8"))
        lead = state["lead"].encode()
        self.rewrite("docs/HANDOFF.md", lambda b: to_crlf(b.replace(b"lead: " + lead, b"lead: other")))
        self.assert_drift("HANDOFF")

    def test_g06b_crlf_state_line_lead_conflict(self):
        # Guide section untouched; only the HANDOFF state line names another lead.
        self.rewrite("docs/HANDOFF.md", lambda b: to_crlf(
            b.replace("상태: 기존 작업 종료".encode(), "상태: 구현 전 · lead=claude".encode())))
        self.assert_drift("lead")

    def test_g07_crlf_in_handoff_outside_section_with_duplicate_marker(self):
        self.rewrite("docs/HANDOFF.md", lambda b: to_crlf(b + b"\n" + BEGIN_GUIDE + b"\n"))
        self.assert_drift("begin=2")


if __name__ == "__main__":
    unittest.main()
