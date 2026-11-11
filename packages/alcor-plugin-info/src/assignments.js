// The editable field belongs to the bundle which enforces it, and survives UI extension removal.
export const assignments = {
  '@alcor/mail': {entry:'alcor-mail', initial:'both'},
  '@alcor/elearning': {entry:'alcor-elearning', initial:'both'},
  '@alcor/apple-sync': {entry:'alcor-apple-sync', initial:'alcor'},
  '@alcor/agent-hub': {entry:'agent-hub', initial:'dsh'},
  'dsh-onebot-qq': {entry:'dsh-onebot', initial:'alcor', exclusive:true},
}
export const modeNames={alcor:'Alcor',dsh:'DSH 工作台',both:'两边共用'}
export function applicationEnvironment() {
  const value=globalThis.__ALCOR_APPLICATION__ ?? globalThis.process?.env?.ALCOR_APPLICATION
  return ['alcor','dsh'].includes(value) ? value : undefined
}
export function publishApplication(ctx) {
  const application=applicationEnvironment()
  if(application)ctx.on('webserver/index-inject',rows=>{
    const existing=rows.find(row=>row.kind==='global' && row.name==='__ALCOR_APPLICATION__')
    if(existing && existing.value!==application)throw Error('运行环境身份不一致。')
    if(!existing)rows.push({kind:'global',name:'__ALCOR_APPLICATION__',value:application})
  })
}
export function permits(value, mode){return value==='both' || value===mode}
export function selectedScope(ctx,name) {
  if(applicationEnvironment())return 'both'
  const spec=assignments[name]
  const row=ctx.get?.('configEditor')?.entries?.().find(e=>e.options.id===spec.entry)
  return row?.options.config?.productScope ?? spec.initial
}
export function sessionMode(ctx,agent) {
  let session=agent?.session
  const seen=new Set()
  while(session?.header?.parentSession){
    const agents=ctx.get('agents'), parent=agents?.get(session.header.parentSession)
    if(!parent || seen.has(session.id) || !agents.isOwnedBy(session.id,parent))return undefined
    seen.add(session.id);session=parent.session
  }
  if(!session || session.header?.origin==='subagent')return undefined
  if(/^onebot-(private|group)-/.test(session.id))return 'alcor'
  if(/^workbench-onebot-/.test(session.id))return 'dsh'
  const preset=ctx.get('agentPresets')?.presetForSession(session) ?? session.header?.agentPreset
  if(!preset)return undefined
  return ['alcor','stella','alcor-delivery','alcor-briefing','stella-briefing','qq-group-chat'].includes(preset)?'alcor':'dsh'
}
