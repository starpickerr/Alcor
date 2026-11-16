"""The saved eLearning assignment rule must resolve typed rows before model submission."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'packages/alcor-secretary'))
from business.briefing.domain.generator import NeedModel
from business.briefing.domain.models import FilterRule, SourceItem
from business.briefing.domain.routing import ASSIGNMENT_ONLY_RULE, routed_draft


NOW = '2026-11-16T12:00:00+08:00'


def item(identity, kind, **payload):
    return SourceItem(id=identity, source='elearning', account_id='school', source_id=identity,
                      thread_id=identity, sender='课程', title='课程 · ' + identity,
                      snippet='待完成', occurred_at='2026-11-16T03:00:00Z', url='https://example.test/' + identity,
                      payload={'body': '# 作业\n- 提交方式：上传文件\n## 要求原文\n上传指定材料',
                               'course_id': '42', 'resource_type': kind, **payload})


def rule(text=ASSIGNMENT_ONLY_RULE):
    return FilterRule(id='policy', enabled=True, source='elearning', action='exclude',
                      match_field='title', match_op='contains', match_value='unused', note='',
                      created_at='', updated_at='', text=text)


class RoutingTests(unittest.TestCase):
    def test_typed_assignments_resolve_locally_and_email_remains_for_model(self):
        rows = [(item('open', 'assignment', submission_status='unsubmitted', due_at='2026-11-17T13:30:00+08:00'), None),
                (item('done', 'assignment', submission_status='submitted', due_at='2026-11-17T13:30:00+08:00'), None),
                (item('late', 'assignment', submission_status='unsubmitted', due_at='2026-11-15T13:30:00+08:00'), None),
                (item('unknown', 'assignment', submission_status='unsubmitted'), None),
                (item('score-zero', 'grade', score=0, points_possible=100), None),
                (item('notice', 'announcement'), None),
                (item('file', 'file'), None)]
        mail = SourceItem(id='mail', source='email', account_id='a', source_id='mail', thread_id='mail',
                          sender='Teacher', title='Message', snippet='Read this',
                          occurred_at='2026-11-16T03:00:00Z', url='', payload={'body': 'Read this'})
        rows.append((mail, None))
        with self.assertRaises(NeedModel) as pending:
            routed_draft(rows, [rule()], '2026-11-16', [], {}, NOW)
        self.assertEqual([row['id'] for row in pending.exception.request['entries']], ['mail'])
        draft = routed_draft(rows, [rule()], '2026-11-16',
                             [{'decisions': [{'id': 'mail', 'selected': False, 'reason': '无关'}]}], {}, NOW)
        self.assertEqual([card['source_id'] for card in draft['items']], ['open'])
        self.assertIn('上传指定材料', draft['body'])
        self.assertIn('2026-11-17 13:30', draft['items'][0]['snippet'])
        self.assertEqual(len(draft['mail']), len(rows))

    def test_edited_policy_falls_back_to_model(self):
        rows = [(item('open', 'assignment', submission_status='unsubmitted', due_at='2026-11-17T13:30:00+08:00'), None)]
        with self.assertRaises(NeedModel) as pending:
            routed_draft(rows, [rule('请按新的课程规则决定。')], '2026-11-16', [], {}, NOW)
        self.assertEqual(pending.exception.request['entries'][0]['id'], 'open')


if __name__ == '__main__':
    unittest.main()
