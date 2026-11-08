import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, mkdir, writeFile, rm, access } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'
import { BusinessWorker, resolveBusinessPython } from '../../packages/alcor-organizer/src/business.js'

test('business imports never create bytecode in the interpreter or plugin payload', async context => {
  const directory = await mkdtemp(join(tmpdir(), 'stella-python-readonly-'))
  context.after(() => rm(directory, { recursive: true, force: true }))
  await writeFile(join(directory, 'signed_library.py'), 'value = 42\n')
  const workerFile = join(directory, 'worker.py')
  await writeFile(workerFile, 'import sys, json, signed_library\nfor line in sys.stdin:\n request = json.loads(line)\n print(json.dumps({"id": request["id"], "ok": True, "value": [signed_library.value, sys.dont_write_bytecode]}), flush=True)\n')
  const worker = new BusinessWorker(directory, { worker: pathToFileURL(workerFile) })
  try { assert.deepEqual(await worker.call('probe'), [42, true]) }
  finally { await worker.close() }
  await assert.rejects(access(join(directory, '__pycache__')), { code: 'ENOENT' })
})

test('official Desktop uses its validated payload without requiring Python on PATH', async context => {
  const source = await mkdtemp(join(tmpdir(), 'stella-desktop-python-'))
  context.after(() => rm(source, { recursive: true, force: true }))
  const executable = process.platform === 'win32' ? join(source, 'dependencies/python/python.exe') : join(source, 'dependencies/python/bin/python3')
  await mkdir(process.platform === 'win32' ? join(source, 'dependencies/python/Lib/site-packages') : join(source, 'dependencies/python/lib/python3.12/site-packages'), { recursive: true })
  await mkdir(join(source, 'dependencies/python/bin'), { recursive: true })
  await writeFile(executable, 'synthetic bundled interpreter')
  const manifest = { desktopVersion: '0.2.2-alpha.3', platform: process.platform, arch: process.arch, python: '3.12.14', pythonPackages: {} }
  await writeFile(join(source, 'runtime.json'), JSON.stringify(manifest))
  const argv = ['node', '/official/@deepseek-ai/dsh-desktop-host/lib/index.js', 'runtime', 'project', source]
  const environment = { PATH: '/no-system-python' }
  assert.equal(await resolveBusinessPython(argv, environment), executable)
  assert.equal(environment.PATH, '/no-system-python')
  await writeFile(join(source, 'runtime.json'), JSON.stringify({ ...manifest, arch: process.arch === 'arm64' ? 'x64' : 'arm64' }))
  await assert.rejects(resolveBusinessPython(argv, environment), /incompatible/)
  await assert.rejects(resolveBusinessPython(argv.slice(0, 4), environment), /路径无效/)
})

test('explicit development override and non-Desktop behavior remain compatible', async () => {
  const argv = ['node', '/official/@deepseek-ai/dsh-desktop-host/lib/index.js']
  assert.equal(await resolveBusinessPython(argv, { ALCOR_BUSINESS_PYTHON: '/explicit/python' }), '/explicit/python')
  assert.equal(await resolveBusinessPython(argv, { STELLA_BUSINESS_PYTHON: '/legacy/python' }), '/legacy/python')
  assert.equal(await resolveBusinessPython(argv, { ALCOR_BUSINESS_PYTHON: '/same', STELLA_BUSINESS_PYTHON: '/same' }), '/same')
  await assert.rejects(resolveBusinessPython(argv, { ALCOR_BUSINESS_PYTHON: '/new', STELLA_BUSINESS_PYTHON: '/old' }), /配置冲突/)
  assert.equal(await resolveBusinessPython(['node', '/cli/dsh.js'], {}), 'python3')
})
