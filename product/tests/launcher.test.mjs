import test from 'node:test'
import assert from 'node:assert/strict'
import {fileURLToPath} from 'node:url'
import {build} from 'esbuild'
import {createRequire} from 'node:module'

const source=new URL('../../packages/alcor-launcher/src/index.ts',import.meta.url)
const output=await build({entryPoints:[fileURLToPath(source)],bundle:true,packages:'external',platform:'node',target:'node24',format:'cjs',write:false,
  define:{'import.meta.url':JSON.stringify(source.href)}})
const module={exports:{}}
new Function('module','exports','require',output.outputFiles[0].text)(module,module.exports,createRequire(import.meta.url))
const Launcher=module.exports.default

test('only an owner operation can open a local application',async()=>{
  for(const principal of [undefined,{kind:'agent'},{kind:'remote'}]){
    const service={ctx:{invocation:{peer:{principal}}},openDesktop:()=>assert.fail('must not launch')}
    await assert.rejects(Launcher.prototype.open.call(service),/所有者/)
  }
})

test('repeated owner clicks share pending startup and a failure permits a later retry',async()=>{
  let finish,calls=0
  const result=new Promise(resolve=>{finish=resolve})
  const service={ctx:{invocation:{peer:{principal:{kind:'owner'}}}},openDesktop:()=>{calls++;return result}}
  const a=Launcher.prototype.open.call(service),b=Launcher.prototype.open.call(service)
  assert.equal(calls,1);finish({opened:true})
  assert.deepEqual(await Promise.all([a,b]),[{opened:true},{opened:true}])
  assert.equal(service.opening,undefined)
  service.openDesktop=async()=>{throw Error('startup failed')}
  await assert.rejects(Launcher.prototype.open.call(service),/startup failed/)
  assert.equal(service.opening,undefined)
  service.openDesktop=async()=>({opened:true})
  assert.deepEqual(await Launcher.prototype.open.call(service),{opened:true})
})
