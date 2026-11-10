import { build } from 'esbuild'
import { mkdir, writeFile } from 'node:fs/promises'
await mkdir('.local/artifacts', { recursive: true })
await mkdir('packages/alcor-agent-hub/lib',{recursive:true})
await build({entryPoints:['packages/alcor-agent-hub/src/index.ts'],outfile:'packages/alcor-agent-hub/lib/index.js',bundle:true,packages:'external',format:'esm',platform:'node',target:'node24'})
const result=await build({entryPoints:['packages/alcor-agent-hub/src/client.jsx'],bundle:true,external:['react','react-dom','zod','@deepseek-ai/cordis','@deepseek-ai/dsh-client-ui-primitives'],loader:{'.css':'text'},format:'cjs',platform:'browser',write:false,target:'es2022'})
await writeFile('packages/alcor-agent-hub/lib/client.js',`window.__ModuleLoader__.load({id:"@alcor/agent-hub",factory(require){const module={exports:{}};const exports=module.exports;\n${result.outputFiles[0].text}\nreturn module.exports;}});\n`)
