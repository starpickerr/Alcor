import './build-eventkit.mjs'
import { build } from 'esbuild'
import { copyFile, mkdir, writeFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { join } from 'node:path'

const root = fileURLToPath(new URL('../../', import.meta.url))
const directory = join(root, 'packages/stella-apple-sync')
await mkdir(join(directory, 'lib'), { recursive: true })
await mkdir(join(root, '.local/artifacts'), { recursive: true })
await copyFile(join(root, 'product/DSH-LICENSE.txt'), join(directory, 'DSH-LICENSE.txt'))
await build({ entryPoints: [join(directory, 'src/index.ts')], outdir: join(directory, 'lib'), bundle: true, packages: 'external', format: 'esm', platform: 'node', target: 'node24' })
const result = await build({ entryPoints: [join(directory, 'src/client.jsx')], bundle: true, external: ['react', 'react-dom', 'zod', '@deepseek-ai/cordis', '@deepseek-ai/dsh-client-ui-primitives'], loader: { '.css': 'text' }, format: 'cjs', platform: 'browser', write: false, target: 'es2022' })
await writeFile(join(directory, 'lib/client.js'), `window.__ModuleLoader__.load({id:"@stella/apple-sync",factory(require){const module={exports:{}};const exports=module.exports;\n${result.outputFiles[0].text}\nreturn module.exports;}});\n`)
