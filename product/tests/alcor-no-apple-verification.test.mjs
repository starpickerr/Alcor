import test from 'node:test'
import { execFileSync } from 'node:child_process'

test('verification-only Apple isolation blocks every invoke and touches neither bridge nor storage', () => {
  execFileSync('python3', ['-B', '-c', `
import os,sys,tempfile,hashlib
from pathlib import Path
sys.path.insert(0,'packages/alcor-apple-sync/python')
from worker import AppleWorker
class Bridge:
 def __init__(self): self.calls=[]
 def call(self,*args,**kwargs): self.calls.append(args); raise AssertionError('native bridge called')
 def close(self): self.calls.append(('close',))
with tempfile.TemporaryDirectory(prefix='alcor-no-apple-') as root:
 path=Path(root)/'apple';path.mkdir();(path/'synthetic-settings').write_bytes(b'unchanged')
 before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in path.iterdir()}
 os.environ['DSH_ALCOR_VERIFY_NO_APPLE']='1'; bridge=Bridge(); peer=[]
 worker=AppleWorker(path,lambda request:peer.append(request),bridge=bridge)
 assert worker.initialize(legacy_directory=Path(root)/'legacy') is True and worker.managers=={}
 for method in ('catalog','status','request_access','start_sync','configure','replace_links','disconnect','set_auto','items','lookup_item','preview','preview_items','one_time','sync','resolve_conflict','resolve_category_conflict','cancel_operation','sync_range','preview_batch','transfer_batch','unknown'):
  try:worker.invoke(method,{'kind':'calendar'})
  except RuntimeError as error:assert 'verification process' in str(error)
  else:raise AssertionError('Apple method escaped verification isolation: '+method)
 for method,args in (('preview_category_rebuild',('calendar','a','s')),('rebuild_category',('calendar','a','s',{})),('prepare_approved_export',('one_time',{'kind':'calendar'},'operation')),('run_approved_export',({'kind':'calendar'},))):
  try:getattr(worker,method)(*args)
  except RuntimeError as error:assert 'verification process' in str(error)
  else:raise AssertionError('Private Apple method escaped verification isolation: '+method)
 worker.close();assert not bridge.calls and not peer
 assert before=={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in path.iterdir()}
 absent=Path(root)/'must-not-exist';AppleWorker(absent,lambda _:None).initialize();assert not absent.exists()
 os.environ['DSH_ALCOR_VERIFY_NO_APPLE']='0'
 try:AppleWorker(Path(root)/'invalid',lambda _:None)
 except RuntimeError:pass
 else:raise AssertionError('invalid verification flag did not fail closed')
 os.environ.pop('DSH_ALCOR_VERIFY_NO_APPLE')
 # Normal startup creates only a synthetic empty root and retains ordinary catalog behavior.
 class EmptyPeer:
  def __call__(self,request): raise AssertionError('unexpected peer')
 normal=AppleWorker(Path(root)/'normal',EmptyPeer(),bridge=Bridge());normal.initialize()
 assert len(normal.invoke('catalog',{'kind':'calendar'}))>0
 normal.close()
`])
})

test('merged private worker actions reach the no-Apple guard through the actual pipe', () => {
  execFileSync(process.execPath, ['--input-type=module', '-e', `
import assert from 'node:assert/strict'
import { mkdtemp, access, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { BusinessWorker } from './packages/alcor-organizer/src/business.js'
const root=await mkdtemp(join(tmpdir(),'alcor-private-no-apple-')), absent=join(root,'must-not-exist')
const worker=new BusinessWorker(absent,{worker:new URL('./packages/alcor-apple-sync/python/worker.py',import.meta.url),onRequest(){throw Error('unexpected owner request')}})
try {
  assert.equal(await worker.call('initialize'),true)
  for(const [action,input] of [
    ['preview_category_rebuild',{kind:'calendar',apple_id:'a',source_id:'s'}],
    ['rebuild_category',{kind:'calendar',apple_id:'a',source_id:'s',snapshot:{}}],
    ['prepare_approved_export',{method:'one_time',arguments:{kind:'calendar'},operation_id:'operation'}],
    ['run_approved_export',{plan:{kind:'calendar'}}],
  ]) await assert.rejects(worker.call(action,input),/verification process/)
  await assert.rejects(access(absent),{code:'ENOENT'})
} finally { await worker.close(); await rm(root,{recursive:true,force:true}) }
`], { env: { ...process.env, DSH_ALCOR_VERIFY_NO_APPLE: '1' } })
})
