#!/usr/bin/env python3
import json
import sys
import time
import os
from pathlib import Path
import fcntl
import uuid
from datetime import datetime,timedelta

request = json.loads(sys.stdin.readline())
op = request['op']
state_file = os.environ.get('STELLA_EVENTKIT_STATE') or (str(Path(os.environ['DSH_HOME']) / 'fake-eventkit-state.json') if os.environ.get('DSH_HOME') else None)
defaults = [{'id':'a','title':'Synthetic source','writable':True,'immutable':False,'source_id':'account1','source':'Synthetic iCloud'}]
state = {'calendar': list(defaults), 'reminders': list(defaults)}
lock = None
if state_file:
    lock = open(state_file + '.lock', 'a')
    fcntl.flock(lock, fcntl.LOCK_EX)
    if Path(state_file).exists():
        state = json.loads(Path(state_file).read_text())
kind = 'reminders' if op.startswith('reminder') or op == 'reminders' else 'calendar'

if op in ('watch', 'reminder_watch'):
    if lock:
        lock.close()
    while True:
        time.sleep(1)
elif op in ('status', 'reminder_status', 'request_access', 'reminder_request_access'):
    result = {'supported': True, 'authorization': 'full_access'}
elif op in ('calendars', 'reminder_lists'):
    result = {'calendars' if op == 'calendars' else 'lists':state[kind],'sources':[{'id':'account1','title':'Synthetic iCloud','can_create':True}],'source_ids':['account1'],'default_id':'a','default_source_id':'account1'}
elif op in ('create_calendar', 'reminder_list_create'):
    row = {'id': 'synthetic-' + request['title'], 'title': request['title'], 'color': request['color'], 'writable':True,'immutable':False,'source_id':request.get('source_id','account1'),'source':'Synthetic iCloud'}
    state[kind].append(row)
    result = {'calendar' if kind == 'calendar' else 'list': row}
elif op in ('delete_calendar', 'reminder_list_delete'):
    identity = request['calendar_id' if kind == 'calendar' else 'list_id']
    state[kind] = [row for row in state[kind] if row['id'] != identity]
    result = {'deleted': True}
elif op in ('rename', 'reminder_rename'):
    identity = request['calendar_id' if kind == 'calendar' else 'list_id']
    row = next(row for row in state[kind] if row['id'] == identity)
    row.update({key: request[key] for key in ('title', 'color') if key in request})
    result = {'calendar' if kind == 'calendar' else 'list': row}
elif op in ('events', 'lookup'):
    event = dict(calendar_id='a', event_id='event', external_id='external', occurrence_date='2026-11-05T01:00:00Z',
                 stella_id='', title='Synthetic Apple event', description='', location='',
                 start_at='2026-11-05T01:00:00Z', end_at='2026-11-05T02:00:00Z', all_day=False,
                 timezone='Asia/Shanghai', recurrence={'frequency': 'none'}, is_detached=False,
                 last_modified='2026-11-04T00:00:00Z', writable=True)
    rows=state.setdefault('calendar_items',[event])
    if op=='events':
        lower,upper=[datetime.fromisoformat(request[key].replace('Z','+00:00')) for key in ('start','end')]
        result={'events':[row for row in rows if row['calendar_id'] in request['calendar_ids'] and lower<=datetime.fromisoformat(row['start_at'].replace('Z','+00:00'))<upper],'successful_calendar_ids':[row['id'] for row in state[kind]],'failed_calendar_ids':[]}
    else:
        found=next((row for row in rows if row['calendar_id']==request['calendar_id'] and (row['event_id']==request.get('event_id') or request.get('external_id') and row['external_id']==request['external_id'])),None)
        result={'event':found}
elif op in ('reminders', 'reminder_lookup'):
    reminder = dict(list_id='a', reminder_id='reminder', external_id='external', stella_id='',
                    title='Synthetic Apple task', notes='', due_on='2026-11-05', due_time=None,
                    timezone='Asia/Shanghai', planned_on=None, remind_at=None, alarm_count=0, priority=0,
                    completed=False, completed_at=None, recurrence={'frequency': 'none'},
                    last_modified='2026-11-04T00:00:00Z', writable=True)
    rows=state.setdefault('reminder_items',[reminder])
    result={'reminders':[row for row in rows if row['list_id'] in request['list_ids']],'successful_list_ids':[row['id'] for row in state[kind]],'failed_list_ids':[]} if op=='reminders' else {'reminder':next((row for row in rows if row['list_id']==request['list_id'] and (row['reminder_id']==request.get('reminder_id') or request.get('external_id') and row['external_id']==request['external_id'])),None)}
elif op in ('upsert','reminder_upsert','delete','reminder_delete','reminder_complete'):
    reminder=kind=='reminders'
    group,identity,collection,singular=('list_id','reminder_id','reminder_items','reminder') if reminder else ('calendar_id','event_id','calendar_items','event')
    rows=state.setdefault(collection,[])
    matches=[row for row in rows if row[group]==request.get('lookup_'+group,request[group]) and (row[identity]==request.get(identity) or request.get('external_id') and row['external_id']==request['external_id'])]
    found=matches[0] if len(matches)==1 else None
    if op in ('delete','reminder_delete'):
        if found:rows.remove(found)
        result={'deleted':True}
    elif op=='reminder_complete':
        assert found and found['due_on']==request['occurrence_on']
        found.update(completed=request['completed'],completed_at=datetime.now().astimezone().isoformat())
        if found.get('recurrence',{}).get('frequency')=='daily' and request['completed']:
            rows.append({**found,'reminder_id':str(uuid.uuid4()),'due_on':(datetime.fromisoformat(found['due_on'])+timedelta(days=1)).date().isoformat(),'completed':False,'completed_at':None})
        result={'reminder':found}
    else:
        if found is None:
            found={group:request[group],identity:str(uuid.uuid4()),'external_id':request.get('stella_id') or str(uuid.uuid4()),'stella_id':'','writable':True,'last_modified':datetime.now().astimezone().isoformat(),'timezone':'UTC','alarm_count':0}
            rows.append(found)
        found.update({key:value for key,value in request.items() if key in ('title','notes','description','location','start_at','end_at','all_day','due_on','due_time','planned_on','remind_at','priority','completed','completed_at','timezone','url','alarms','floating',group)})
        if not reminder:
            found['occurrence_date']=request.get('occurrence_date',found['start_at']);found['timezone']=request.get('repeat_timezone',found['timezone']);found['is_detached']=False
        if not matches or request.get('set_recurrence'):
            found['recurrence']=request.get('native_recurrence') or {'frequency':request.get('repeat_frequency','none'),'interval':request.get('repeat_interval',1),'days':request.get('repeat_days',[]),'until':request.get('repeat_until')}
        result={singular:found}
else:
    print(json.dumps({'ok': False, 'error': 'Unexpected synthetic system write: ' + op}), flush=True)
    sys.exit(1)
if state.get('offline') and op not in ('status','reminder_status','calendars','reminder_lists'):
    print(json.dumps({'ok':False,'error':'Synthetic offline; local data retained'}),flush=True)
    sys.exit(1)
if state_file:
    Path(state_file).write_text(json.dumps(state))
if lock:
    lock.close()
print(json.dumps({'ok': True, **result}), flush=True)
