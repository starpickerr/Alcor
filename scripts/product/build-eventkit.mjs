import { execFileSync } from 'node:child_process'
import { copyFile, mkdir, mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

if (process.platform === 'darwin') {
  const directory = fileURLToPath(new URL('../../packages/alcor-apple-sync/python/apple_sync/', import.meta.url))
  const source = join(directory, 'native')
  const app = join(directory, 'Alcor Apple Sync.app')
  const executable = join(app, 'Contents/MacOS/AlcorCalendarBridge')
  await mkdir(join(app, 'Contents/MacOS'), { recursive: true })
  await copyFile(join(source, 'Info.plist'), join(app, 'Contents/Info.plist'))
  const temporary = await mkdtemp(join(tmpdir(), 'alcor-eventkit-build-'))
  try {
    await copyFile(join(source, 'CalendarBridge.swift'), join(temporary, 'main.swift'))
    execFileSync('/usr/bin/swiftc', ['-O', '-framework', 'EventKit',
      '-Xlinker', '-sectcreate', '-Xlinker', '__TEXT', '-Xlinker', '__info_plist', '-Xlinker', join(source, 'Info.plist'),
      join(temporary, 'main.swift'), ...['OccurrenceIdentity.swift', 'ScanWindows.swift', 'ReminderBridge.swift', 'ReminderFields.swift'].map(name => join(source, name)),
      '-o', executable], { stdio: 'inherit', env: { ...process.env, DEVELOPER_DIR: process.env.DEVELOPER_DIR || '/Library/Developer/CommandLineTools' } })
    // Finder metadata on the generated app is rejected by codesign.
    execFileSync('/usr/bin/xattr', ['-cr', app], { stdio: 'inherit' })
    execFileSync('/usr/bin/codesign', ['--force', '--sign', '-', app], { stdio: 'inherit' })
  } finally { await rm(temporary, { recursive: true, force: true }) }
}
