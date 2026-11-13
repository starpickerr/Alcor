import test from 'node:test'
import assert from 'node:assert/strict'
import { build } from 'esbuild'
import { automationUiPlugin } from '../../scripts/product/automation-ui.mjs'

test('Alcor owns its retained manager factory without applying the official schedule plugin', async () => {
  const result = await build({ stdin: { contents: "export { registerAutomationUi } from 'alcor:automation-ui'", resolveDir: process.cwd() }, plugins: [automationUiPlugin()], bundle: true, packages: 'external', platform: 'node', format: 'cjs', write: false })
  assert.doesNotMatch(result.outputFiles[0].text, /\.useAnchoredPosition/)
  assert.doesNotMatch(result.outputFiles[0].text, /@deepseek-ai\/dsh-client-ui-schedule\/.*?\.module\.css/)
  assert.match(result.outputFiles[0].text, /@alcor\/automation-ui\/TaskManagerPage\.module\.css/)
  const module = { exports: {} }
  const generic = new Proxy(() => {}, { get: () => generic })
  new Function('module', 'exports', 'require', result.outputFiles[0].text)(module, module.exports, () => generic)
  const factories = [], locales = [], dispose = []
  let factoryReleased=false,localeReleased=false
  module.exports.registerAutomationUi({ effect: fn => { dispose.push(fn()) }, locale: { register: (name) => { locales.push(name); return () => { localeReleased=true } } }, slots: { registerFactory: (options, component) => { factories.push({ options, component }); return () => { factoryReleased=true } } } })
  assert.deepEqual(locales, ['alcor.automation'])
  assert.deepEqual(factories.map(row => row.options.name), ['alcor.automation.view'])
  assert.equal(typeof factories[0].component, 'function')
  assert.equal(factories[0].options.locale, 'alcor.automation')
  assert.equal('apply' in module.exports, false)
  assert.equal(dispose.length,2)
  for (const release of dispose.reverse()) release()
  assert.equal(factoryReleased,true); assert.equal(localeReleased,true)
})
