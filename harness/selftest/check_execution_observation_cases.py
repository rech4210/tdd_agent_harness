"""WO-H69 opt-in execution linkage. Existing watcher/provider fixtures, one real
manual registration shared across scenarios; no real models or Orca launches.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

import check_role_flexibility_cases as flexibility
from check_orca_watch_cases import FAKE_ORCA, H, SCREEN, agy_tr, codex_rollout, files

PY = sys.executable


class ObservationCases(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.owner = flexibility.FlexibilityCases('test_01_manual_control_and_honest_evidence')
        cls.owner.setUp()
        cls.addClassCleanup(cls.owner.doCleanups)
        cls.owner.prepare_manual()
        cls.run_id = cls.owner.manual_start()
        cls.root, cls.worker = cls.owner.root, cls.owner.workspace
        cls.contract_path = cls.root / 'reports/executions' / cls.run_id / 'contract.json'
        cls.contract_bytes = cls.contract_path.read_bytes()
        cls.worker_bytes = files(cls.worker)
        cls.events_path = cls.root / 'reports/events.jsonl'
        cls.initial_events = cls.events_path.read_bytes() if cls.events_path.exists() else b''
        cls.fixture_dir = Path(cls.owner.tmp.name) / 'observation-fixtures'
        cls.fixture_dir.mkdir()
        # Reuse the existing fake CLI, with the installed wait receipt's satisfied field.
        cls.fake = cls.fixture_dir / 'fake_orca.py'
        fake_text = FAKE_ORCA.replace('if (st / "idle").exists():', 'if (st / "unsat").exists():\n        print(json.dumps({"ok": True, "result": {"wait": {"satisfied": False}}})); sys.exit(0)\n    if (st / "idle").exists():')
        cls.fake.write_text(fake_text.replace('{"ok": True}', '{"ok": True, "result": {"wait": {"satisfied": True}}}'), encoding='utf-8')

    def setUp(self):
        self.events_path.write_bytes(self.initial_events)
        self.state = Path(tempfile.mkdtemp(dir=self.fixture_dir, prefix='state-'))
        (self.state / 'screen.txt').write_text(SCREEN, encoding='utf-8')
        self.brain = self.state / 'sessions'
        self.brain.mkdir()

    def tearDown(self):
        self.assertEqual(self.contract_path.read_bytes(), self.contract_bytes)
        self.assertEqual(files(self.worker), self.worker_bytes)
        self.assertTrue(self.events_path.read_bytes().startswith(self.initial_events))
        self.assertFalse(any(e.get('kind') == 'gate_run' for e in self.new_events()))

    def new_events(self):
        suffix = self.events_path.read_bytes()[len(self.initial_events):]
        return [json.loads(line) for line in suffix.decode('utf-8').splitlines() if line.strip()]

    def invoke(self, tool, args):
        return subprocess.run([PY, '-B', str(self.root / 'adapters/tools' / tool), *args], cwd=self.root,
                              env={**os.environ, 'FAKE_ORCA_STATE': str(self.state), 'PYTHONDONTWRITEBYTECODE': '1'},
                              capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=25)

    def watch(self, *, wo='DOC', role='doc_editor', agent='codex', workspace=None, execution=None):
        return self.invoke('orca_watch.py', ['--execution-id', execution or self.run_id,
            '--terminal', H, '--workspace', str(workspace or self.worker), '--agent', agent,
            '--wo', wo, '--role', role, '--cap-seconds', '3', '--interval', '1',
            '--orca', str(self.fake), '--transcript-root', str(self.brain), '--log', str(self.state / 'watch.log')])

    def transcript(self):
        codex_rollout(self.brain, 'observed-provider-session', datetime.now(timezone.utc), str(self.worker))
        return next(self.brain.rglob('rollout-*.jsonl'))

    def collect(self, source, *, wo='DOC', role='doc_editor', execution=None):
        return self.invoke('collect_usage.py', ['--execution-id', execution or self.run_id,
            '--codex-session', str(source), '--wo', wo, '--role', role])

    def observation(self, outcome):
        found = [e for e in self.new_events() if e.get('kind') == 'execution_observation']
        self.assertEqual(len(found), 1, found)
        e = found[0]
        self.assertEqual(e['execution_id'], self.run_id)
        self.assertEqual(e['terminal'], H)
        self.assertEqual(Path(e['workspace']).resolve(), self.worker.resolve())
        self.assertEqual(e['wo'], 'DOC')
        self.assertEqual(e['role'], 'doc_editor')
        self.assertEqual(e['outcome'], outcome)
        self.assertEqual(e['time_scope'], 'watcher_wall')
        self.assertEqual(e['time_source'], 'watcher_monotonic')
        self.assertGreaterEqual(e['wall_seconds'], 0)
        self.assertGreaterEqual(datetime.fromisoformat(e['ended'].replace('Z', '+00:00')),
                                datetime.fromisoformat(e['started'].replace('Z', '+00:00')))
        self.assertIsNone(e.get('model_activity_seconds'))
        self.assertIsNone(e.get('observed_model'))
        self.assertIsNone(e.get('permission'))
        return e

    def test_registered_idle_then_actual_collection_and_dedup(self):
        (self.state / 'idle').touch()
        source = self.transcript()
        start = time.monotonic()
        p = self.watch()
        elapsed = time.monotonic() - start
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        e = self.observation('idle_only')
        self.assertLessEqual(e['wall_seconds'], elapsed + 1)
        self.assertFalse([e for e in self.new_events() if e.get('kind') == 'model_usage'])
        p = self.collect(source)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        usage = [e for e in self.new_events() if e.get('kind') == 'model_usage']
        self.assertEqual(len(usage), 1)
        self.assertEqual(usage[0]['execution_id'], self.run_id)
        self.assertEqual(usage[0]['run_id'], 'observed-provider-session')
        self.assertEqual(usage[0]['tokens']['input'], 1000)
        self.assertEqual(usage[0]['time_scope'], 'transcript_span')
        before = self.events_path.read_bytes()
        self.assertEqual(self.collect(source).returncode, 0)
        self.assertEqual(self.events_path.read_bytes(), before)

    def test_cap_and_unavailable_keep_observation_separate_from_usage(self):
        for scenario in ['cap_with_source', 'cap_without_source', 'interrupt_failed', 'terminal_gone', 'unsatisfied_request']:
            with self.subTest(scenario=scenario):
                self.setUp()
                if scenario == 'cap_with_source':
                    self.transcript()
                if scenario == 'interrupt_failed':
                    (self.state / 'sendfail').touch()
                if scenario == 'terminal_gone':
                    (self.state / 'gone').touch()
                if scenario == 'unsatisfied_request':
                    (self.state / 'unsat').touch()
                p = self.watch()
                self.assertEqual(p.returncode, 0 if scenario == 'terminal_gone' else 3, p.stdout + p.stderr)
                outcome = {'terminal_gone': 'terminal_unavailable', 'interrupt_failed': 'stop_unverified'}.get(scenario, 'interrupt_requested')
                observation = self.observation(outcome)
                if scenario != 'cap_with_source':
                    self.assertIsNone(observation.get('provider_session_id'))
                sent = (self.state / 'sent.log').read_text().splitlines() if (self.state / 'sent.log').exists() else []
                self.assertEqual(len(sent), 0 if scenario in ('terminal_gone', 'interrupt_failed') else 1)
                usage = [e for e in self.new_events() if e.get('kind') == 'model_usage']
                self.assertEqual(len(usage), 1 if scenario == 'cap_with_source' else 0)
                if usage:
                    self.assertEqual(usage[0]['execution_id'], self.run_id)
                    self.assertEqual(usage[0]['run_id'], 'observed-provider-session')
                    self.assertEqual(usage[0]['tokens']['input'], 1000)
                self.tearDown()

    def test_invalid_binding_rejects_before_calls_or_writes(self):
        (self.state / 'idle').touch()
        source = self.transcript()
        cases = [dict(wo='WRONG'), dict(role='reviewer'), dict(agent='agy'),
                 dict(workspace=self.fixture_dir), dict(execution='unregistered-run')]
        for changes in cases:
            with self.subTest(changes=changes):
                before = self.events_path.read_bytes()
                p = self.watch(**changes)
                self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
                self.assertEqual(self.events_path.read_bytes(), before)
                self.assertFalse((self.state / 'watch.log').exists())
                self.assertFalse((self.state / 'sent.log').exists())
        for changes in [dict(wo='WRONG'), dict(role='reviewer'), dict(execution='unregistered-run')]:
            with self.subTest(collector=changes):
                before = self.events_path.read_bytes()
                p = self.collect(source, **changes)
                self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
                self.assertEqual(self.events_path.read_bytes(), before)
        # R2: different actual provider must not be linked to a Codex registration.
        agy_tr(self.brain, 'wrong-provider', datetime.now(timezone.utc), self.worker.name)
        wrong_source = self.brain / 'wrong-provider/.system_generated/logs/transcript.jsonl'
        before = self.events_path.read_bytes()
        p = self.invoke('collect_usage.py', ['--execution-id', self.run_id, '--agy-transcript',
            str(wrong_source), '--wo', 'DOC', '--role', 'doc_editor'])
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertEqual(self.events_path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
