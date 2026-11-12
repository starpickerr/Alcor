import { assertPrivatePermissions } from './private-permissions.mjs'
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, writeFile, mkdir, symlink, rm, readFile } from 'node:fs/promises'
import { tmpdir, homedir } from 'node:os'
import { join } from 'node:path'
import { prepareProductHome, selectPreviewProfile, initializeFreshPreview } from '../../scripts/product/home.mjs'
import { isMigrationMarker } from '../../packages/alcor-organizer/src/migration-contract.js'
import { withFileLock } from '@deepseek-ai/dsh-atomic-write'

test('preview root rejects personal/project directories, foreign contents and symlink roots', async () => {
  const temporary = await mkdtemp(join(tmpdir(), 'stella-home-check-'))
  try {
    const repo = join(temporary, 'repo')
    await mkdir(repo)
    for (const target of [homedir(), join(homedir(), '.dsh/profiles'), repo, join(repo, 'packages')]) {
      await assert.rejects(prepareProductHome(target, repo, 'web'), /dedicated Alcor product data directory/)
    }
    const foreign = join(temporary, 'foreign')
    await mkdir(foreign); await writeFile(join(foreign, 'existing-data'), 'preserve')
    await assert.rejects(prepareProductHome(foreign, repo, 'web'), /not owned by this Alcor preview/)
    const home = await prepareProductHome(join(repo, '.local/web'), repo, 'web')
    assert.equal(JSON.parse(await readFile(join(home, '.alcor-product.json'), 'utf8')).kind, 'web')
    assert.equal(await selectPreviewProfile(home), 'alcor')
    await assertPrivatePermissions(home, 0o700)
    assert.equal(await prepareProductHome(home, repo, 'web'), home)
    await assert.rejects(prepareProductHome(home, repo, 'desktop'), /different runtime/)
    const alias = join(temporary, 'alias')
    await symlink(home, alias, process.platform === 'win32' ? 'junction' : 'dir')
    await assert.rejects(prepareProductHome(alias, repo, 'web'), /symbolic link/)
    await withFileLock(join(home, 'product-instance'), async () => {
      await assert.rejects(withFileLock(join(home, 'product-instance'), async () => assert.fail('second writer'), { waitMs: 0 }), /timed out/)
    })
  } finally { await rm(temporary, { recursive: true, force: true }) }
})

test('only an empty locked new preview gets a fresh migration marker', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'alcor-fresh-preview-'))
  try {
    const repo = join(dir, 'repo'); await mkdir(repo)
    const home = await prepareProductHome(join(dir, 'fresh'), repo, 'web')
    assert.equal(await initializeFreshPreview(home, 'alcor'), false)
    await withFileLock(join(home, 'product-instance'), async () => {
      assert.equal(await initializeFreshPreview(home, 'alcor'), true)
      const marker = JSON.parse(await readFile(join(home, 'profiles/alcor/alcor/.migration.json'), 'utf8'))
      assert.equal(marker.source, 'fresh'); assert(isMigrationMarker(marker))
      assert.equal(await initializeFreshPreview(home, 'alcor'), false)
    })
    const existing = join(dir, 'existing'); await mkdir(existing)
    await writeFile(join(existing, '.alcor-product.json'), '{"version":1,"kind":"web"}')
    await writeFile(join(existing, 'existing-data'), 'preserve')
    await withFileLock(join(existing, 'product-instance'), async () => assert.equal(await initializeFreshPreview(existing, 'alcor'), false))
    assert.equal(await readFile(join(existing, 'existing-data'), 'utf8'), 'preserve')
  } finally { await rm(dir, { recursive: true, force: true }) }
})

test('existing preview identity is preserved and ambiguous profiles fail closed', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'alcor-preview-'))
  try {
    const repo = join(dir, 'repo'), home = join(dir, 'legacy')
    await mkdir(repo); await mkdir(join(home, 'profiles', 'stella'), { recursive: true })
    const marker = '{"version":1,"kind":"web"}\n'
    await writeFile(join(home, '.stella-product.json'), marker)
    await writeFile(join(home, 'profiles', 'stella', 'synthetic-data'), 'preserve')
    await prepareProductHome(home, repo, 'web')
    assert.equal(await selectPreviewProfile(home), 'stella')
    assert.equal(await readFile(join(home, '.stella-product.json'), 'utf8'), marker)
    assert.equal(await readFile(join(home, 'profiles', 'stella', 'synthetic-data'), 'utf8'), 'preserve')
    await mkdir(join(home, 'profiles', 'alcor'))
    await assert.rejects(selectPreviewProfile(home), /Conflicting/)
    await writeFile(join(home, '.alcor-product.json'), marker)
    await assert.rejects(prepareProductHome(home, repo, 'web'), /Conflicting/)
  } finally { await rm(dir, { recursive: true, force: true }) }
})
