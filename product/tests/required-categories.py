"""Required category and destructive category deletion contracts."""
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'packages/alcor-secretary'))
from business.worker import Business


class RequiredCategories(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='stella-required-category-')
        self.root = Path(self.temp.name)
        self.business = Business(self.root)

    def tearDown(self):
        self.business.meta.close()
        self.temp.cleanup()

    def test_default_assignment_and_explicit_empty_rejection(self):
        calendar, tasks = self.business.calendar, self.business.tasks
        event = calendar.create_event({'title':'Event', 'start_at':'2026-11-07T01:00:00Z', 'end_at':'2026-11-07T02:00:00Z'})
        task = tasks.create_todo({'title':'Task'})
        self.assertTrue(event['category_id'])
        self.assertTrue(task['category_id'])
        for empty in (None, '', False):
            with self.assertRaises(ValueError):
                calendar.update_event(event['id'], {'category_id':empty})
            with self.assertRaises(ValueError):
                tasks.update_todo(task['id'], {'category_id':empty})
            with self.assertRaises(ValueError):
                calendar.set_event_category(event['id'], empty)
            with self.assertRaises(ValueError):
                tasks.set_todo_category(task['id'], empty)
        self.assertEqual(calendar.get_event(event['id'])['category_id'], event['category_id'])

    def test_retry_does_not_create_an_extra_default_category(self):
        for owner, create, payload in (
            (self.business.calendar, self.business.calendar.create_event, {'title':'Stable', 'start_at':'2026-11-07T01:00:00Z', 'end_at':'2026-11-07T02:00:00Z'}),
            (self.business.tasks, self.business.tasks.create_todo, {'title':'Stable'}),
        ):
            for group in owner.categories():
                owner.delete_category(group['id'])
            group = owner.create_category({'name':'Existing'})
            first = create({**payload, 'category_id':group['id']}, _briefing_key='stable')
            before = owner.categories()
            self.assertEqual(create(payload, _briefing_key='stable')['id'], first['id'])
            self.assertEqual(owner.categories(), before)

    def test_sql_guard_and_external_import_require_a_real_category(self):
        for owner, table, create in (
            (self.business.calendar, 'calendar_events', lambda: self.business.calendar.create_event({'title':'Guard', 'start_at':'2026-11-07T01:00:00Z', 'end_at':'2026-11-07T02:00:00Z'})),
            (self.business.tasks, 'todos', lambda: self.business.tasks.create_todo({'title':'Guard'})),
        ):
            row = create()
            with self.assertRaises(sqlite3.IntegrityError), owner._connect() as db:
                db.execute(f'UPDATE {table} SET category_id=NULL WHERE id=?', (row['id'],))
            with self.assertRaises(sqlite3.IntegrityError), owner._connect() as db:
                db.execute(f'UPDATE {table} SET category_id=? WHERE id=?', ('missing', row['id']))
        calendar = self.business.calendar
        group = calendar.create_category({'name':'Apple class'})
        before = calendar.categories()
        imported = calendar.upsert_external_event('apple_calendar', 'eventkit:required', {'title':'External', 'start_at':'2026-11-07T01:00:00Z', 'end_at':'2026-11-07T02:00:00Z', 'category_id':group['id']})
        self.assertEqual(imported['category_id'], group['id'])
        self.assertEqual(calendar.categories(), before)

    def test_delete_category_deletes_records_and_reminders_without_restoring_them(self):
        calendar, tasks = self.business.calendar, self.business.tasks
        group = calendar.create_category({'name':'Delete calendar'})
        event = calendar.create_event({'title':'Recurring', 'category_id':group['id'], 'start_at':'2026-11-07T01:00:00Z', 'end_at':'2026-11-07T02:00:00Z', 'repeat_frequency':'daily'}, _briefing_key='briefing-deleted-category')
        op = 'a' * 64
        args = (event['id'], '2026-11-08', 'this')
        plan = calendar.prepare_local_occurrence_delete(*args, operation_id=op)
        calendar.commit_local_occurrence_delete(*args, snapshot=plan['snapshot'], operation_id=op)
        op = 'b' * 64
        plan = calendar.prepare_local_category_delete(group['id'], operation_id=op)
        calendar.commit_local_category_delete(group['id'], snapshot=plan['snapshot'], operation_id=op)
        with self.assertRaises(KeyError):
            calendar.get_event(event['id'])
        self.assertTrue(calendar.has_source_tombstone(['briefing-deleted-category']))
        group = tasks.create_category({'name':'Delete list'})
        task = tasks.create_todo({'title':'Task', 'category_id':group['id'], 'remind_at':'2099-12-25T05:00:00Z'}, _briefing_key='task-deleted-category')
        child = tasks.add_subtask(task['id'], {'title':'Child'})
        op = 'b' * 64
        plan = tasks.prepare_local_category_delete(group['id'], operation_id=op)
        tasks.commit_local_category_delete(group['id'], snapshot=plan['snapshot'], operation_id=op)
        for row in [task, child]:
            with self.assertRaises(KeyError):
                tasks.get_todo(row['id'])
        with tasks._connect() as db:
            self.assertIsNotNone(db.execute('SELECT 1 FROM source_tombstones WHERE source_key=?', ('task-deleted-category',)).fetchone())
            self.assertFalse(db.execute("SELECT 1 FROM todo_scheduler_links WHERE desired_at IS NOT NULL").fetchone())

    def test_subtasks_inherit_the_parent_list_without_creating_an_extra_list(self):
        tasks = self.business.tasks
        group = tasks.create_category({'name':'planA'})
        parent = tasks.create_todo({'title':'Parent','category_id':group['id']})
        before = tasks.categories()
        for child in [tasks.add_subtask(parent['id'],{'title':'Child'}), tasks.create_todo({'title':'Direct child','parent_id':parent['id']})]:
            self.assertEqual(child['category_id'], group['id'])
            self.assertEqual(child['list_name'], 'planA')
        self.assertEqual(tasks.categories(), before)
        other = tasks.create_category({'name':'Explicit list'})
        explicit = tasks.add_subtask(parent['id'],{'title':'Explicit child','category_id':other['id']})
        self.assertEqual(explicit['category_id'], other['id'])
        with self.assertRaises(ValueError):
            tasks.add_subtask(parent['id'],{'title':'Invalid child','category_id':None})

    def test_old_uncategorized_records_migrate_without_changing_identity(self):
        calendar, tasks = self.business.calendar, self.business.tasks
        event = calendar.create_event({'title':'Old event', 'start_at':'2026-11-07T01:00:00Z', 'end_at':'2026-11-07T02:00:00Z'})
        task = tasks.create_todo({'title':'Old task', 'notes':'Keep', 'tags':['keep']})
        # Simulate a database produced by the previous version.
        for owner, table in [(calendar,'calendar_events'), (tasks,'todos')]:
            with owner._connect() as db:
                for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall():
                    db.execute(f'DROP TRIGGER "{name}"')
                db.execute(f'UPDATE {table} SET category_id=NULL')
        from business.calendar.domain.service import CalendarService
        from business.tasks.domain.service import TodoService
        event_after = CalendarService(calendar.db_path).get_event(event['id'])
        task_after = TodoService(tasks.db_path).get_todo(task['id'])
        self.assertTrue(event_after['category_id'])
        self.assertTrue(task_after['category_id'])
        self.assertEqual(task_after['notes'], 'Keep')
        self.assertEqual(json.loads(task_after['tags']), ['keep'])
        self.assertEqual(event_after['start_at'], event['start_at'])


if __name__ == '__main__':
    unittest.main()
