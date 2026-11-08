import test from 'node:test'
import { execFileSync } from 'node:child_process'

test('owner snapshots reject same-ID recreations at identical timestamps', () => {
  execFileSync('python3', ['-B', '-c', `
import sys,tempfile
from pathlib import Path
sys.path.insert(0,'packages/alcor-secretary')
import business.tasks.domain.service as tasks
import business.calendar.domain.service as calendar
with tempfile.TemporaryDirectory(prefix='alcor-generation-aba-') as root:
 for name,module,cls in [('tasks',tasks,tasks.TodoService),('calendar',calendar,calendar.CalendarService)]:
  module._now=lambda:'2026-11-01T00:00:00+00:00'
  svc=cls(Path(root)/(name+'.sqlite3')); identity='synthetic-same-id'
  if name=='tasks':
   payload={'id':identity,'title':'Synthetic'}
   svc.create_todo(payload); plan=svc.prepare_local_todo_delete(identity,operation_id='a'*64)
   replacement=svc.prepare_local_todo_delete(identity,operation_id='b'*64)
   svc.commit_local_todo_delete(identity,snapshot=replacement['snapshot'],operation_id='b'*64); svc.create_todo(payload)
   commit=lambda:svc.commit_local_todo_delete(identity,snapshot=plan['snapshot'],operation_id='a'*64)
   read=svc.get_todo
  else:
   payload={'id':identity,'title':'Synthetic','start_at':'2026-11-01T09:00:00+00:00','end_at':'2026-11-01T10:00:00+00:00'}
   svc.create_event(payload); plan=svc.prepare_local_event_delete(identity,operation_id='a'*64)
   replacement=svc.prepare_local_event_delete(identity,operation_id='b'*64)
   svc.commit_local_event_delete(identity,snapshot=replacement['snapshot'],operation_id='b'*64); svc.create_event(payload)
   commit=lambda:svc.commit_local_event_delete(identity,snapshot=plan['snapshot'],operation_id='a'*64)
   read=svc.get_event
  try: commit()
  except ValueError as error: assert 'conflict' in str(error)
  else: raise AssertionError('old approval removed a new object')
  assert read(identity)['id']==identity
`])
})

test('generation migration preserves old business rows and recovery, and rolls back atomically', () => {
  execFileSync('python3', ['-B', '-c', `
import sys,tempfile
from pathlib import Path
sys.path.insert(0,'packages/alcor-secretary')
from business.tasks.domain.service import TodoService
from business.calendar.domain.service import CalendarService
with tempfile.TemporaryDirectory(prefix='alcor-generation-migrate-') as root:
 for name,cls,table in [('tasks',TodoService,'todos'),('calendar',CalendarService,'calendar_events')]:
  path=Path(root)/(name+'.sqlite3'); svc=cls(path)
  item=svc.create_todo({'title':'Synthetic'}) if name=='tasks' else svc.create_event({'title':'Synthetic','start_at':'2026-11-01T09:00:00+00:00','end_at':'2026-11-01T10:00:00+00:00'})
  live=svc.create_todo({'title':'Synthetic retained'}) if name=='tasks' else svc.create_event({'title':'Synthetic retained','start_at':'2026-11-02T09:00:00+00:00','end_at':'2026-11-02T10:00:00+00:00'})
  with svc._connect() as db:
   before=dict(db.execute('SELECT * FROM '+table+' WHERE id=?',(item['id'],)).fetchone())
   live_before=dict(db.execute('SELECT * FROM '+table+' WHERE id=?',(live['id'],)).fetchone())
   for row in db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'alcor_generation_%'").fetchall(): db.execute('DROP TRIGGER '+row[0])
   db.execute('DROP TABLE alcor_object_generations')
  svc.apple_remove_item(item['id'])
  recovery=svc.list_apple_inbound_recoveries()[0]['id']
  # Old archived row shape remains valid after the generation migration.
  svc=cls(path)
  with svc._connect() as db:
   assert dict(db.execute('SELECT * FROM '+table+' WHERE id=?',(live['id'],)).fetchone())==live_before
   assert db.execute("SELECT generation FROM alcor_object_generations WHERE entity='item' AND object_id=?",(live['id'],)).fetchone()[0]
  restored=svc.restore_apple_inbound_recovery(recovery); rid=restored['ids'][0]
  with svc._connect() as db:
   after=dict(db.execute('SELECT * FROM '+table+' WHERE id=?',(rid,)).fetchone())
   assert set(before)==set(after)
   before_generation=db.execute("SELECT generation FROM alcor_object_generations WHERE entity='item' AND object_id=?",(rid,)).fetchone()[0]
   db.execute('BEGIN IMMEDIATE'); db.execute('DELETE FROM '+table+' WHERE id=?',(rid,)); db.rollback()
   assert db.execute("SELECT generation FROM alcor_object_generations WHERE entity='item' AND object_id=?",(rid,)).fetchone()[0]==before_generation
  svc=cls(path)
  with svc._connect() as db:
   assert db.execute("SELECT generation FROM alcor_object_generations WHERE entity='item' AND object_id=?",(rid,)).fetchone()[0]==before_generation
   assert db.execute("SELECT count(*) FROM alcor_object_generations WHERE entity='item'").fetchone()[0]==db.execute('SELECT count(*) FROM '+table).fetchone()[0]
`])
})
