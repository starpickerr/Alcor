import test from 'node:test'
import assert from 'node:assert/strict'
import {mkdtempSync,rmSync} from 'node:fs'
import {tmpdir} from 'node:os'
import {join} from 'node:path'
import {DatabaseSync} from 'node:sqlite'
import {createSpaceDiary,replyExperience,stickerExperience} from '../../scripts/product/alcor-space-diary.js'

const hour=3600_000
function fixture(t){
  const dir=mkdtempSync(join(tmpdir(),'alcor-diary-'))
  t.after(()=>rmSync(dir,{recursive:true,force:true}))
  let time=Date.now()
  const file=join(dir,'space.sqlite')
  const published=[],evaluated=[]
  const open=(overrides={})=>createSpaceDiary({
    file,account:'one',now:()=>time,active:()=>true,
    decide:async facts=>{evaluated.push(facts);return '今天击了个掌。'},
    publish:async content=>{published.push(content);return {tid:'42'}},...overrides,
  })
  return {open,file,published,evaluated,advance:hours=>{time+=hours*hour}}
}

test('delivered experiences are categorized locally, deduplicated, and used only by the same account',async t=>{
  const f=fixture(t)
  const diary=f.open()
  const fact=replyExperience('私人原文和昵称：帮我调试代码')
  assert.equal(fact,'在 QQ 回复了关于编程的话题')
  assert.equal(stickerExperience('fav:private'),'在 QQ 用表情回应')
  diary.record('private:123','7',fact)
  diary.record('private:123','7',fact)
  diary.record('group:456','8',stickerExperience('cheer'))
  const other=f.open({account:'two'})
  await other.tick()
  assert.equal(f.evaluated.length,0)
  await Promise.all([diary.tick(),diary.tick()])
  assert.deepEqual(f.evaluated,[[fact,'用小熊击掌图庆祝']])
  assert.equal(f.published.length,1)
  const db=new DatabaseSync(f.file,{readOnly:true})
  assert.equal(db.prepare('SELECT status,tid FROM diary WHERE account=?').get('one').tid,'42')
  assert.doesNotMatch(JSON.stringify(db.prepare('SELECT * FROM experiences').all()),/private:123|group:456|私人原文/)
  db.close();other.close();diary.close()
})

test('the model can pass and revoked authorization cannot publish a pending choice',async t=>{
  const f=fixture(t)
  const diary=f.open({decide:async()=>null})
  diary.record('g',1,'用小熊击掌图庆祝')
  await diary.tick();diary.close()
  f.advance(2)
  let enabled=true
  const next=f.open({active:()=>enabled,decide:async facts=>{
    assert.deepEqual(facts,['用小熊歪头图接梗'])
    enabled=false
    return '歪了歪头'
  }})
  await next.tick()
  next.record('g',2,'用小熊歪头图接梗')
  await next.tick()
  assert.equal(enabled,false)
  assert.deepEqual(f.published,[])
  next.close()
})

test('an uncertain send survives restart without a second attempt and aged experiences expire',async t=>{
  const f=fixture(t)
  let attempts=0
  const diary=f.open({publish:async()=>{attempts++;throw Error('lost receipt')}})
  diary.record('g',1,'用小熊歪头图接梗')
  await diary.tick();diary.close()
  const restarted=f.open()
  f.advance(2)
  restarted.record('g',2,'用小熊击掌图庆祝')
  await restarted.tick()
  assert.deepEqual(f.published,[])
  f.advance(25)
  await restarted.tick()
  assert.equal(attempts,1)
  assert.deepEqual(f.published,[])
  restarted.record('g',3,'用小熊晚安图道别')
  await restarted.tick()
  assert.deepEqual(f.evaluated.at(-1),['用小熊晚安图道别'])
  assert.equal(f.published.length,1)
  restarted.close()
})
