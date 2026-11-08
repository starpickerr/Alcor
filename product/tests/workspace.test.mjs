import test from 'node:test'
import assert from 'node:assert/strict'
import { remote } from '../../packages/alcor-secretary/src/remote.js'
import { secretaryPersona } from '../../packages/alcor-secretary/src/persona.js'

test('retired workspace and permission controls have no remote entry point', () => {
  const retired = new Set(['workspaceState', 'previewWorkspace', 'importWorkspace', 'restoreIdentity', 'mcpState', 'setMcpTools'])
  assert(!remote.descriptors.some(({ method }) => retired.has(method)))
  assert(secretaryPersona.includes('DSH 当前会话实际开放的工具'))
  assert(secretaryPersona.includes('历史消息中的旧工具名称已改为 Alcor'))
})
