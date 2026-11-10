import { createHash } from 'node:crypto'
import { readFile, writeFile, cp, mkdir, mkdtemp, rm } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import { resolve, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import {adaptQQVision} from './qq-vision-adapter.mjs'
import {adaptQQOwner} from './qq-mode-adapter.mjs'
import { adaptQQ, adaptedFiles } from './qq-alcor-adapter.mjs'
const root = fileURLToPath(new URL('../../', import.meta.url))
const pin = JSON.parse(await readFile(join(root, 'product/qq-source.lock.json'), 'utf8'))
const archive = process.env.ALCOR_QQ_SOURCE
if (!archive) throw Error('Set ALCOR_QQ_SOURCE to the fixed QQ source archive; no installed package is edited')
const sha = data => createHash('sha256').update(data).digest('hex')
if (sha(await readFile(archive)) !== pin.sha256) throw Error('QQ source archive does not match the fixed source')
await mkdir(join(root, '.local/artifacts'), { recursive: true })
const temp = await mkdtemp(join(root, '.local/qq-build-'))
try {
  execFileSync('tar', ['-xzf', resolve(archive), '-C', temp])
  const directory = join(temp, 'package')
  for (const file of [...adaptedFiles,'lib/registry.js']) {
    const source=await readFile(join(directory,file),'utf8')
    await writeFile(join(directory,file),adaptQQVision(file,adaptQQOwner(file,adaptQQ(file,source))))
  }
  const patch=join(directory,'cordis.patch.yml')
  await writeFile(patch,(await readFile(patch,'utf8'))+`
- insert:
  - id: preset-qq-dsh-chat
    name: '@deepseek-ai/dsh-agent-preset'
    config:
      id: qq-dsh-chat
      name: DSH QQ 对话
      order: 10001
      plugins:
        - id: persona
          name: '@deepseek-ai/dsh-persona'
          config:
            complete: true
            includeRuntimeContext: false
            prefix: '你是 DSH 工作台通过 QQ 提供的对话助手。结合当前聊天回答，不冒充用户或其他助手。'
`)

  await cp(join(root,'scripts/product/qq-stickers'),join(directory,'lib/stickers'),{recursive:true})
  await cp(join(root,'scripts/product/qq-social.mjs'),join(directory,'lib/alcor-social.js'))
  await cp(join(root,'scripts/product/qq-offline-inbox.mjs'),join(directory,'lib/alcor-offline-inbox.js'))
  const diary=await readFile(join(root,'scripts/product/alcor-space-diary.js'))
  await writeFile(join(directory,'lib/alcor-space-diary.js'),diary)
  const info=await readFile(join(root,'scripts/product/qq-conversation-info.mjs'))
  await writeFile(join(directory,'lib/alcor-conversation-info.js'),info)
  await writeFile(join(directory,'lib/alcor-platform-history.js'),await readFile(join(root,'scripts/product/qq-platform-history.mjs')))
  const replies=await readFile(join(root,'scripts/product/qq-replies.mjs'))
  await writeFile(join(directory,'lib/alcor-replies.js'),replies)
  const history=await readFile(join(root,'packages/alcor-secretary/src/qq-history.js'))
  await writeFile(join(directory,'lib/alcor-history.js'),history)
  const manifest = JSON.parse(await readFile(join(directory, 'package.json'), 'utf8'))
  manifest.version = '0.9.19-alcor.21'
  delete manifest.peerDependencies['dsh-lan-memory']
  if (manifest.peerDependenciesMeta) delete manifest.peerDependenciesMeta['dsh-lan-memory']
  manifest.peerDependencies['@alcor/secretary'] = '>=0.1.98'
  manifest.peerDependenciesMeta ??= {}
  for (const name of Object.keys(manifest.peerDependencies)) {
    if (name.startsWith('@deepseek-ai/') || name === '@alcor/secretary') {
      manifest.peerDependenciesMeta[name] = { ...manifest.peerDependenciesMeta[name], optional: true }
    }
  }
  await writeFile(join(directory, 'package.json'), JSON.stringify(manifest, null, 2) + '\n')
  await writeFile(join(directory, 'ALCOR-SOURCE.json'), JSON.stringify({ ...pin, visionAdapter:sha(await readFile(join(root,'scripts/product/qq-vision-adapter.mjs'))), modeAdapter:sha(await readFile(join(root,'scripts/product/qq-mode-adapter.mjs'))), diary:sha(diary), social:sha(await readFile(join(root,'scripts/product/qq-social.mjs'))), conversationInfo:sha(info), participation:sha(await readFile(join(root,'scripts/product/qq-participation.mjs'))), platformHistory:sha(await readFile(join(root,'scripts/product/qq-platform-history.mjs'))), historyDecoder:sha(history), replies:sha(replies), adapter: sha(await readFile(join(root, 'scripts/product/qq-alcor-adapter.mjs'))) }, null, 2) + '\n')
  manifest.files.push('ALCOR-SOURCE.json', 'LICENSE')
  await writeFile(join(directory, 'package.json'), JSON.stringify(manifest, null, 2) + '\n')
  execFileSync('npm', ['pack', '--ignore-scripts', '--pack-destination', join(root, '.local/artifacts')], { cwd: directory, stdio: 'inherit' })
} finally { await rm(temp, { recursive: true, force: true }) }
