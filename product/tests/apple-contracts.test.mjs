import test from 'node:test'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import { fileURLToPath } from 'node:url'
const run = promisify(execFile)
test('Apple compatibility preserves data across permissions, accounts, conflicts, recurrence and incremental ranges', async () => {
  await run(process.env.STELLA_BUSINESS_PYTHON || 'python3',[fileURLToPath(new URL('./apple-contracts.py',import.meta.url))])
})
test('native Apple fields and durable receipts retain public data without requesting authorization',{skip:process.platform!=='darwin'},async () => {
  await run(process.env.STELLA_BUSINESS_PYTHON || 'python3',[fileURLToPath(new URL('./apple-native-fields.py',import.meta.url))],{timeout:60000})
})
