"""WO-H66 P1-A proposed Red: transcript spans must not become activity time.
Synthetic intervals exercise provenance handling, not real provider observations.
Run: python -B .jobs/h66/P1-A/check_activity_provenance.py
"""
from pathlib import Path
import copy
import importlib.util
import json
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'harness'))
sys.path.insert(0, str(ROOT / 'adapters/tools'))
import kpi
import collect_usage


def measured(run='observed', **kw):
    value = dict(kind='model_usage', wo='WO-H66', task='P1-A', role='implementer', agent='agy',
                 run_id=run, seconds=20.0, time_scope='model_activity',
                 time_source='measured_activity_intervals', started='2026-10-21T00:00:00Z',
                 ended='2026-10-21T00:00:20Z', tier='T1', risk='medium',
                 tokens={'input': 100, 'output': 10, 'cache_read': 0}, model_calls=1)
    value.update(kw)
    return value


class ActivityProvenance(unittest.TestCase):
    def test_agy_long_final_response_is_only_transcript_span(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'provider-session/.system_generated/logs/transcript.jsonl'
            p.parent.mkdir(parents=True)
            rows = [dict(created_at='2026-10-21T00:00:00Z', input_tokens=100),
                    dict(created_at='2026-10-21T00:00:08Z', output_tokens=10,
                         text='Long final generation has no end timestamp or measured interval')]
            p.write_text('\n'.join(map(json.dumps, rows)), encoding='utf-8')
            result = collect_usage.parse_agy(p)
            self.assertEqual(result['time_scope'], 'transcript_span')
            self.assertEqual(result['tokens']['input'], 100)
            self.assertEqual(result['tokens']['output'], 10)
            self.assertEqual(result['seconds'], 8.0)
            event = measured(time_scope=result['time_scope'], time_source=None, seconds=result['seconds'])
            self.assertEqual(kpi.timing_summary([event])[0]['모델 활동 합(초)'], 'unknown')

    def test_codex_record_span_is_not_activity_either(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'rollout.jsonl'
            rows = [dict(type='session_meta', timestamp='2026-10-21T00:00:00Z',
                         payload=dict(id='provider-codex', model='declared')),
                    dict(type='event_msg', timestamp='2026-10-21T00:00:08Z',
                         payload=dict(type='task_complete'))]
            p.write_text('\n'.join(map(json.dumps, rows)), encoding='utf-8')
            result = collect_usage.parse_codex(p)
            self.assertEqual(result['time_scope'], 'transcript_span')
            self.assertEqual(result['seconds'], 8.0)
            event = measured(time_scope=result['time_scope'], time_source=None, seconds=result['seconds'])
            self.assertEqual(kpi.timing_summary([event])[0]['모델 활동 합(초)'], 'unknown')

    def test_legacy_span_is_not_aggregated_or_proposed(self):
        legacy = measured(time_source=None, seconds=8.0)
        before = copy.deepcopy(legacy)
        row = kpi.usage_by_role_agent([legacy])[0]
        self.assertIn(row['모델 활동 시간(분)'], ('—', 'unknown'))
        summary = kpi.timing_summary([legacy])[0]
        self.assertEqual(summary['모델 활동 합(초)'], 'unknown')
        self.assertEqual(summary['모델 벽시계(초)'], 'unknown')
        self.assertEqual(kpi.time_observations([legacy]), {})
        self.assertEqual(legacy, before, 'historical event must stay unchanged')

    def test_legacy_timing_row_does_not_reconstruct_activity(self):
        legacy = measured(time_source=None, seconds=None)
        row = kpi.timing_rows([legacy])[0]
        self.assertEqual(row['시간(초)'], 'unknown')
        self.assertNotIn('·model_activity', row['구간'])

    def test_measured_control_and_duplicate_preservation(self):
        event = measured()
        snapshot = copy.deepcopy(event)
        row = kpi.usage_by_role_agent([event, event])[0]
        self.assertEqual(row['위임 요청 수'], '1')
        self.assertEqual(row['입력'], '100')
        self.assertEqual(kpi.timing_summary([event, event])[0]['모델 활동 합(초)'], '20.0')
        self.assertEqual(event, snapshot)

    def test_delegation_wall_is_not_counted_as_activity(self):
        event = measured(time_scope='delegation_total', time_source=None, seconds=600.0)
        row = kpi.usage_by_role_agent([event])[0]
        self.assertIn(row['모델 활동 시간(분)'], ('—', 'unknown'))
        self.assertEqual(kpi.timing_summary([event])[0]['모델 활동 합(초)'], 'unknown')

    def test_review_cost_mixed_sources_is_partial(self):
        events = [measured('review-measured', role='reviewer'),
                  measured('review-span', role='reviewer', time_scope='transcript_span', time_source=None, seconds=8.0),
                  measured('review-wall', role='reviewer', time_scope='delegation_total', time_source=None, seconds=600.0)]
        row = kpi.review_cost_by_risk(events)[0]
        self.assertEqual(row['검토 모델 시간(초)'], '20.0 부분 (확인 1/3)')
        self.assertEqual(row['검토 입력 토큰'], '300')

    def test_review_cost_unknown_is_not_zero(self):
        row = kpi.review_cost_by_risk([measured(role='reviewer', time_source=None)])[0]
        self.assertEqual(row['검토 모델 시간(초)'], 'unknown')

    def test_mixed_known_unknown_remains_partial(self):
        events = [measured(), measured('legacy', time_source=None, seconds=8.0)]
        row = kpi.usage_by_role_agent(events)[0]
        self.assertIn('부분', row['모델 활동 시간(분)'])
        summary = kpi.timing_summary(events)[0]
        self.assertIn('부분', summary['모델 활동 합(초)'])


if __name__ == '__main__':
    unittest.main()
