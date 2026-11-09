import { verifyNativeUi } from './build-native-ui.mjs'
import './build-confirmation.mjs'
import { build } from 'esbuild'
import { mkdir, writeFile, readFile, copyFile } from 'node:fs/promises'
import { createHash } from 'node:crypto'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = fileURLToPath(new URL('../../', import.meta.url))
await verifyNativeUi()
const directory = resolve(root, 'packages/alcor-secretary')
const memorySource = resolve(directory, 'src/memory')
const memoryPin = JSON.parse(await readFile(resolve(memorySource, 'UPSTREAM.json'), 'utf8'))
for (const [name, digest] of Object.entries(memoryPin.sha256)) {
  if (createHash('sha256').update(await readFile(resolve(memorySource, 'engine', name))).digest('hex') !== digest) throw Error('LAN memory source differs from its fixed origin: ' + name)
}
await copyFile(resolve(memorySource, 'LICENSE.txt'), resolve(directory, 'LAN-MEMORY-LICENSE.txt'))
await copyFile(resolve(memorySource, 'UPSTREAM.json'), resolve(directory, 'LAN-MEMORY-SOURCE.json'))
await mkdir(resolve(directory, 'lib'), { recursive: true })
await mkdir(resolve(root, '.local/artifacts'), { recursive: true })
await build({ entryPoints: [resolve(directory, 'src/index.ts'), resolve(directory, 'src/tools.js'), resolve(directory, 'src/briefing-agent.js'), resolve(directory, 'src/elearning.js')], outdir: resolve(directory, 'lib'), bundle: true, packages: 'external', format: 'esm', platform: 'node', target: 'node24' })
await build({ entryPoints: [resolve(root, 'packages/alcor-organizer/src/index.ts')], outfile: resolve(directory, 'lib/organizer.js'), bundle: true, packages: 'external', format: 'esm', platform: 'node', target: 'node24' })
const result = await build({ absWorkingDir: root, metafile: true, entryPoints: [resolve(directory, 'src/client.jsx')], bundle: true, external: ['react', 'react-dom', 'zod', '@deepseek-ai/cordis', '@deepseek-ai/dsh-client-ui-primitives'], loader: { '.css': 'text', '.svg': 'dataurl' }, format: 'cjs', platform: 'browser', write: false, target: 'es2022' })
const legacyInputs = Object.keys(result.metafile.inputs).filter(name => /^(desktop|agent|application|channel|core)\//.test(name))
if (legacyInputs.length) throw new Error(`Native Alcor client imports legacy runtime files: ${legacyInputs.join(', ')}. Migrate their implementation into packages/ first.`)
await writeFile(resolve(directory, 'lib/client.js'), `window.__ModuleLoader__.load({id:"@alcor/secretary",factory(require){const module={exports:{}};const exports=module.exports;\n${result.outputFiles[0].text}\nreturn module.exports;}});\n`)
