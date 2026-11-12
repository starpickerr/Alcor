import { automationUiPlugin } from './automation-ui.mjs'
import { verifyNativeUi } from './build-native-ui.mjs'
import './build-confirmation.mjs'
import { build } from 'esbuild'
import { mkdir, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = fileURLToPath(new URL('../../', import.meta.url))
await verifyNativeUi()
const directory = resolve(root, 'packages/alcor-secretary')
await mkdir(resolve(directory, 'lib'), { recursive: true })
await mkdir(resolve(root, '.local/artifacts'), { recursive: true })
await build({ entryPoints: [resolve(directory, 'src/index.ts'), resolve(directory, 'src/tools.js'), resolve(directory, 'src/briefing-agent.js'), resolve(directory, 'src/elearning.js')], outdir: resolve(directory, 'lib'), bundle: true, packages: 'external', format: 'esm', platform: 'node', target: 'node24' })
await build({ entryPoints: [resolve(root, 'packages/alcor-organizer/src/index.ts')], outfile: resolve(directory, 'lib/organizer.js'), bundle: true, packages: 'external', format: 'esm', platform: 'node', target: 'node24' })
const result = await build({ plugins: [automationUiPlugin()], absWorkingDir: root, metafile: true, entryPoints: [resolve(directory, 'src/client.jsx')], bundle: true, external: ['react', 'react-dom', '@deepseek-ai/dsh-client-store', 'zod', '@deepseek-ai/cordis', '@deepseek-ai/dsh-client-ui-primitives'], loader: { '.css': 'text', '.svg': 'dataurl' }, format: 'cjs', platform: 'browser', write: false, target: 'es2022' })
const legacyInputs = Object.keys(result.metafile.inputs).filter(name => /^(desktop|agent|application|channel|core)\//.test(name))
if (legacyInputs.length) throw new Error(`Native Alcor client imports legacy runtime files: ${legacyInputs.join(', ')}. Migrate their implementation into packages/ first.`)
await writeFile(resolve(directory, 'lib/client.js'), `window.__ModuleLoader__.load({id:"@alcor/secretary",factory(require){const module={exports:{}};const exports=module.exports;\n${result.outputFiles[0].text}\nreturn module.exports;}});\n`)
