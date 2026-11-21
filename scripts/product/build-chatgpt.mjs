import { build } from 'esbuild'
import { copyFile, mkdir, writeFile } from 'node:fs/promises'

const directory = 'packages/dsh-chatgpt'
await mkdir(directory + '/lib/licenses', { recursive: true })
await mkdir('.local/artifacts', { recursive: true })
await build({
  entryPoints: [directory + '/src/index.js'], outfile: directory + '/lib/index.js',
  bundle: true, external: ['@deepseek-ai/*', 'zod'], platform: 'node', target: 'node24', format: 'esm',
})
const client = await build({
  entryPoints: [directory + '/src/client.jsx'], bundle: true, external: ['react', '@deepseek-ai/dsh-client-ui-primitives'],
  platform: 'browser', target: 'es2022', format: 'cjs', write: false,
})
await writeFile(directory + '/lib/client.js',
  'window.__ModuleLoader__.load({id:"@alcor/chatgpt",factory(require){const module={exports:{}};const exports=module.exports;\n'
  + client.outputFiles[0].text + '\nreturn module.exports;}});\n')
for (const [name, license] of [['jose', 'LICENSE.md'], ['eventsource-parser', 'LICENSE']]) {
  await copyFile('node_modules/' + name + '/' + license, directory + '/lib/licenses/' + name + '.txt')
}
