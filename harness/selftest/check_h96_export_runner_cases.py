"""WO-H96: 배포 러너·호환 진입점과 제외 경로 검사 회귀."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class ExportRunner(unittest.TestCase):
    def test_export_parallel_progress_and_failure(self):
        if not (ROOT / "public/export.toml").is_file():
            self.skipTest("public/export.toml absent: private export integration")
        sys.path.insert(0, str(ROOT / "harness/selftest"))
        import check_export_cases as export
        import shutil
        import os
        import stat

        def remove_readonly(func, path, exc):
            os.chmod(path, stat.S_IWRITE)
            func(path)
        for fail in (False, True):
            extra = {"harness/selftest_runner.py": (ROOT / "harness/selftest_runner.py").read_text(encoding="utf-8"),
                     "harness/selftest_weights.json": "{}\n"}
            if fail:
                extra["harness/selftest/check_bad.py"] = "raise SystemExit(3)\n"
            repo = export.make_private(extra)
            out = export.empty_out()
            try:
                p = export.export(repo, out)
                self.assertEqual(p.returncode, int(fail), p.stdout + p.stderr)
                self.assertIn("DONE", p.stdout)
                self.assertIn("check_ok_cases ... OK", p.stdout)
                self.assertFalse(list(out.rglob("*.out")))
                self.assertFalse(list(out.rglob("selftest-times.json")))
                if fail:
                    self.assertIn("check_bad ... FAIL(3)", p.stdout)
            finally:
                for target in (repo, out.parent):
                    target.resolve().relative_to(Path(tempfile.gettempdir()).resolve())
                    shutil.rmtree(target, onerror=remove_readonly)

    def test_shipped_runner_and_seed(self):
        self.assertTrue((ROOT / "harness/selftest_weights.json").is_file())
        with tempfile.TemporaryDirectory(prefix="h96-run-") as td:
            root = Path(td)
            tests = root / "harness/selftest"
            tests.mkdir(parents=True)
            (tests / "check_pass.py").write_text("print('1/1 통과')\n", encoding="utf-8")
            runner = ROOT / "harness/selftest_runner.py"
            p = subprocess.run([sys.executable, str(runner), "--root", td, "--log-dir", str(root / "logs")],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertIn("DONE 1/1", p.stdout)
            wrapper = ROOT / "scripts/run_suite_parallel.py"
            if wrapper.is_file():
                q = subprocess.run([sys.executable, str(wrapper), "--root", td, "--log-dir", str(root / "logs")],
                                   capture_output=True, text=True, encoding="utf-8", errors="replace")
                self.assertEqual(q.returncode, 0, q.stdout + q.stderr)
                self.assertIn("DONE 1/1", q.stdout)
            else:
                print("SKIP compatibility wrapper absent")

    def test_excluded_reference_guards(self):
        spec = importlib.util.spec_from_file_location("export_refs", ROOT / "harness/selftest/check_export_refs_cases.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        cfg = {"include": ["harness/**", "docs/**"], "public_root": "public"}
        for source in ('P = ROOT / "scripts" / "run_suite_parallel.py"',
                       'P = ROOT / "harness" / "selftest" / "check_export_cases.py"',
                       'P = "dev/WO-example.md"', 'P = "public/export.toml"'):
            self.assertTrue(mod.inspect_source(source, cfg), source)
            guarded = source + '\nif not P.is_file():\n    print("SKIP missing")\n'
            self.assertFalse(mod.inspect_source(guarded, cfg), guarded)
        self.assertTrue(mod.inspect_source('P = "scripts/run.py"\nprint("SKIP")', cfg))
        self.assertFalse(mod.inspect_source('P = ROOT / "harness" / "selftest_runner.py"', cfg))


if __name__ == "__main__":
    unittest.main()
