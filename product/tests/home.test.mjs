import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, writeFile, mkdir, symlink, rm, stat } from 'node:fs/promises'
import { tmpdir, homedir } from 'node:os'
import { join } from 'node:path'
import { prepareProductHome } from '../../scripts/product/home.mjs'
import { withFileLock } from '@deepseek-ai/dsh-atomic-write'

test('preview root rejects personal/project directories, foreign contents and symlink roots', async () => {
  const temporary = await mkdtemp(join(tmpdir(), 'stella-home-check-'))
  try {
    const repo = join(temporary, 'repo')
    await mkdir(repo)
    for (const target of [homedir(), join(homedir(), '.dsh/profiles'), repo, join(repo, 'packages')]) {
      await assert.rejects(prepareProductHome(target, repo, 'web'), /dedicated/)
    }
    const foreign = join(temporary, 'foreign')
    await mkdir(foreign); await writeFile(join(foreign, 'existing-data'), 'preserve')
    await assert.rejects(prepareProductHome(foreign, repo, 'web'), /not owned/)
    const home = await prepareProductHome(join(repo, '.local/web'), repo, 'web')
    assert.equal((await stat(home)).mode & 0o777, 0o700)
    assert.equal(await prepareProductHome(home, repo, 'web'), home)
    await assert.rejects(prepareProductHome(home, repo, 'desktop'), /different runtime/)
    const alias = join(temporary, 'alias')
    await symlink(home, alias)
    await assert.rejects(prepareProductHome(alias, repo, 'web'), /symbolic link/)
    await withFileLock(join(home, 'product-instance'), async () => {
      await assert.rejects(withFileLock(join(home, 'product-instance'), async () => assert.fail('second writer'), { waitMs: 0 }), /timed out/)
    })
  } finally { await rm(temporary, { recursive: true, force: true }) }
})
