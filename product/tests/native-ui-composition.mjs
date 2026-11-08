import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'
// Run with the official Electron executable and ELECTRON_RUN_AS_NODE=1; no Host boot or GUI.
const app=process.env.ALCOR_OFFICIAL_COMPOSITION_APP
assert(app, 'Set ALCOR_OFFICIAL_COMPOSITION_APP to the reviewed official App')
const runtime=join(app,'Contents/Resources/app.asar/dsh/node_modules/@deepseek-ai/')
assert.equal(JSON.parse(readFileSync(runtime+'dsh-app-boot/package.json')).version,'0.2.2-rc.1')
const { loadOverlayPatches, composeEntries }=await import(pathToFileURL(runtime+'dsh-app-boot/lib/index.js'))
const root=new URL('../../',import.meta.url).pathname
const names=['apple-sync','secretary','elearning','mail','agent-hub']
const load=path=>loadOverlayPatches('synthetic',path)
const base=load(runtime+'dsh-base/cordis.patch.yml'),web=load(runtime+'dsh-web-app/cordis.patch.yml')
const alcor=names.map(n=>load(root+'packages/alcor-'+n+'/cordis.patch.yml'))
const team=load(runtime+'dsh-experimental-agent-team-profile/cordis.patch.yml')
for(const [name,layers] of [['bad',[base,...alcor,web]],['good',[base,web,...alcor]],['existing-official-team',[base,web,...alcor.slice(0,4),team,alcor[4]]],['disabled',[base,web]]]){
 const warnings=[];const entries=composeEntries(layers,s=>warnings.push(s));
 const rows=['layout','workspace','settings-general','schedule','settings-models'].map(n=>({id:n,officialEnabled:entries.some(e=>e.id==='ui-'+n&&!e.disabled),vendorEnabled:entries.some(e=>e.id==='alcor-ui-'+n&&!e.disabled)}));
 assert.equal(rows.every(r=>!r.officialEnabled&&r.vendorEnabled),name==='good'||name==='existing-official-team')
 if(name==='bad'){assert(rows.every(r=>r.officialEnabled&&r.vendorEnabled));assert.equal(warnings.filter(s=>/ui-.*not found/.test(s)).length,5)}
 else if(name==='disabled')assert(rows.every(r=>r.officialEnabled&&!r.vendorEnabled))
 else assert(!warnings.length,JSON.stringify(warnings))
 if(name==='existing-official-team'){
  for(const id of ['tool-subagent-control','tool-subagent','tool-subagent-fork'])assert.equal(entries.find(e=>e.id===id)?.disabled,true)
  for(const id of ['agent-team','tool-agent-team','ui-agent-team'])assert(entries.some(e=>e.id===id&&!e.disabled))
 }
 console.log(JSON.stringify({name,rows,warnings}))
}
