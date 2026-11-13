import test from 'node:test'
import assert from 'node:assert/strict'
import { build } from 'esbuild'
import { execFileSync } from 'node:child_process'
import { python } from './platform-python.mjs'

test('every current plugin Host entry bundles without the retired application runtime', async () => {
  const plugins = ['agent-hub', 'apple-sync', 'elearning', 'mail', 'organizer', 'plugin-info', 'secretary']
  const result = await build({
    entryPoints: [...plugins.map(name => `packages/alcor-${name}/src/index.ts`), 'packages/alcor-privacy/src/index.js'],
    bundle: true, packages: 'external', platform: 'node', format: 'esm',
    outdir: 'out', write: false, metafile: true,
  })
  for (const name of Object.keys(result.metafile.inputs)) {
    assert(!/^(?:desktop|agent|application|bridge|channel|core|models)\//.test(name), name)
  }
})

test('shipped plugin Python sources never import the retired Core or Agent', () => {
  execFileSync(python, ['-B', '-c', `
import ast
from pathlib import Path
retired = {'agent', 'application', 'bridge', 'channel', 'core', 'models'}
for package in ('alcor-secretary', 'alcor-apple-sync', 'alcor-agent-hub'):
    for path in (Path('packages') / package).rglob('*.py'):
        tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        for node in ast.walk(tree):
            roots = ([name.name.split('.')[0] for name in node.names] if isinstance(node, ast.Import) else
                     [node.module.split('.')[0]] if isinstance(node, ast.ImportFrom) and node.module and not node.level else [])
            assert not retired.intersection(roots), (str(path), node.lineno, roots)
`], { stdio: 'pipe' })
})
