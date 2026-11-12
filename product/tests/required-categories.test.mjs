import { python as testPython } from './platform-python.mjs'
import test from 'node:test'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import { fileURLToPath } from 'node:url'

test('categories are mandatory across legacy data, imports and cascading deletion', async () => {
  await promisify(execFile)(testPython, [fileURLToPath(new URL('./required-categories.py', import.meta.url))])
})
