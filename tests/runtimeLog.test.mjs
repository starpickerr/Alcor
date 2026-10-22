import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync, rmSync, existsSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { RuntimeLog } from '../desktop/src/main/runtime-log.ts'

test('shell log rotates with bounded retention and redacts before disk', () => {
  const directory = mkdtempSync(path.join(os.tmpdir(), 'stella-log-'))
  try {
    const log = new RuntimeLog(directory, 300, 2)
    for (let i = 0; i < 15; i++) log.event('renderer', 'ERROR', `event ${i} password="secret phrase" Authorization: Bearer PRIVATE`)
    assert.ok(existsSync(path.join(directory, 'run.log.2')))
    assert.equal(existsSync(path.join(directory, 'run.log.3')), false)
    for (const name of ['run.log', 'run.log.1', 'run.log.2']) {
      const text = readFileSync(path.join(directory, name), 'utf8')
      assert.ok(!text.includes('secret phrase') && !text.includes('PRIVATE'))
      for (const line of text.trim().split('\n')) assert.equal(JSON.parse(line).source, 'renderer')
    }
  } finally { rmSync(directory, { recursive: true, force: true }) }
})

test('backend chunks are joined before redaction and capped when no newline arrives', () => {
  const directory = mkdtempSync(path.join(os.tmpdir(), 'stella-log-'))
  try {
    const log = new RuntimeLog(directory)
    log.write('INFO token=SEC'); log.write('RET\n')
    log.write('x'.repeat(100000)); log.write('\n')
    assert.ok(!readFileSync(path.join(directory, 'run.log'), 'utf8').includes('SECRET'))
    assert.ok(readFileSync(path.join(directory, 'run.log'), 'utf8').length < 20000)
  } finally { rmSync(directory, { recursive: true, force: true }) }
})
