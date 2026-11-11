import {build} from 'esbuild'
import {mkdir,readFile,writeFile,copyFile} from 'node:fs/promises'
import {fileURLToPath} from 'node:url'
import {join} from 'node:path'

const root=fileURLToPath(new URL('../../',import.meta.url))
const pkg=join(root,'packages/alcor-launcher')
const secretary=JSON.parse(await readFile(join(root,'packages/alcor-secretary/package.json')))
await mkdir(join(pkg,'lib'),{recursive:true})
await mkdir(join(pkg,'runtime'),{recursive:true})
await copyFile(join(root,`.local/artifacts/alcor-secretary-${secretary.version}.tgz`),join(pkg,'runtime/alcor-secretary.tgz'))
await build({entryPoints:[join(pkg,'src/index.ts')],outfile:join(pkg,'lib/index.js'),bundle:true,packages:'external',format:'esm',platform:'node',target:'node24'})
const client=await build({entryPoints:[join(pkg,'src/client.jsx')],bundle:true,external:['react','@deepseek-ai/dsh-client-ui-primitives'],platform:'browser',format:'cjs',target:'es2022',write:false})
await writeFile(join(pkg,'lib/client.js'),`window.__ModuleLoader__.load({id:"@alcor/launcher",factory(require){const module={exports:{}};const exports=module.exports;\n${client.outputFiles[0].text}\nreturn module.exports;}});\n`)
