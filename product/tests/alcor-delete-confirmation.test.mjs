import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, readFile, rm } from 'node:fs/promises'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'
import { build } from 'esbuild'

test('confirmation subprocess accepts only this request reply; malformed, denied, disconnected and unavailable fail closed', { skip: process.platform !== 'darwin' }, async t => {
  const output = await mkdtemp(join(process.cwd(), '.alcor-confirm-test-'))
  t.after(async () => { delete globalThis.__alcorConfirmSynthetic; await rm(output, { recursive: true, force: true }) })
  const file = join(output, 'confirmation.mjs')
  await build({ entryPoints: ['packages/alcor-secretary/src/mac-delete-confirmation.js'], outfile: file, bundle: true,
    format: 'esm', platform: 'node', logLevel: 'silent', plugins: [{ name: 'synthetic-child-only', setup(builder) {
      builder.onResolve({ filter: /^node:child_process$/ }, () => ({ path: 'child', namespace: 'synthetic' }))
      builder.onLoad({ filter: /.*/, namespace: 'synthetic' }, () => ({ contents: `
        import { EventEmitter } from 'node:events'
        export function spawn(path, args, options) {
          const state = globalThis.__alcorConfirmSynthetic; Object.assign(state, { path, args, options })
          const child = new EventEmitter(); child.stdout = new EventEmitter(); child.stdin = new EventEmitter()
          child.kill = () => { state.killed = true; queueMicrotask(() => child.emit('close', 1)) }
          options.signal.addEventListener('abort', () => child.emit('error', new Error('aborted')), { once: true })
          child.stdin.end = input => { state.request = JSON.parse(input); queueMicrotask(() => {
            if (state.mode === 'error') child.emit('error', new Error('unavailable'))
            else if (state.mode !== 'waiting') {
              child.stdout.emit('data', state.mode === 'oversize' ? 'x'.repeat(5000) : state.mode === 'malformed' ? 'not json' : JSON.stringify({ version: 1,
                operation_id: state.mode === 'wrong-operation' ? 'b'.repeat(64) : state.request.operation_id,
                outcome: state.mode === 'confirmed' ? 'confirmed' : 'cancelled' }))
              if (!state.killed) child.emit('close', state.mode === 'exit-error' ? 1 : 0)
            }
          }) }
          return child
        }` }))
    } }] })
  const { confirmMacDeletion } = await import(pathToFileURL(file))
  const request = { version: 1, operation_id: 'a'.repeat(64), detail: 'Synthetic exact target' }
  for (const mode of ['confirmed', 'cancelled', 'wrong-operation', 'malformed', 'oversize', 'exit-error', 'error']) {
    const state = globalThis.__alcorConfirmSynthetic = { mode }, controller = new AbortController()
    if (mode === 'error') await assert.rejects(() => confirmMacDeletion(request, controller.signal), /不可用/)
    else assert.equal(await confirmMacDeletion(request, controller.signal), mode === 'confirmed')
    assert.deepEqual(state.request, request); assert.deepEqual(state.args, [])
    assert.match(state.path, /native\/Alcor Confirm.app\/Contents\/MacOS\/AlcorConfirm$/)
    assert.equal(state.options.timeout, 245000); assert.equal(state.options.signal, controller.signal)
  }
  globalThis.__alcorConfirmSynthetic = { mode: 'waiting' }
  const controller = new AbortController(), result = confirmMacDeletion(request, controller.signal)
  controller.abort(); await assert.rejects(() => result, /取消/)
})

test('core confirmation has no Apple or EventKit dependency and is packaged with secretary', async () => {
  const source = await readFile('packages/alcor-secretary/native/Confirm.swift', 'utf8')
  assert.doesNotMatch(source, /import EventKit|EKEventStore|requestAccess|saveEvent|removeEvent/)
  assert(source.indexOf('guard data.count') < source.indexOf('NSApplication.shared'))
  assert.match(source, /response == \.alertSecondButtonReturn/)
  assert.match(source, /timeInterval: 240/)
  assert.equal(source.split('app.finishLaunching()').length - 1, 1)
  assert(source.indexOf('app.finishLaunching()') < source.indexOf('alert.runModal()'))
  assert(source.includes('alert.buttons[0].keyEquivalent = "\\r"'))
  assert(source.includes('alert.buttons[1].keyEquivalent = ""'))
  const pkg = JSON.parse(await readFile('packages/alcor-secretary/package.json', 'utf8'))
  assert(pkg.files.includes('native')); assert.equal(pkg.dependencies?.['@alcor/apple-sync'], undefined)
  assert.match(await readFile('scripts/product/build-client.mjs', 'utf8'), /build-confirmation.mjs/)
})
