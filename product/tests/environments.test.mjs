import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, mkdir, readFile, writeFile, symlink, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import {spawn} from 'node:child_process'
import {once} from 'node:events'
import { prepareEnvironment, environmentFor, installPlugin, launchEnvironment, desktopIsRunning, desktopApplication, officialDesktop } from '../../scripts/product/environments.mjs'
import { applicationEnvironment, selectedScope, publishApplication } from '../../packages/alcor-plugin-info/src/assignments.js'
import { guardSource } from '../../packages/alcor-plugin-info/src/host-policy.js'

async function fixture(t) {
  const parent = await mkdtemp(join(tmpdir(), 'alcor-environments-'))
  t.after(() => rm(parent, { recursive: true, force: true }))
  return { parent, root: join(parent, 'owned') }
}
const desktop = { cli: '/verified-desktop/dsh', binary: '/verified-desktop/app' }

test('the optional isolated Desktop launcher refuses unverified platforms', { skip: process.platform === 'darwin' }, async () => {
  await assert.rejects(officialDesktop('/unverified-desktop'), /仅验证了 macOS/)
})

test('a live Desktop outliving its launcher is recognized, focused and protected from CLI installation',{
  skip: process.platform === 'win32' && 'The optional isolated launcher requires macOS; its process identity probe uses POSIX ps',
},async t=>{
  const {root}=await fixture(t)
  const paths=await prepareEnvironment(root,'dsh'), app={...desktop,binary:process.execPath}
  const child=spawn(process.execPath,['-e','setInterval(()=>{},1000)','--','--user-data-dir='+paths.userData],{stdio:'ignore'})
  await once(child,'spawn')
  try{
    await writeFile(join(paths.base,'.desktop-process.json'),JSON.stringify({pid:child.pid,binary:app.binary,userData:paths.userData}))
    assert(await desktopIsRunning(paths,app))
    await assert.rejects(installPlugin(paths,app,'third-party',async()=>assert.fail()),/退出/)
    const calls=[]
    await launchEnvironment(paths,app,{runner:async(...args)=>calls.push(args)})
    assert.equal(calls.length,1);assert.equal(calls[0][0],process.execPath)
    await writeFile(join(paths.base,'.desktop-process.json'),JSON.stringify({pid:process.pid,binary:app.binary,userData:paths.userData}))
    assert.equal(await desktopIsRunning(paths,app),false)
  }finally{child.kill();await once(child,'exit')}
  assert.equal(desktopApplication('/Applications/DeepSeek Harness.app/Contents/Resources/runtime/bin/node'),'/Applications/DeepSeek Harness.app')
})

test('a process record must belong to this environment even after its process exits', async t => {
  const { root } = await fixture(t)
  const paths = await prepareEnvironment(root, 'dsh')
  const child = spawn(process.execPath, ['-e', ''], { stdio: 'ignore' })
  await once(child, 'exit')
  const record = { pid: child.pid, binary: process.execPath, userData: paths.userData }
  const file = join(paths.base, '.desktop-process.json')
  await writeFile(file, JSON.stringify({ ...record, userData: paths.home }))
  await assert.rejects(desktopIsRunning(paths, desktop), /桌面进程记录无效/)
  await writeFile(file, JSON.stringify(record))
  assert.equal(await desktopIsRunning(paths, desktop), false, 'an exited app may have moved since its previous launch')
})

test('applications have independent standard profiles, homes, plugin state and user data', async t => {
  const { root } = await fixture(t)
  const alcor = await prepareEnvironment(root, 'alcor'), dsh = await prepareEnvironment(root, 'dsh')
  for (const key of ['base', 'home', 'dsh', 'userData', 'workspace', 'profile', 'packages']) assert.notEqual(alcor[key], dsh[key])
  const original = await readFile(join(dsh.profile, 'package.json'), 'utf8')
  assert.deepEqual(JSON.parse(original).dsh.profile.bundles, ['@deepseek-ai/dsh-base', '@deepseek-ai/dsh-web-app'])
  await writeFile(join(alcor.profile, 'package.json'), JSON.stringify({ private: true, dependencies: { 'unchanged-third-party': '1.0.0' } }))
  await prepareEnvironment(root, 'alcor')
  assert.equal(await readFile(join(dsh.profile, 'package.json'), 'utf8'), original)
  assert.equal(JSON.parse(await readFile(join(alcor.profile, 'package.json'))).dependencies['unchanged-third-party'], '1.0.0')
})

test('existing unowned data, incorrect identities, symbolic roots and escaping application names are refused', async t => {
  const { parent, root } = await fixture(t)
  await mkdir(root)
  await writeFile(join(root, 'personal-data'), 'keep')
  await assert.rejects(prepareEnvironment(root, 'alcor'), /not owned/)
  assert.equal(await readFile(join(root, 'personal-data'), 'utf8'), 'keep')
  const valid = join(parent, 'valid')
  const alcor = await prepareEnvironment(valid, 'alcor')
  await writeFile(join(alcor.base, '.alcor-environment.json'), JSON.stringify({ version: 1, application: 'dsh' }))
  await assert.rejects(prepareEnvironment(valid, 'alcor'), /身份/)
  await assert.rejects(prepareEnvironment(valid, '../dsh'), /请选择/)
  await symlink(valid, join(parent, 'alias'), process.platform === 'win32' ? 'junction' : 'dir')
  await assert.rejects(prepareEnvironment(join(parent, 'alias'), 'dsh'), /symbolic link/)
})

test('nested directory and manifest symlinks cannot alias the other application', async t => {
  const { root } = await fixture(t)
  const alcor = await prepareEnvironment(root, 'alcor'), dsh = await prepareEnvironment(root, 'dsh')
  await rm(alcor.dsh, { recursive: true })
  await symlink(dsh.dsh, alcor.dsh, process.platform === 'win32' ? 'junction' : 'dir')
  await assert.rejects(prepareEnvironment(root, 'alcor'), /软链接/)
  await rm(alcor.dsh); await mkdir(alcor.dsh)
  await prepareEnvironment(root, 'alcor')
  const manifest = join(alcor.profile, 'package.json')
  await rm(manifest)
  // Directory aliases can also occupy the manifest path on unprivileged Windows.
  await symlink(dsh.profile, manifest, process.platform === 'win32' ? 'junction' : 'dir')
  await assert.rejects(prepareEnvironment(root, 'alcor'), /普通文件/)
  await rm(manifest)
  await t.test('manifest symlinks cannot alias the other application', async t => {
    try { await symlink(join(dsh.profile, 'package.json'), manifest, 'file') }
    catch (error) {
      if (process.platform !== 'win32' || error.code !== 'EPERM') throw error
      t.skip('Windows file symlink creation requires a privilege unavailable to this test account')
      return
    }
    await assert.rejects(prepareEnvironment(root, 'alcor'), /普通文件/)
  })
})

test('runtime environment retains proxy settings but does not inherit credentials or parent data roots', async t => {
  const { root } = await fixture(t)
  const alcor = await prepareEnvironment(root, 'alcor')
  const env = environmentFor(alcor, { PATH: '/bin', HOME: '/personal', DSH_HOME: '/personal/.dsh', DEEPSEEK_API_KEY: 'synthetic-secret',
    OPENAI_API_KEY: 'synthetic-secret', AURABOT_DATA_DIR: '/personal/qq', NODE_OPTIONS: '--require=untrusted', HTTPS_PROXY: 'http://proxy.invalid' })
  assert.equal(env.HOME, alcor.home); assert.equal(env.DSH_HOME, alcor.dsh)
  assert.equal(env.USERPROFILE, alcor.home); assert.equal(env.ALCOR_APPLICATION, 'alcor')
  assert.equal(env.HTTPS_PROXY, 'http://proxy.invalid')
  for (const key of ['DEEPSEEK_API_KEY', 'OPENAI_API_KEY', 'AURABOT_DATA_DIR', 'NODE_OPTIONS']) assert.equal(env[key], undefined)
})

test('third-party installation uses native DSH without rewriting plugin source or requiring custom scope metadata', async t => {
  const { root } = await fixture(t)
  const paths = await prepareEnvironment(root, 'alcor')
  const calls = []
  await installPlugin(paths, desktop, 'github:example/unmodified-plugin', async (...args) => calls.push(args))
  assert.deepEqual(calls[0].slice(0, 2), [desktop.cli, ['plugin', '--profile', 'desktop', 'add', 'github:example/unmodified-plugin', '--ignore-scripts']])
  assert.equal(calls[0][2].env.DSH_HOME, paths.dsh)
  await assert.rejects(installPlugin(paths, desktop, '--unsafe', async () => assert.fail()), /安装地址/)
})

test('workbench launches without Alcor installation and desktop data never reuse the parent', async t => {
  const { root } = await fixture(t)
  const paths = await prepareEnvironment(root, 'dsh')
  const calls = []
  await launchEnvironment(paths, desktop, { runner: async (...args) => calls.push(args) })
  assert.equal(calls.length, 1)
  assert.deepEqual(calls[0].slice(0, 2), [desktop.binary, ['--user-data-dir=' + paths.userData]])
  assert.equal(calls[0][2].env.ALCOR_APPLICATION, 'dsh')
  assert.equal(calls[0][2].cwd, paths.workspace)
})

test('first Alcor launch retains its install archive, and failed initialization is retryable without launching', async t => {
  const { root, parent } = await fixture(t)
  const paths = await prepareEnvironment(root, 'alcor')
  const archive = join(parent, 'secretary.tgz')
  await writeFile(archive, 'synthetic immutable archive')
  const calls = []
  await assert.rejects(launchEnvironment(paths, desktop, { archive, runner: async (...args) => { calls.push(args); throw Error('install failed') } }), /install failed/)
  assert.equal(calls.length, 1)
  const saved = calls[0][1][4]
  assert.equal(dirname(saved), paths.packages); assert.equal(await readFile(saved, 'utf8'), 'synthetic immutable archive')
  const retried = []
  await launchEnvironment(paths, desktop, { archive, runner: async (...args) => retried.push(args) })
  assert.equal(retried.length, 2); assert.equal(retried[0][1][4], saved)
})

test('installation cannot race a running environment, while the other environment can run', async t => {
  const { root } = await fixture(t)
  const paths = await prepareEnvironment(root, 'dsh'), other = await prepareEnvironment(root, 'alcor')
  let started, release
  const began = new Promise(done => { started = done }), wait = new Promise(done => { release = done })
  const launched = launchEnvironment(paths, desktop, { runner: async () => { started(); await wait } })
  await began
  try {
    await assert.rejects(installPlugin(paths, desktop, 'example-plugin', async () => assert.fail()), /lock/i)
    let installed = false
    await installPlugin(other, desktop, 'example-plugin', async () => { installed = true })
    assert(installed)
  } finally { release(); await launched }
  await installPlugin(paths, desktop, 'example-plugin', async () => {})
})

test('own legacy assignment fields do not constrain a plugin installed in an independent environment', () => {
  const old = process.env.ALCOR_APPLICATION
  try {
    process.env.ALCOR_APPLICATION = 'alcor'
    assert.equal(applicationEnvironment(), 'alcor')
    assert.equal(selectedScope({}, '@alcor/agent-hub'), 'both')
    const callbacks=[]
    const publisher={on:(name,callback)=>{assert.equal(name,'webserver/index-inject');callbacks.push(callback)}}
    publishApplication(publisher);publishApplication(publisher)
    const rows=[]
    callbacks.forEach(callback=>callback(rows))
    assert.deepEqual(rows,[{kind:'global',name:'__ALCOR_APPLICATION__',value:'alcor'}])
    assert.throws(()=>callbacks[0]([{kind:'global',name:'__ALCOR_APPLICATION__',value:'dsh'}]),/身份/)
    let guard
    guardSource({ tools: { guard: fn => { guard = fn } }, on() {}, effect() {} }, { productScope: { get: () => 'dsh' } }, 'example_')
    assert.equal(guard({ name: 'example_call', agent: { session: { header: { agentPreset: 'alcor' } } } }), undefined)
  } finally { if (old === undefined) delete process.env.ALCOR_APPLICATION; else process.env.ALCOR_APPLICATION = old }
})
