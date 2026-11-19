import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { existsSync, mkdirSync, rmSync, statSync, writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { resolve } from 'node:path'

assert.equal(process.platform, 'darwin', 'Run native confirmation acceptance on macOS')
const root = fileURLToPath(new URL('../../', import.meta.url))
const native = resolve(root, 'packages/alcor-secretary/native')
const helper = resolve(native, 'AlcorConfirm')
const legacy = resolve(native, 'Alcor Confirm.app')

// Exercise cleanup of an old generated bundle and two complete successive builds.
mkdirSync(legacy, { recursive: true })
for (let run = 1; run <= 2; run++) {
  execFileSync(process.execPath, ['scripts/product/build-client.mjs'], { cwd: root, stdio: 'inherit', timeout: 180000 })
  assert(!existsSync(legacy), 'Build left a searchable confirmation app bundle')
  assert(statSync(helper).isFile() && (statSync(helper).mode & 0o111), 'Confirmation executable missing')
  execFileSync('/usr/bin/codesign', ['--verify', '--strict', helper])
  assert.deepEqual(execFileSync('/usr/bin/lipo', ['-archs', helper], { encoding: 'utf8' }).trim().split(/\s+/).sort(), ['arm64', 'x86_64'])
  assert.deepEqual(JSON.parse(execFileSync(helper, { input: '{}', encoding: 'utf8', timeout: 5000 })), { version: 1, operation_id: '', outcome: 'unavailable' })
  console.log(`PASS: build ${run}, signed universal executable, no app bundle, invalid request rejected`)
}
mkdirSync(legacy)
writeFileSync(resolve(legacy, 'legacy.txt'), 'stale generated helper fixture')
try {
  const [pack] = JSON.parse(execFileSync('npm', ['pack', '--workspace', '@alcor/secretary', '--dry-run', '--ignore-scripts', '--json'], { cwd: root, encoding: 'utf8', timeout: 180000 }))
  assert(pack.files.some(file => file.path === 'native/AlcorConfirm' && (file.mode & 0o111)), 'Package omits the executable')
  assert(!pack.files.some(file => file.path.includes('Alcor Confirm.app')), 'Package includes the legacy app bundle')
} finally { rmSync(legacy, { recursive: true, force: true }) }
console.log('PASS: package includes the confirmation executable and excludes the legacy bundle')
