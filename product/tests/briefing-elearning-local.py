"""The saved eLearning assignment rule must resolve typed rows before model submission."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'packages/alcor-secretary'))
from business.briefing.domain.generator import NeedModel
from business.briefing.domain.models import FilterRule, SourceItem, EmailMessage
from business.briefing.domain.routing import ASSIGNMENT_ONLY_RULE, routed_draft, validate_policy


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

    def test_published_grades_are_independent_of_assignment_deadlines(self):
        policy = {'assignment': 'pending', 'grade': 'published', 'course_grade': 'exclude'}
        rows = [(item('zero', 'grade', submission_status='graded', score=0, points_possible=100,
                      due_at='2026-11-01T00:00:00Z'), None),
                (item('waiting', 'grade', submission_status='submitted', score=None), None),
                (item('provisional', 'grade', submission_status='pending_review', score=95), None),
                (item('total', 'course_grade', score=99), None)]
        draft = routed_draft(rows, [rule()], '2026-11-16', [], {}, NOW, policy)
        self.assertEqual([card['source_id'] for card in draft['items']], ['zero'])
        self.assertIn('0 / 100', draft['body'])
        # A revised score is re-evaluated even with an older saved decision.
        rows[0][0].payload['score'] = 85
        old = {'zero': {'_individual_report': True, 'selected': False}}
        updated = routed_draft(rows, [rule()], '2026-11-16', [], old, NOW, policy)
        self.assertIn('85 / 100', updated['body'])

    def test_interpretation_mode_batches_complete_remaining_originals(self):
        rows = [(item('grade', 'grade', submission_status='graded', score=100), None),
                (item('notice', 'announcement'), None)]
        policy = {'grade': 'published', 'assignment': 'pending', 'course_grade': 'exclude'}
        with self.assertRaises(NeedModel) as pending:
            routed_draft(rows, [rule('请按正文判断其他通知')], '2026-11-16', [], {}, NOW, policy)
        request = pending.exception.request
        self.assertEqual([row['id'] for row in request['entries']], ['notice'])
        self.assertIn('上传指定材料', request['entries'][0]['body'])
        self.assertTrue(request['singleBatch'])
        with self.assertRaises(NeedModel):
            routed_draft(rows[:1], [rule()], '2026-11-16', [], {}, NOW, {'grade': 'model'})

    def test_only_exact_matching_grade_notices_are_deduplicated(self):
        grade = item('score', 'grade', submission_status='graded', score=100, points_possible=100)
        grade.url = 'https://elearning.fudan.edu.cn/courses/42/grades#assignment_12'
        mail = SourceItem(id='mail', source='email', account_id='mail', source_id='mail', thread_id='mail',
                          sender='eLearning', title='作业已计分:练习', snippet='', occurred_at=NOW, url='mid:mail',
                          payload={'body': '您的作业 练习 已计分。\n计分日期: 11月16日 12:00\n分数：100 次，共 100 次\n您可在此处审阅作业:\nhttps://elearning.fudan.edu.cn/courses/42/assignments/12/submissions/7'})
        rows = [(grade, None), (mail, EmailMessage('mail', 'elearning@fudan.edu.cn'))]
        policy = {'grade': 'published'}
        draft = routed_draft(rows, [], '2026-11-16', [], {}, NOW, policy)
        self.assertEqual(len(draft['items']), 1)
        for body in (mail.payload['body'] + '\n请明天前提交复核申请。', mail.payload['body'].replace('100 次，共', '90 次，共')):
            mail.payload['body'] = body
            with self.assertRaises(NeedModel) as pending:
                routed_draft(rows, [], '2026-11-16', [], {}, NOW, policy)
            self.assertEqual([e['id'] for e in pending.exception.request['entries']], ['mail'])

    def test_invalid_policies_are_rejected(self):
        for value in ([], {'grade': True}, {'grade': []}, {'unknown': 'exclude'}):
            with self.assertRaises(ValueError):
                validate_policy(value)

    def test_edited_policy_falls_back_to_model(self):
        rows = [(item('open', 'assignment', submission_status='unsubmitted', due_at='2026-11-17T13:30:00+08:00'), None)]
        with self.assertRaises(NeedModel) as pending:
            routed_draft(rows, [rule('请按新的课程规则决定。')], '2026-11-16', [], {}, NOW)
        self.assertEqual(pending.exception.request['entries'][0]['id'], 'open')


if __name__ == '__main__':
    unittest.main()
