#!/usr/bin/env python3
import json
import sys
import time

request = json.loads(sys.stdin.readline())
op = request['op']
if op in ('watch', 'reminder_watch'):
    while True:
        time.sleep(1)
elif op in ('status', 'reminder_status', 'request_access', 'reminder_request_access'):
    result = {'supported': True, 'authorization': 'full_access'}
elif op in ('calendars', 'reminder_lists'):
    result = {'calendars' if op == 'calendars' else 'lists': [{'id': 'a', 'title': 'Synthetic source', 'writable': True}]}
elif op in ('events', 'lookup'):
    event = dict(calendar_id='a', event_id='event', external_id='external', occurrence_date='2026-11-05T01:00:00Z',
                 stella_id='', title='Synthetic Apple event', description='', location='',
                 start_at='2026-11-05T01:00:00Z', end_at='2026-11-05T02:00:00Z', all_day=False,
                 timezone='Asia/Shanghai', recurrence={'frequency': 'none'}, is_detached=False,
                 last_modified='2026-11-04T00:00:00Z', writable=True)
    result = {'events': [event], 'successful_calendar_ids': ['a'], 'failed_calendar_ids': []} if op == 'events' else {'event': event}
elif op in ('reminders', 'reminder_lookup'):
    reminder = dict(list_id='a', reminder_id='reminder', external_id='external', stella_id='',
                    title='Synthetic Apple task', notes='', due_on='2026-11-05', due_time=None,
                    timezone='Asia/Shanghai', planned_on=None, remind_at=None, alarm_count=0, priority=0,
                    completed=False, completed_at=None, recurrence={'frequency': 'none'},
                    last_modified='2026-11-04T00:00:00Z', writable=True)
    result = {'reminders': [reminder], 'successful_list_ids': ['a'], 'failed_list_ids': []} if op == 'reminders' else {'reminder': reminder}
else:
    print(json.dumps({'ok': False, 'error': 'Unexpected synthetic system write: ' + op}), flush=True)
    sys.exit(1)
print(json.dumps({'ok': True, **result}), flush=True)
