import { fileURLToPath } from 'node:url'
import test from 'node:test'
import assert from 'node:assert/strict'
import {build} from 'esbuild'
import {createRequire} from 'node:module'

const output=await build({entryPoints:[fileURLToPath(new URL('../../packages/alcor-secretary/src/desk.jsx',import.meta.url))],bundle:true,packages:'external',platform:'node',format:'cjs',loader:{'.svg':'dataurl'},write:false})
const module={exports:{}}, require=createRequire(import.meta.url)
const primitive=new Proxy(()=>null,{get:()=>()=>null})
const React={createElement:(type,props,...children)=>({type,props,children}),useState:init=>[typeof init==='function'?init():init,()=>{}],useEffect(){},useRef:()=>({}),useSyncExternalStore:(_subscribe,snapshot)=>snapshot()}
new Function('module','exports','require',output.outputFiles[0].text)(module,module.exports,name=>name==='react'?React:name.startsWith('@deepseek-ai/')?primitive:require(name))
const {PluginPanels,SecretarySidebar}=module.exports
const source={list:()=>[
  {options:{id:'plugins',label:()=> 'Plugins'}},
  {options:{id:'schedules',label:()=> 'Schedules'}},
  {options:{id:'late-plugin',label:()=> 'Late plugin',order:30}},
  {options:{id:'early-plugin',label:()=> 'Early plugin',order:20}},
],subscribe:()=>()=>{}}
function elements(node,type){
  if(Array.isArray(node))return node.flatMap(n=>elements(n,type))
  if(!node || typeof node!=='object')return []
  return [...(node.type===type?[node]:[]),...elements(node.children,type)]
}

test('an installed app is accessible in an independent environment despite its old mode field',()=>{
  const previous=globalThis.__ALCOR_APPLICATION__
  const props={collapsed:false,drawer:false,width:240,navigate(){},startSession(){},toggleSidebar(){},
    usePanelInfo:fn=>fn({activePanelId:null}),useRemoteAccess:()=>true,renderFactorySlot:()=>null,pluginPanels:source,
    hubForm:{subscribe:()=>()=>{},getSnapshot:()=>({status:'ready',value:{productScope:'dsh'}})},t:value=>value}
  try{
    delete globalThis.__ALCOR_APPLICATION__
    assert(!elements(SecretarySidebar(props),'button').some(button=>button.props['aria-label']==='AgentHub'))
    globalThis.__ALCOR_APPLICATION__='alcor'
    assert(elements(SecretarySidebar(props),'button').some(button=>button.props['aria-label']==='AgentHub'))
  }finally{if(previous===undefined)delete globalThis.__ALCOR_APPLICATION__;else globalThis.__ALCOR_APPLICATION__=previous}
})

test('plugin controls retain registered labels, order, navigation and selection without a specific integration',()=>{
  const selected=[]
  const tree=PluginPanels({source,wide:true,panel:'early-plugin',navigate:id=>selected.push(id)})
  const buttons=elements(tree,'button')
  assert.deepEqual(buttons.map(b=>b.props['aria-label']),['Early plugin','Late plugin'])
  assert.equal(buttons[0].props['aria-current'],'page')
  buttons[1].props.onClick();assert.deepEqual(selected,['late-plugin'])
  assert.equal(elements(tree,'span').length,2)
})

test('collapsed plugin controls retain accessible names and an empty list adds no controls',()=>{
  const tree=PluginPanels({source,wide:false,panel:null,navigate(){}})
  assert.equal(elements(tree,'button')[0].props['aria-label'],'Early plugin')
  assert.equal(elements(tree,'span').length,0)
  assert.equal(PluginPanels({source:{...source,list:()=>[]},wide:true,navigate(){}}),null)
})
