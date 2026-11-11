import { spawn, execFile } from 'node:child_process'
import { createHash } from 'node:crypto'
import { readFile, writeFile, mkdir, readdir, lstat, chmod, realpath, unlink } from 'node:fs/promises'
import { homedir } from 'node:os'
import { join, resolve, dirname, isAbsolute } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { promisify } from 'node:util'
import { parseArgs } from 'node:util'
import { withFileLock, writeFileAtomic } from '@deepseek-ai/dsh-atomic-write'
import { prepareProductHome } from './home.mjs'
import releases from '../../product/desktop-release.lock.json' with { type: 'json' }

const repository = fileURLToPath(new URL('../../', import.meta.url))
const execute = promisify(execFile)
const kinds = new Set(['alcor', 'dsh'])
const bundles = ['@deepseek-ai/dsh-base', '@deepseek-ai/dsh-web-app']

async function directory(path) {
  await mkdir(path, { recursive: true, mode: 0o700 })
  const info = await lstat(path)
  if (!info.isDirectory() || info.isSymbolicLink()) throw new Error('运行环境目录不能是软链接：' + path)
  await chmod(path, 0o700)
}

async function json(path) {
  const info = await lstat(path)
  if (!info.isFile() || info.isSymbolicLink()) throw new Error('运行环境记录必须是普通文件：' + path)
  return JSON.parse(await readFile(path, 'utf8'))
}

/** Create only task-owned roots; never adopt a personal DSH home or copy its secrets. */
export async function prepareEnvironment(root, kind) {
  if (!kinds.has(kind)) throw new Error('请选择 alcor 或 dsh 环境。')
  root = await prepareProductHome(root, repository, 'environments')
  return withFileLock(join(root, 'environments'), async () => {
    const base = join(root, kind)
    await mkdir(base, { recursive: true, mode: 0o700 })
    const info=await lstat(base)
    if(!info.isDirectory() || info.isSymbolicLink())throw new Error('运行环境目录不能是软链接：'+base)
    const marker = join(base, '.alcor-environment.json')
    let ownership
    try { ownership = await json(marker) }
    catch (error) {
      if (error.code !== 'ENOENT') throw error
      if ((await readdir(base)).length) throw new Error('已有目录不属于此运行环境，原数据已保留。')
      ownership = { version: 1, application: kind }
      await writeFile(marker, JSON.stringify(ownership) + '\n', { flag: 'wx', mode: 0o600 })
    }
    if (ownership.version !== 1 || ownership.application !== kind) throw new Error('运行环境身份不匹配。')
    await chmod(base,0o700)
    const paths = { root, kind, base, home: join(base, 'home'), dsh: join(base, 'dsh'),
      userData: join(base, 'electron'), workspace: join(base, 'workspace'), packages: join(base, 'packages') }
    for (const path of [paths.home, paths.dsh, paths.userData, paths.workspace, paths.packages]) await directory(path)
    await directory(join(paths.dsh, 'profiles'))
    paths.profile = join(paths.dsh, 'profiles', 'desktop')
    await directory(paths.profile)
    const manifest = join(paths.profile, 'package.json')
    try { await json(manifest) }
    catch (error) {
      if (error.code !== 'ENOENT') throw error
      await writeFile(manifest, JSON.stringify({ private: true, dsh: { profile: { bundles } } }, null, 2) + '\n', { flag: 'wx', mode: 0o600 })
    }
    return paths
  }, { waitMs: 0 })
}

/** Third-party plugins receive normal DSH paths and APIs in their own process. */
export function environmentFor(paths, inherited = process.env) {
  const env = Object.fromEntries(Object.entries(inherited).filter(([key]) =>
    /^(PATH|USER|LOGNAME|SHELL|TMPDIR|LANG|LC_[A-Z_]+|https?_proxy|all_proxy|no_proxy|HTTPS?_PROXY|ALL_PROXY|NO_PROXY|SSL_CERT_FILE|NODE_EXTRA_CA_CERTS)$/.test(key)))
  return { ...env, HOME: paths.home, USERPROFILE: paths.home, ZDOTDIR: paths.home,
    XDG_CONFIG_HOME: join(paths.home, '.config'), XDG_CACHE_HOME: join(paths.home, '.cache'), XDG_DATA_HOME: join(paths.home, '.local', 'share'),
    DSH_HOME: paths.dsh, DSH_TELEMETRY_DISABLED: '1', ALCOR_APPLICATION: paths.kind, ALCOR_ENVIRONMENTS_ROOT: paths.root }
}

export function desktopApplication(executable=process.execPath) {
  for(let path=executable;dirname(path)!==path;path=dirname(path))if(path.endsWith('.app'))return path
  return join(homedir(),'Applications','DeepSeek Harness.app')
}

/** Use the unchanged, signed Desktop and the CLI it ships, not a second DSH distribution. */
export async function officialDesktop(app = desktopApplication()) {
  if (process.platform !== 'darwin') throw new Error('此入口当前仅验证了 macOS 官方 DSH 桌面版。')
  app = await realpath(app)
  const { stdout } = await execute('/usr/bin/plutil', ['-convert', 'json', '-o', '-', join(app, 'Contents', 'Info.plist')])
  const metadata = JSON.parse(stdout)
  const release = [releases, ...releases.compatibleReleases ?? []].find(item =>
    item.version === metadata.CFBundleShortVersionString && item.bundleId === metadata.CFBundleIdentifier && item.arch === process.arch)
  if (!release) throw new Error('此官方桌面版本尚未通过 Alcor 兼容验证。')
  const archive = join(app, 'Contents', 'Resources', 'app.asar')
  // Electron's fs treats .asar as a directory; hash the physical archive outside that adapter.
  const digest = (await execute('/usr/bin/shasum',['-a','256',archive])).stdout.split(/\s+/)[0]
  if (digest !== release.appAsarSha256) throw new Error('官方桌面归档与已验证版本不一致。')
  await execute('/usr/bin/codesign', ['--verify', '--deep', '--strict', app])
  const signature = await execute('/usr/bin/codesign', ['--display', '--verbose=4', app])
  if (!signature.stderr.includes('TeamIdentifier=' + release.teamId)) throw new Error('官方桌面签名身份不匹配。')
  return { app, version: release.version, binary: join(app, 'Contents', 'MacOS', 'DeepSeek Harness'),
    cli: join(app, 'Contents', 'Resources', 'runtime', 'cli', 'bin', 'dsh') }
}

export async function run(command, args, options) {
  const {onStarted,...spawnOptions}=options
  const child = spawn(command, args, { stdio: 'inherit', ...spawnOptions })
  if(spawnOptions.detached)child.unref()
  const forward = signal => child.kill(signal)
  const interrupt = () => forward('SIGINT'), terminate = () => forward('SIGTERM')
  if(!spawnOptions.detached){process.on('SIGINT', interrupt); process.on('SIGTERM', terminate)}
  try {
    await new Promise((done, fail) => {
      child.once('error', fail)
      child.once('spawn',()=>{Promise.resolve(onStarted?.(child)).catch(error=>{child.kill('SIGTERM');fail(error)})})
      child.once('exit', (code, signal) => code === 0 ? done() : fail(new Error('DSH 已退出：' + (signal ?? code))))
    })
  } finally { if(!spawnOptions.detached){process.off('SIGINT', interrupt); process.off('SIGTERM', terminate)} }
}

export async function desktopIsRunning(paths,desktop) {
  let record
  try {record=await json(join(paths.base,'.desktop-process.json'))}
  catch(error){if(error.code==='ENOENT')return false;throw error}
  if(!Number.isSafeInteger(record.pid) || record.pid<=0 || typeof record.binary!=='string' || !isAbsolute(record.binary) || record.userData!==paths.userData)throw new Error('桌面进程记录无效，原文件已保留。')
  try{process.kill(record.pid,0)}catch(error){if(error.code==='ESRCH')return false;throw error}
  // A recycled PID must still identify this exact application and user-data directory.
  let command
  try{command=(await execute('/bin/ps',['-p',String(record.pid),'-o','command='])).stdout}
  catch(error){if(error.code===1)return false;throw error}
  if(!command.includes(record.binary) || !command.includes('--user-data-dir='+paths.userData))return false
  // macOS can relocate the same signed app between launches.
  if(record.binary!==desktop.binary)await officialDesktop(desktopApplication(record.binary))
  return true
}

async function install(paths, desktop, spec, runner) {
  if (typeof spec !== 'string' || !spec.trim() || spec.startsWith('-') || spec.includes('\0')) throw new Error('请提供标准 DSH 插件安装地址。')
  await runner(desktop.cli, ['plugin', '--profile', 'desktop', 'add', spec, '--ignore-scripts'],
    { cwd: paths.workspace, env: environmentFor(paths) })
}

export function installPlugin(paths, desktop, spec, runner = run) {
  return withFileLock(join(paths.base, 'desktop-instance'), async () => {
    if(await desktopIsRunning(paths,desktop))throw new Error('请先退出此环境的桌面窗口，再从命令行安装插件。')
    return install(paths, desktop, spec, runner)
  }, { waitMs: 0 })
}

async function secretaryArchive(paths, file) {
  const bytes = await readFile(file)
  const digest = createHash('sha256').update(bytes).digest('hex')
  const target = join(paths.packages, 'alcor-secretary-' + digest + '.tgz')
  try { await writeFile(target, bytes, { flag: 'wx', mode: 0o600 }) }
  catch (error) {
    if (error.code !== 'EEXIST') throw error
    const info = await lstat(target)
    if (!info.isFile() || info.isSymbolicLink() || !(await readFile(target)).equals(bytes)) throw new Error('运行环境中的安装包已改变，未覆盖原文件。')
  }
  return target
}

export async function launchEnvironment(paths, desktop, { archive, runner = run, detached=false, onStarted } = {}) {
  const options={cwd:paths.workspace,env:environmentFor(paths),detached, ...(detached ? {stdio:'ignore'} : {})}
  if(await desktopIsRunning(paths,desktop))return runner(desktop.binary,['--user-data-dir='+paths.userData],{...options,onStarted})
  return withFileLock(join(paths.base, 'desktop-instance'), async () => {
    const manifest = await json(join(paths.profile, 'package.json'))
    if (paths.kind === 'alcor' && !manifest.dependencies?.['@alcor/secretary']) {
      if (!archive) throw new Error('请先运行 npm run product:pack，构建 Alcor 原生安装包。')
      await install(paths, desktop, await secretaryArchive(paths, archive), runner)
    }
    let pid
    try{
      await runner(desktop.binary, ['--user-data-dir=' + paths.userData], {...options,onStarted:async child=>{
        pid=child.pid
        await writeFileAtomic(join(paths.base,'.desktop-process.json'),JSON.stringify({pid,binary:desktop.binary,userData:paths.userData})+'\n',{mode:0o600})
        await onStarted?.(child)
      }})
    }finally{
      const file=join(paths.base,'.desktop-process.json')
      if(pid && (await json(file).catch(error=>{if(error.code==='ENOENT')return {};throw error})).pid===pid)await unlink(file)
    }
  }, { waitMs: 0 })
}

async function main() {
  const { values, positionals } = parseArgs({ options: { root: { type: 'string' }, app: { type: 'string' } }, allowPositionals: true })
  const [command = 'launch', kind = 'alcor', spec, ...extra] = positionals
  if (!['init', 'launch', 'install', 'status'].includes(command) || extra.length || (command === 'install' ? !spec : spec)) {
    throw new Error('用法：environments.mjs init|launch|status alcor|dsh，或 install alcor|dsh <插件地址>；可指定 --root、--app。')
  }
  const paths = await prepareEnvironment(resolve(values.root ?? process.env.ALCOR_ENVIRONMENTS_ROOT ?? join(homedir(), '.local', 'share', 'Alcor', 'environments')), kind)
  if (command === 'init' || command === 'status') { console.log(JSON.stringify(paths, null, 2)); return }
  const desktop = await officialDesktop(values.app)
  if (command === 'install') return installPlugin(paths, desktop, spec)
  const pkg = JSON.parse(await readFile(new URL('../../packages/alcor-secretary/package.json', import.meta.url)))
  const archive = join(repository, '.local', 'artifacts', `alcor-secretary-${pkg.version}.tgz`)
  await launchEnvironment(paths, desktop, { archive })
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main().catch(error => { console.error(error.message); process.exitCode = 1 })
}
