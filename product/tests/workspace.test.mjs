import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, mkdir, writeFile, symlink, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { previewLegacyWorkspace, workspacePersona, secretaryPersona } from '../../packages/stella-secretary/src/workspace-import.js'
import { initialWorkspaceState } from '../../packages/stella-secretary/src/workspace-state.js'

test('legacy preview reads only identity files, fingerprints edits and refuses links, large files and invalid roots', async () => {
  const root = await mkdtemp(join(tmpdir(), 'stella-workspace-'))
  try {
    const canonical = await import('node:fs/promises').then(fs => fs.realpath(root))
    const source = Object.fromEntries(['application', 'agent', 'user', 'shared'].map(role => [role, canonical]))
    await writeFile(join(root, 'AGENT.md'), 'Persona sentinel {{literal}}')
    await writeFile(join(root, 'USER.md'), 'User sentinel')
    await writeFile(join(root, '.env'), 'DO_NOT_IMPORT')
    await mkdir(join(root, 'skills'))
    const preview = await previewLegacyWorkspace(source)
    assert.deepEqual(preview.files.map(file => file.name), ['AGENT.md', 'USER.md'])
    assert(!JSON.stringify(preview).includes('DO_NOT_IMPORT'))
    assert(preview.inventory.some(item => item.name === 'skills' && item.present))
    assert.equal((await previewLegacyWorkspace(source)).fingerprint, preview.fingerprint)
    await writeFile(join(root, 'USER.md'), 'Changed user')
    assert.notEqual((await previewLegacyWorkspace(source)).fingerprint, preview.fingerprint)
    await symlink(join(root, '.env'), join(root, 'RULE.md'))
    await assert.rejects(previewLegacyWorkspace(source), /链接/)
    await rm(join(root, 'RULE.md'))
    await writeFile(join(root, 'RULE.md'), 'x'.repeat(65537))
    await assert.rejects(previewLegacyWorkspace(source), /64 KB/)
    await assert.rejects(previewLegacyWorkspace({ ...source, agent: '/' }), /明确/)
    await assert.rejects(previewLegacyWorkspace({ ...source, user: 'relative' }), /明确/)
  } finally { await rm(root, { recursive: true, force: true }) }
})

test('imported private identity is excluded from delegated or unknown scopes', () => {
  const state = { ...initialWorkspaceState, identity: { files: [{ name: 'USER.md', content: 'PRIVATE_USER_SENTINEL' }] } }
  assert(workspacePersona(state, { cwd: '/project' }, true).includes('PRIVATE_USER_SENTINEL'))
  for (const header of [undefined, { origin: 'subagent' }, { parentSession: 'parent' }]) assert.equal(workspacePersona(state, header, true), secretaryPersona)
  assert.equal(workspacePersona(state, { cwd: '/project' }, false), secretaryPersona)
  assert.equal(workspacePersona(initialWorkspaceState, {}), secretaryPersona)
})
