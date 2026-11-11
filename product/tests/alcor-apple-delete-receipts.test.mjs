import { python as testPython } from './platform-python.mjs'
import test from 'node:test'
import { execFileSync } from 'node:child_process'

test('old pending, sending and uncertain delete receipts cannot dispatch or finalize deletion', () => {
  execFileSync(testPython, ['-B', '-c', `
import sys,tempfile,json,sqlite3
sys.path[:0]=['packages/alcor-secretary','packages/alcor-apple-sync/python']
from business.worker import Business
from apple_sync.contracts import process_operations
with tempfile.TemporaryDirectory(prefix='alcor-old-delete-') as root:
 b=Business(root);row=b.calendar.create_event({'title':'Synthetic','start_at':'2026-11-01T09:00:00Z','end_at':'2026-11-01T10:00:00Z'})
 b.calendar.apple_bind('item',row['id'],{'apple_id':'native','group_id':'group','source_id':'source','can_delete':True})
 for state in ['pending','sending','uncertain']:
  with b.calendar._connect() as db:db.execute('INSERT INTO apple_operations VALUES (?,?,?,?,?,?,?,?)',(state,'item',row['id'],'delete',json.dumps({'_approval':{'snapshot':'a'*64,'native':{}}}),state,'','2026-11-01'))
  try:b.calendar.apple_finish_operation(state)
  except PermissionError as error:assert '人工确认' in str(error)
  else:raise AssertionError('old receipt finalized deletion')
 class Sync:
  reads=0
  def _connect(self):
   self.reads+=1
   db=sqlite3.connect(':memory:');db.execute('CREATE TABLE settings (key TEXT PRIMARY KEY,value TEXT)')
   db.set_authorizer(lambda action,*_: sqlite3.SQLITE_OK if action in (sqlite3.SQLITE_SELECT,sqlite3.SQLITE_READ,sqlite3.SQLITE_FUNCTION) else sqlite3.SQLITE_DENY)
   return db
  continue_approved_delete=None
  finish_approved_delete=None
  def _set(self,key,value):assert key=='operation_counts' and value=={'deleted_remote':0,'exported':0}
  def __getattr__(self,name):raise AssertionError('No bridge, receipt or claim access expected: '+name)
 sync=Sync();removed,errors=process_operations(sync,b.calendar,'calendar')
 assert sync.reads==1
 assert removed==0 and len(errors)==3 and b.calendar.get_event(row['id'])
 b.meta.close()
`])
})
