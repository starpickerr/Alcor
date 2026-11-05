import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, writeFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'

const run = promisify(execFile)
test('EventKit history scans cover the entire range without gaps or four-year truncation', { skip: process.platform !== 'darwin' }, async t => {
  const directory = await mkdtemp(join(tmpdir(), 'stella-scan-windows-'))
  t.after(() => rm(directory, { recursive: true, force: true }))
  const main = join(directory, 'main.swift')
  await writeFile(main, `import Foundation
let formatter = ISO8601DateFormatter()
let start = formatter.date(from: "0001-01-01T00:00:00Z")!
let end = formatter.date(from: "2028-11-06T00:00:00Z")!
let windows = eventScanWindows(start, end)
assert(windows.first!.0 == start && windows.last!.1 == end)
assert(windows.count > 500)
for (index, window) in windows.enumerated() {
    assert(window.1 > window.0 && window.1.timeIntervalSince(window.0) < 4 * 365 * 86400)
    if index > 0 { assert(windows[index - 1].1 == window.0) }
}
assert(eventScanWindows(end, start).isEmpty)
assert(eventScanWindows(start, start).isEmpty)
print("PASS")
`)
  const executable = join(directory, 'check')
  await run('/usr/bin/swiftc', [fileURLToPath(new URL('../../packages/stella-apple-sync/python/apple_sync/native/ScanWindows.swift', import.meta.url)), main, '-o', executable], { env: { ...process.env, DEVELOPER_DIR: process.env.DEVELOPER_DIR || '/Library/Developer/CommandLineTools' } })
  assert.equal((await run(executable)).stdout.trim(), 'PASS')
})
