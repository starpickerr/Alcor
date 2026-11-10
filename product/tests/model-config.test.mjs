import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { parse } from 'yaml'

test('product overlay preserves native account, API key and third-party model adapters', async () => {
  const base = parse(await readFile(new URL('../../node_modules/@deepseek-ai/dsh-base/cordis.patch.yml', import.meta.url), 'utf8'), { customTags: [{ tag: 'tag:yaml.org,2002:js', resolve: value => value }] })
  const overlay = parse(await readFile(new URL('../cordis.patch.yml', import.meta.url), 'utf8'))
  const plugins = base.flatMap(entry => entry.insert ?? [])
  for (const id of ['llm-deepseek', 'llm-deepseek-account', 'llm-pi-ai', 'credentials']) {
    assert(plugins.some(entry => entry.id === id && !entry.disabled), `Native ${id} must be installed`)
    assert(!overlay.some(entry => entry.id === id && entry.disabled), `Product must preserve ${id}`)
  }
})
