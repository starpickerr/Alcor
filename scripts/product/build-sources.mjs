import { build } from 'esbuild'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import { createHash } from 'node:crypto'
import { cp, mkdir, readFile, readdir, rm, writeFile } from 'node:fs/promises'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = fileURLToPath(new URL('../../', import.meta.url))
const lock = JSON.parse(await readFile(join(root, 'product/source-releases.lock.json'), 'utf8'))
const run = promisify(execFile)
if (process.platform !== 'darwin' || process.arch !== 'arm64') throw new Error('此发行构建仅用于 macOS Apple Silicon。')
for (const source of lock.sources) {
  const repo = join(root, '..', 'MCP', source.repo)
  const files = ['mcp_server.py', 'pyproject.toml', ...(await readdir(join(repo, source.module))).filter(name => /\.(py|json|sql)$/.test(name)).map(name => source.module + '/' + name)].sort()
  const identity = (await Promise.all(files.map(async name => name + '\0' + createHash('sha256').update(await readFile(join(repo, name))).digest('hex') + '\n'))).join('')
  if (createHash('sha256').update(identity).digest('hex') !== source.sourceSha256) throw new Error(source.repo + ' 源码已变化，请先验证并更新发行锁。')
  const python = join(repo, '.venv/bin/python')
  const metadata = JSON.parse((await run(python, ['-B', '-c', 'import importlib.metadata as m,json,sysconfig; print(json.dumps({"versions":{n:m.version(n) for n in '+JSON.stringify(Object.keys(source.dependencies))+'},"license":sysconfig.get_path("stdlib")+"/LICENSE.txt"}))'])).stdout)
  for (const [name, version] of Object.entries(source.dependencies)) if (metadata.versions[name] !== version) throw new Error(source.repo + ' 构建依赖版本不符：' + name)
  const freeze = await run(python, ['-B', '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--name', source.binary,
    '--distpath', join(root, '.local/source-servers'), '--workpath', join(root, '.local/source-build', source.binary), '--specpath', join(root, '.local/source-specs'),
    '--paths', repo, '--collect-data', source.module, '--collect-submodules', 'mcp', '--exclude-module', 'pytest', '--exclude-module', 'numpy', '--exclude-module', 'torch', join(repo, 'mcp_server.py')],
    { cwd: repo, env: { ...process.env, PYINSTALLER_CONFIG_DIR: join(root, '.local/source-cache', source.binary) }, maxBuffer: 4 * 1024 * 1024 })
  await writeFile(join(root, '.local/source-build', source.binary + '.log'), freeze.stdout + freeze.stderr)
  const directory = join(root, 'packages', source.package)
  await rm(join(directory, 'server'), { recursive: true, force: true })
  // npm archives omit symlinks; frozen libraries must be real files in the bundle.
  await cp(join(root, '.local/source-servers', source.binary), join(directory, 'server'), { recursive: true, dereference: true })
  await writeFile(join(directory, 'server', 'source.json'), JSON.stringify(source, null, 2) + '\n')
  await mkdir(join(directory, 'server/licenses'), { recursive: true })
  await cp(metadata.license, join(directory, 'server/licenses/Python-LICENSE.txt'))
  await cp(join(root, 'product/DSH-LICENSE.txt'), join(directory, 'DSH-LICENSE.txt'))
  await mkdir(join(directory, 'lib'), { recursive: true })
  await build({ entryPoints: [join(directory, 'src/index.ts')], outdir: join(directory, 'lib'), bundle: true, packages: 'external', format: 'esm', platform: 'node', target: 'node24' })
  const client = await build({ entryPoints: [join(directory, 'src/client.jsx')], bundle: true, external: ['react', 'react-dom', 'zod', '@deepseek-ai/cordis', '@deepseek-ai/dsh-client-ui-primitives'], loader: { '.css': 'text' }, format: 'cjs', platform: 'browser', write: false, target: 'es2022' })
  const id = JSON.parse(await readFile(join(directory, 'package.json'), 'utf8')).name
  await writeFile(join(directory, 'lib/client.js'), `window.__ModuleLoader__.load({id:${JSON.stringify(id)},factory(require){const module={exports:{}};const exports=module.exports;\n${client.outputFiles[0].text}\nreturn module.exports;}});\n`)
  await writeFile(join(directory, 'THIRD_PARTY_NOTICES.md'), `# ${id}\n\nMCP ${source.version}，来源：本工作区独立 ${source.repo}。源码摘要见 server/source.json。\n\n包含 Python、MCP Python SDK（MIT）、PyInstaller（GPL with bootloader exception）${source.module === 'elearning_mcp' ? '、Playwright（Apache-2.0）' : ''} 及其运行依赖。许可证随冻结运行库的 dist-info 与 licenses 目录分发。官方 DSH SDK 使用宿主可选 peer，不包含宿主副本。\n`)
  console.log('Built', id)
}
