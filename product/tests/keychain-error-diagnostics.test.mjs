import test from 'node:test'
import assert from 'node:assert/strict'
import childProcess from 'node:child_process'
import { syncBuiltinESMExports } from 'node:module'
import { EventEmitter } from 'node:events'
import { keychainRequest } from '../../packages/alcor-privacy/src/keychain.js'

test('Keychain failures give a remedy without exposing native output or credentials', { skip: process.platform !== 'darwin' }, async t => {
  t.after(() => { t.mock.restoreAll(); syncBuiltinESMExports() })
  for (const [error, expected] of [
    [{ code: 'ENOENT' }, /keychain_helper_missing.*重新安装/],
    [{ code: 'EACCES' }, /keychain_helper_denied.*执行权限/],
    [{ killed: true }, /keychain_helper_timeout.*核对保存状态/],
    [{ code: 1 }, /keychain_helper_failed.*钥匙串/],
    [null, /keychain_protocol_error.*修复或更新/],
  ]) {
    t.mock.method(childProcess, 'execFile', (_file, _args, _options, callback) => {
      const stdin = new EventEmitter()
      stdin.end = () => queueMicrotask(() => callback(error && { ...error, message: 'synthetic-private-secret' }, 'synthetic-private-secret'))
      return { stdin }
    })
    syncBuiltinESMExports()
    await assert.rejects(keychainRequest({ value: 'synthetic-private-secret' }), failure => expected.test(failure.message) && !failure.message.includes('synthetic-private-secret'))
    t.mock.restoreAll()
  }
})
