"""Synthetic-only regression checks for briefing prompt deduplication."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('briefing_prompt_evaluation', ROOT / 'scripts/product/evaluate-briefing-prompts.py')
evaluation = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = evaluation
spec.loader.exec_module(evaluation)
from business.briefing.domain import generator


class PromptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads((ROOT / 'product/tests/fixtures/briefing-prompts.json').read_text())
        cls.result, cls.requests = evaluation.run(generator, cls.fixture)

    def test_selection_keeps_full_source_once_instead_of_a_second_preview(self):
        request = self.requests[0]
        self.assertEqual(len(request['entries']), 16)
        self.assertNotIn('candidates', evaluation.task_data(request))
        self.assertEqual(set(evaluation.task_data(request)), {'window_start', 'window_end'})
        entry = next(row for row in request['entries'] if row['id'] == 'assignment-tail')
        self.assertGreater(len(entry['body']), 2000)
        self.assertIn('2026年11月10日23:59', entry['body'][2000:])
        self.assertEqual(entry['metadata']['course_id'], 'course-physics')
        self.assertEqual(evaluation.model_prompt(request).count('末尾重要更新：'), 1)
        for row in self.fixture['rows']:
            entry = next(item for item in request['entries'] if item['id'] == row['id'])
            for key in ('source', 'account_id', 'source_id', 'thread_id', 'sender', 'sender_email', 'title', 'snippet', 'occurred_at', 'url'):
                self.assertEqual(entry[key], row[key])
            self.assertEqual(entry['body'], row['payload'].get('body') or row['snippet'])

    def test_writing_keeps_ordered_plan_and_all_selected_thread_members(self):
        request = self.requests[1]
        plan = evaluation.task_data(request)
        ids = ['task-overdue', 'mail-deadline', 'meeting-new', 'assignment-tail', 'calendar', 'bill-due',
               'injection', 'mail-keep-exception', 'source-rule-course', 'missing-date', 'grade', 'ambiguous']
        self.assertEqual(plan['expected_ids'], ids)
        self.assertEqual([row['id'] for row in plan['items']], ids)
        self.assertEqual([row['source_item_id'] for row in self.result['items']], ids)
        self.assertEqual(set(row['id'] for row in request['entries']),
                         set(row['id'] for row in self.fixture['rows'] if row['expected']['selected']))
        meeting = next(row for row in plan['items'] if row['id'] == 'meeting-new')
        self.assertEqual([row['id'] for row in meeting['members']], ['meeting-old', 'meeting-new'])
        for item in plan['items']:
            for row in [item, *item['members']]:
                self.assertNotIn('body_preview', row)
                self.assertNotIn('body_length', row)
        self.assertNotIn('read_briefing_source', request['task'])
        self.assertIn('阅读完整 body', request['task'])

    def test_existing_checkpoint_boundaries_are_preserved(self):
        data = dict(evaluation.scenarios(self.fixture))['multibatch_48']
        result, requests = evaluation.run(generator, data)
        selection = [r for r in requests if 'decisions' in r['schema']['properties']]
        writing = [r for r in requests if 'reports' in r['schema']['properties']]
        self.assertEqual([len(r['entries']) for r in selection], [20, 20, 8])
        self.assertEqual([len(evaluation.task_data(r)['items']) for r in writing], [15, 20, 1])
        self.assertEqual(len(result['items']), 36)
        self.assertEqual(len(result['mail']), 48)

    def test_transport_projection_does_not_mutate_plan(self):
        batch = [{'id': 'a', 'body_preview': 'body', 'body_length': 4, 'summary': 'summary',
                  'metadata': {'category': 'test'},
                  'members': [{'id': 'a', 'body_preview': 'body', 'body_length': 4, 'summary': 'summary'}]}]
        original = copy.deepcopy(batch)
        projected = generator.writing_items(batch)
        self.assertEqual(batch, original)
        self.assertEqual(projected, [{'id': 'a', 'summary': 'summary', 'metadata': {'category': 'test'},
                                      'members': [{'id': 'a', 'summary': 'summary'}]}])

    def test_empty_window_never_calls_model(self):
        result, requests = evaluation.run(generator, {**self.fixture, 'rows': []})
        self.assertEqual(requests, [])
        self.assertEqual(result['items'], [])

    def test_output_validation_is_not_relaxed(self):
        for payload in ({'decisions': []}, {'decisions': [{'id': 'a', 'selected': 'true', 'reason': 'x'}]},
                        {'decisions': [{'id': 'a', 'selected': True, 'reason': 'x', 'section': 'action', 'score': 101, 'summary': 'x'}]}):
            with self.assertRaises(ValueError):
                generator.validate_decisions(payload, ['a'])
        with self.assertRaises(ValueError):
            generator.validate_body({'body': 'body', 'source_item_ids': ['a'], 'reports': []}, ['a'])

    def test_estimate_matches_javascript_utf16_units(self):
        self.assertEqual(evaluation.estimate('😀😀😀'), 2)


if __name__ == '__main__':
    unittest.main()
