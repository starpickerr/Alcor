import { python as testPython } from './platform-python.mjs'
import test from 'node:test'
import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import assert from 'node:assert/strict'

test('permanent deletion owner boundaries and reversible positive controls', () => {
  execFileSync(testPython, ['-B', 'product/tests/permanent-delete-failclosed.py'], { stdio: 'pipe' })
})

test('native top-level dispatch rejects unconditioned delete opcodes before any EventKit store construction', () => {
  for (const [file, entry, ops] of [['CalendarBridge.swift', 'run', ['delete', 'delete_calendar', 'reminder_delete', 'reminder_list_delete']], ['ReminderBridge.swift', 'runReminder', ['reminder_delete', 'reminder_list_delete']]]) {
    const text = readFileSync(new URL('../../packages/alcor-apple-sync/python/apple_sync/native/' + file, import.meta.url), 'utf8')
    const start = text.indexOf(`func ${entry}(_ request: [String: Any])`); assert(start >= 0)
    const beforeStore = text.slice(start, text.indexOf('let store = EKEventStore()', start))
    assert.match(beforeStore, /contains\(op\) && !validDeleteRequest\(request\).*throw BridgeFailure/)
    for (const op of ops) assert(beforeStore.includes('"' + op + '"'), op)
  }
})
