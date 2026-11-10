import {build} from 'esbuild'
import {mkdir,writeFile} from 'node:fs/promises'

const directory='packages/alcor-plugin-info'
await mkdir(directory+'/lib',{recursive:true})
await mkdir('.local/artifacts',{recursive:true})
await build({entryPoints:[directory+'/src/index.ts'],outfile:directory+'/lib/index.js',bundle:true,packages:'external',format:'esm',platform:'node',target:'node24'})
const result=await build({entryPoints:[directory+'/src/client.jsx'],bundle:true,external:['react'],platform:'browser',format:'cjs',target:'es2022',write:false})
await writeFile(directory+'/lib/client.js',`window.__ModuleLoader__.load({id:"@alcor/plugin-info",factory(require){const module={exports:{}};const exports=module.exports;\n${result.outputFiles[0].text}\nreturn module.exports;}});\n`)
