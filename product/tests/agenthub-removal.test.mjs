import test from 'node:test'
import assert from 'node:assert/strict'
import { access, readFile } from 'node:fs/promises'

const root = new URL('../../', import.meta.url)
test('AgentHub is absent from the product and build graph', async () => {
  await assert.rejects(access(new URL('packages/alcor-agent-hub', root)), { code: 'ENOENT' })
  for (const file of ['package.json', 'package-lock.json', '.github/workflows/native-product.yml', 'packages/alcor-secretary/src/desk.jsx', 'packages/alcor-secretary/src/client.jsx']) {
    assert.doesNotMatch(await readFile(new URL(file, root), 'utf8'), /@alcor\/agent-hub|build-agent-hub|hubForm|label:'AgentHub'/, file)
  }
})

test('retired AgentHub tools and page routes cannot be loaded by the legacy host', async () => {
  for (const path of ['agent/tools/agent_hub', 'core/services/agent_hub_collaboration.py', 'core/services/agent_hub_events.py', 'core/services/agent_hub_execution.py', 'core/services/agent_hub_gateway.py']) {
    await assert.rejects(access(new URL(path, root)), { code: 'ENOENT' })
  }
  for (const file of ['agent/tools/__init__.py', 'agent/tools/catalog.py', 'resources/default-plugins.json', 'core/plugins/bootstrap.py', 'core/plugins/lifecycle.py', 'core/plugins/page_host.py', 'desktop/src/renderer/src/layout/navigation.ts', 'desktop/src/renderer/src/App.tsx']) {
    assert.doesNotMatch(await readFile(new URL(file, root), 'utf8'), /AgentHubTool|stella\.agent-hub|_entry\("agent_hub"|_invoke_agent_hub|\/agent-hub/, file)
  }
})
