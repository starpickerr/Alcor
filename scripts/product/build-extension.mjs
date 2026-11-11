import {build} from 'esbuild'
import {mkdir} from 'node:fs/promises'
import {fileURLToPath} from 'node:url'
import {join} from 'node:path'

const root=fileURLToPath(new URL('../../',import.meta.url)),pkg=join(root,'packages/alcor-extension')
await mkdir(join(pkg,'lib'),{recursive:true})
await mkdir(join(root,'.local/artifacts'),{recursive:true})
await build({entryPoints:[join(pkg,'src/index.mjs')],outfile:join(pkg,'lib/index.js'),format:'esm',platform:'node',target:'node24'})
