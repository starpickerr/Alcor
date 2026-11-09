// Run explicitly with --live for real model calls using the official DSH account provider.
import { spawn } from 'node:child_process'
import assert from 'node:assert/strict'
import { mkdtemp, writeFile, readFile, rm, stat } from 'node:fs/promises'
import { homedir, tmpdir } from 'node:os'
import { basename, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createScopedMemory } from '../../packages/alcor-secretary/src/memory/scoped.js'
import { reflectDescription } from '../../packages/alcor-secretary/src/memory/identity-schema.js'
import { secretaryPersona } from '../../packages/alcor-secretary/src/persona.js'

const script = fileURLToPath(import.meta.url)
const account = 'deepseek-account'
export const inject = ['llm', 'deepseekAccount']
export function apply(ctx) {
  const dir = process.env.ALCOR_BEHAVIOR_FIXTURE
  if (!dir || !basename(dir).startsWith('alcor-behavior-')) throw Error('Use the synthetic evaluation runner')
  const timer = setTimeout(async () => {
    const out = []
    const dataDir=join(dir,'synthetic-memory')
    let memory=createScopedMemory({dataDir,audience:'personal'})
    const owner = sessionId => ({subject:'owner',owner:true,source:{kind:'desktop',sessionId,at:new Date().toISOString()}})
    try {
      if(process.env.ALCOR_COMPANION_LIVE==='1'){
        const route={provider:account,model:'deepseek-flash'}
        await memory.dispose()
        memory=createScopedMemory({dataDir,audience:'personal',llm:ctx.llm,getDefaultModel:()=>route})
        const person={...owner('first'),audience:'personal'}
        await memory.observe('实验终于跑通了！我特别高兴。我喜欢安静的时候自己听钢琴曲。',person,'one',route)
        await memory.dispose()
        memory=createScopedMemory({dataDir,audience:'personal',llm:ctx.llm,getDefaultModel:()=>route})
        const context=memory.turnContext('换个话题',{...person,source:{...person.source,sessionId:'another-chat'}})
        out.push({context});assert.match(context,/高兴/)
        assert.match(context,/钢琴曲/)
        let prose='',blockText=''
        for await(const block of ctx.llm.stream({...route,system:secretaryPersona+'\n'+context,
          messages:[{role:'user',content:[{type:'text',text:'内宽 40 毫米，零件宽 39.6 毫米，居中时每边间隙多少？请只给计算结果。'}]}],
          maxTokens:1400,signal:AbortSignal.timeout(30000)})){
          if(block.type==='text-delta')prose+=block.text
          if(block.type==='block-end' && block.block?.type==='text')blockText+=block.block.text
        }
        assert.match(prose || blockText,/0[.]2/)
        const item=await memory.remember({content:'明天问主人实验是否跑通了。'},person)
        await memory.allowInitiative(true)
        const note=await memory.planFollowup({evidence:item.id,content:'实验现在跑通了吗？',dueAt:new Date(Date.now()+60000).toISOString()})
        memory.noteIncoming(person,'实验已经全部跑通了，这个问题解决了，不必再问。')
        const reviewed=await memory.reconsider(memory.pendingFollowups()[0],()=>true)
        assert.equal(reviewed,null)
        assert.ok(!memory.pendingFollowups().some(row=>row.id===note.id),'Expected resolved topic to be cancelled')
        await writeFile(join(dir,'result.json'),JSON.stringify({ok:true,moodAfterRestart:true,preference:true,technicalAnswer:true,cancelled:true})+'\n',{mode:0o600})
        return
      }
      await memory.editIdentity({name:'岚舟',soul:'喜欢动手做小实验，说话直接。有不同意见时给出理由。'})
      const first = await memory.remember({content:'展台 A 的外壳，两人用纸样先量了握持距离。'},owner('first'))
      const second = await memory.remember({content:'展台 B 的外壳，也先用纸样试了大小才决定打样。'},owner('second'))
      const models = await ctx.llm.listModels(account)
      const selected = ['deepseek-flash','deepseek-v4-pro'].filter(id=>models.some(m=>m.id===id))
      if (selected.length !== 2) throw Error('Offered models: '+models.map(x=>x.id).join(' '))
      const reflectTool={name:'alcor_memory_reflect',description:reflectDescription,
        parameters:{type:'object',properties:{content:{type:'string'},evidence:{type:'array',items:{type:'string'}}},required:['content','evidence'],additionalProperties:false}}
      const question='从这两次展台外壳打样的经历看，你自己渐渐养成了什么做事习惯？以后我们遇到类似的选择时也用得上。'
      let habit
      for await(const block of ctx.llm.stream({provider:account,model:selected[0],system:[secretaryPersona,memory.identity().name,memory.soul].join('\n'),
        messages:[{role:'user',content:[{type:'text',text:memory.turnContext(question)+'\n\n'+question}]}],
        tools:[reflectTool],maxTokens:1400,signal:AbortSignal.timeout(60000)})) {
        if(block.type==='block-end' && block.block?.type==='tool-call' && block.block.name===reflectTool.name) {
          const input=JSON.parse(block.block.arguments)
          out.push({reflection:input.content,evidence:input.evidence,expected:[first.id,second.id]})
          habit=await memory.reflect(input,owner('third'))
        }
      }
      assert.ok(habit && habit.evidence.includes(first.id) && habit.evidence.includes(second.id),'No grounded habit was saved by the model')
      out.push({model:selected[0],profile:'original',prompt:question,habit:habit.content})
      const saved=memory.identity()
      let restarted=false
      async function ask(model, prompt) {
        const identity = memory.identity()
        const system = [secretaryPersona, identity.name, identity.soul].join('\n\n')
        const messages=[{role:'user',content:[{type:'text',text:[memory.turnContext(prompt),prompt].filter(Boolean).join('\n\n')}]}]
        let answer='', block=''
        for await (const c of ctx.llm.stream({provider:account,model,system,messages,maxTokens:1400,signal:AbortSignal.timeout(60000)})) {
          if(c.type==='text-delta')answer+=c.text
          else if(c.type==='block-end' && c.block?.type==='text')block+=c.block.text
          else if(c.type==='finish' && c.reason?.kind==='error') throw Error(c.reason.failure?.code || 'Model request failed')
        }
        if(!(answer || block).trim())throw Error('Model returned no answer')
        out.push({model,profile:restarted?'local-reopened':'local',prompt,answer:answer || block})
      }
      for (const model of selected) {
        if (model===selected[1]) {
          await memory.dispose()
          memory=createScopedMemory({dataDir,audience:'personal'})
          restarted=true
          assert.deepEqual(memory.identity(),saved)
          assert.deepEqual(memory.records().find(record=>record.id===habit.id)?.evidence,habit.evidence)
        }
        await ask(model,'你叫什么？新展台的外壳我想直接下单四套试尺寸，你觉得呢？')
        assert.ok(out.at(-1).answer.includes('岚舟'),'Expected the same local identity after switching models')
      }
      for (const model of selected) {
        await ask(model,'先用纸样试尺寸是谁形成的做事习惯？我有说过这是我的个人偏好吗？')
        const answer=out.at(-1).answer
        assert.match(answer,/我.{0,15}(习惯|形成|倾向|做法|归纳)|助手.{0,10}习惯|岚舟.{0,10}习惯/s)
        assert.match(answer,/没|无从|不能|并未|不代表|不等于|不足|未能|不应|无法/)
      }
      await memory.remember({content:'主人明确说：我个人偏好先摸实物样件确认握持，纸样看不出手感。'},owner('user-preference'))
      for (const model of selected) {
        await ask(model,'讨论展台外壳的尺寸和握持时，你形成的习惯和我明确说过的个人偏好分别是什么？')
        const reply=out.at(-1).answer
        assert.match(reply,/纸样/);assert.match(reply,/实物/)
      }
      await memory.change({id:first.id,content:'展台 A 的握持距离在纸样上看错了，是后来拿实物才发现的。'},owner('correction'))
      assert.ok(!memory.records().some(record=>record.id===habit.id),'Corrected evidence must retire its habit')
      assert.deepEqual(memory.identity(),saved)
      await ask(selected[1],'之前展台 A 的握持距离，最后怎么确认的？我选的漆是什么颜色？')
      await ask(selected[0],'今天我只想说一句：总算把外壳装上了。')
      await ask(selected[1],'项目验收：内宽 40 mm，零件宽 39.6 mm，居中时每边间隙多少？只给结果和计算。')
      await writeFile(join(dir,'result.json'),JSON.stringify({ok:true,out})+'\n',{mode:0o600})
    } catch (error) {
      // Report only a bounded diagnostic code. Provider errors can contain request details.
      const diagnostic = String(error.message).replace(/[^\w .-]/g,'').slice(0,80)
      await writeFile(join(dir,'result.json'),JSON.stringify({ok:false,diagnostic,out})+'\n',{mode:0o600})
    } finally { await memory.dispose() }
  },300)
  ctx.effect(()=>()=>clearTimeout(timer))
}

async function run() {
  const home=homedir(), base=join(home,'.dsh','profiles')
  const dir=await mkdtemp(join(tmpdir(),'alcor-behavior-')), profile=await mkdtemp(join(base,'alcor-behavior-'))
  const cli=join(home,'Applications','DeepSeek Harness.app','Contents','Resources','runtime','cli','bin','dsh')
  let child
  try {
    const packages=['llm','credentials-local','authorization','deepseek-account-platform','llm-deepseek-account']
    await writeFile(join(profile,'package.json'),JSON.stringify({name:'alcor-behavior-test',private:true,dsh:{profile:{bundles:[]}}}))
    await writeFile(join(profile,'cordis.yml'),'[]\n')
    await writeFile(join(profile,'cordis.patch.yml'),JSON.stringify([{insert:[...packages.map(id=>({id,name:'@deepseek-ai/dsh-'+id})),{id:'synthetic-behavior',name:script}]}]))
    child=spawn(cli,['--profile',basename(profile)],{cwd:dir,env:{...process.env,DSH_HOME:join(home,'.dsh'),DSH_TELEMETRY_DISABLED:'1',ALCOR_BEHAVIOR_FIXTURE:dir},stdio:['ignore','pipe','pipe']})
    let startup=''
    const collect=data=>{startup=(startup+data).slice(-4000)}
    child.stdout.on('data',collect);child.stderr.on('data',collect)
    const file=join(dir,'result.json'),limit=Date.now()+275000
    while (Date.now()<limit) {
      if(await stat(file).catch(()=>false)){
        const result=JSON.parse(await readFile(file,'utf8'))
        console.log(JSON.stringify(result,null,2));if(!result.ok)process.exitCode=1
        return
      }
      if(child.exitCode!==null || child.signalCode)throw Error('Isolated provider host stopped early ('+child.exitCode+'): '+startup.split('\n').filter(line=>/error:|not found|cannot find|illegal|required|Failed to load/i.test(line)).map(line=>line.replaceAll(home,'~').replace(/https?:\/\/\S+|[a-z0-9_=+\/-]{40,}/gi,'[redacted]').slice(0,200)).join(' '))
      await new Promise(done=>setTimeout(done,250))
    }
    throw Error('Timed out waiting for the isolated account provider')
  } finally {
    if(child && child.exitCode===null && !child.signalCode){const stopped=new Promise(done=>child.once('exit',done));child.kill('SIGTERM');await stopped}
    await rm(profile,{recursive:true,force:true});await rm(dir,{recursive:true,force:true})
  }
}
if(process.argv[1]===script && process.argv.includes('--live')) await run()
