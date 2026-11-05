import { execFileSync } from 'node:child_process'
import { copyFile, mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

if (process.platform === 'darwin') {
  const directory = fileURLToPath(new URL('../../packages/stella-apple-sync/python/apple_sync/', import.meta.url))
  const source = join(directory, 'native')
  const temporary = await mkdtemp(join(tmpdir(), 'stella-eventkit-build-'))
  try {
    await copyFile(join(source, 'CalendarBridge.swift'), join(temporary, 'main.swift'))
    execFileSync('/usr/bin/swiftc', ['-O', '-framework', 'EventKit',
      '-Xlinker', '-sectcreate', '-Xlinker', '__TEXT', '-Xlinker', '__info_plist', '-Xlinker', join(source, 'Info.plist'),
      join(temporary, 'main.swift'), ...['OccurrenceIdentity.swift', 'ScanWindows.swift', 'ReminderBridge.swift', 'ReminderFields.swift'].map(name => join(source, name)),
      '-o', join(directory, 'StellaCalendarBridge')], { stdio: 'inherit', env: { ...process.env, DEVELOPER_DIR: process.env.DEVELOPER_DIR || '/Library/Developer/CommandLineTools' } })
    execFileSync('/usr/bin/codesign', ['--force', '--sign', '-', join(directory, 'StellaCalendarBridge')], { stdio: 'inherit' })
  } finally { await rm(temporary, { recursive: true, force: true }) }
}
