import { fileURLToPath } from 'node:url'
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { build } from 'esbuild'

const root = new URL('../../', import.meta.url)
const react = { createElement: (type, props, ...children) => ({ type, props, children }) }
const built = await build({ entryPoints: [fileURLToPath(new URL('packages/alcor-secretary/src/delete-preview.jsx', root))], bundle: true, packages: 'external', format: 'cjs', platform: 'node', write: false })
const module = { exports: {} }
new Function('module', 'exports', 'require', built.outputFiles[0].text)(module, module.exports, () => react)
const preview = plan => JSON.stringify(module.exports.DeletePreview({ state: { plan } }))
const targets = [{ id: 'synthetic', title: 'Synthetic' }]

test('deletion preview distinguishes Apple submission, import unlink and local-only removal', () => {
  const apple = preview({ targets, apple: { scope: 'item' } })
  assert.match(apple, /将请求 Apple 删除/)
  assert.match(apple, /结果未知.*不会重复提交/)
  assert.doesNotMatch(apple, /仅删除 Alcor 本地内容/)
  const unlink = preview({ targets, importUnlink: { source_id: 'synthetic' } })
  assert.match(unlink, /停止导入.*Apple 不变/)
  assert.doesNotMatch(unlink, /将请求 Apple 删除/)
  const local = preview({ targets })
  assert.match(local, /仅删除 Alcor 本地内容，Apple 不变/)
  assert.doesNotMatch(local, /将请求 Apple 删除/)
})

test('page failures do not promise preserved data or recommend blind deletion retries', async () => {
  for (const name of ['calendar-page.jsx', 'tasks-page.jsx']) {
    const source = await readFile(new URL(`packages/alcor-secretary/src/${name}`, root), 'utf8')
    assert.doesNotMatch(source, /删除会同步至 Apple/)
    assert.match(source, /删除未完成，请先核对 Apple 和本地状态，勿重复提交/)
  }
  const source = await readFile(new URL('packages/alcor-secretary/src/business.jsx', root), 'utf8')
  assert.doesNotMatch(source, /操作失败，请刷新核对后重试/)
  assert.match(source, /操作结果未确认，请先核对当前状态/)
})
