import test from 'node:test'
import { execFileSync } from 'node:child_process'

test('real worker Apple preview is read-only and cannot authorize raw deletion or an invalid private native snapshot', () => {
  execFileSync('python3', ['-B', '-c', `
import sys,tempfile
sys.path.insert(0,'packages/alcor-secretary')
from business.worker import Business
with tempfile.TemporaryDirectory(prefix='alcor-apple-closed-') as root:
 b=Business(root);row=b.tasks.create_todo({'title':'Synthetic linked task'})
 b.tasks.apple_bind('item',row['id'],{'apple_id':'native','group_id':'group','source_id':'source','can_delete':True,'native':{'reminder_id':'native'}})
 args={'todo_id':row['id']};plan=b.prepare_apple_delete('tasks','delete_todo',args,'a'*64)
 assert plan['id']==row['id'] and not b.tasks.apple_operations()
 for submit in [lambda:b.invoke('tasks','delete_todo',args),lambda:b.commit_apple_delete('tasks','delete_todo',args,'a'*64,plan['snapshot'],{'owner':True,'outcome':'allowed-once'})]:
  try:submit()
  except (ValueError,PermissionError):pass
  else:raise AssertionError('preview granted permanent deletion')
 assert b.tasks.get_todo(row['id']) and not b.tasks.apple_operations()
`])
})
