import test from 'node:test'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import { fileURLToPath } from 'node:url'

test('briefing prompt deduplication preserves full data, ordering and checkpoint batches', async () => {
  await promisify(execFile)(process.env.STELLA_BUSINESS_PYTHON || 'python3', ['-B', fileURLToPath(new URL('./briefing-prompts.py', import.meta.url))])
})
