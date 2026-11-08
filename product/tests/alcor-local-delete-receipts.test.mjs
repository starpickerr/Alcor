import test from 'node:test'
import { execFileSync } from 'node:child_process'

test('read-only local snapshots do not grant deletion and all original rows survive', () => {
  execFileSync('python3', ['-B', '-c', `
import sys,tempfile
from pathlib import Path
sys.path.insert(0,'packages/alcor-secretary')
from business.worker import Business
with tempfile.TemporaryDirectory(prefix='alcor-local-snapshot-') as root:
 b=Business(root)
 parent=b.tasks.create_todo({'title':'Synthetic parent'});child=b.tasks.create_todo({'title':'Synthetic child','parent_id':parent['id']})
 plan=b.local_delete_prepare('tasks',parent['id'],'a'*64)
 assert {x['id'] for x in plan['targets']}=={parent['id'],child['id']}
 b.tasks.update_todo(child['id'],{'title':'Changed child'})
 assert b.local_delete_prepare('tasks',parent['id'],'a'*64)['snapshot']!=plan['snapshot']
 for op in [None,'a'*64]:
  try:b.local_delete_commit('tasks',parent['id'],op,snapshot=plan['snapshot'])
  except (ValueError,PermissionError):pass
  else:raise AssertionError('snapshot or operation ID granted permanent deletion')
 assert b.tasks.get_todo(parent['id']) and b.tasks.get_todo(child['id'])
`])
})

test('reversible local cutoff preserves conditional receipt, replay, restart and ordinary undo', () => {
  execFileSync('python3', ['-B', '-c', `
import sys,tempfile
sys.path.insert(0,'packages/alcor-secretary')
from business.worker import Business
with tempfile.TemporaryDirectory(prefix='alcor-local-cutoff-') as root:
 b=Business(root);row=b.calendar.create_event({'title':'Series','start_at':'2026-11-01T09:00:00Z','end_at':'2026-11-01T10:00:00Z','repeat_frequency':'daily','repeat_timezone':'UTC'})
 args=(row['id'],'2026-11-03','future','b'*64)
 plan=b.prepare_local_occurrence_delete(*args)
 b.calendar.update_event(row['id'],{'title':'Changed'})
 try:b.commit_local_occurrence_delete(*args,snapshot=plan['snapshot'])
 except ValueError as error:assert 'conflict' in str(error)
 else:raise AssertionError('stale snapshot accepted')
 plan=b.prepare_local_occurrence_delete(*args);b.commit_local_occurrence_delete(*args,snapshot=plan['snapshot'])
 b.meta.close();b=Business(root)
 assert b.commit_local_occurrence_delete(*args)['replayed']
 b.calendar.update_event(row['id'],{'repeat_until':None})
 assert b.commit_local_occurrence_delete(*args)['replayed']
 assert b.calendar.get_event(row['id'])['repeat_until'] is None
`])
})
