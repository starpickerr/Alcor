import { authorityStore } from '../../packages/alcor-secretary/src/memory/aurabot/authority.js'
import { cp, lstat, readdir, readFile, writeFile, mkdir, rename, rm, chmod } from 'node:fs/promises'
import { join, resolve } from 'node:path'
import { randomUUID, createHash } from 'node:crypto'
import { fileURLToPath } from 'node:url'
import { createScopedMemory } from '../../packages/alcor-secretary/src/memory/scoped.js'

async function exists(path) { try { await lstat(path); return true } catch (error) { if (error.code === 'ENOENT') return false; throw error } }
async function cold(path) {
  if (!(await exists(path))) return
  if ((await lstat(path)).isSymbolicLink()) throw Error('Memory source must not be a symbolic link')
  const lock = join(path, '.lock')
  if (await exists(lock)) {
    const { pid } = JSON.parse(await readFile(lock, 'utf8'))
    if (!Number.isSafeInteger(pid) || pid < 1) throw Error('Invalid memory writer lock')
    try { process.kill(pid, 0); throw Error('Memory still has a running writer; quit its DSH Profile first') }
    catch (error) { if (error.code !== 'ESRCH') throw error }
  }
}
export async function migrateAssistantMemory({ profile, personal, groups, participants = {} }) {
  profile = resolve(profile)
  const target = join(profile, 'alcor/memory')
  await cold(personal)
  if (await exists(target)) throw Error('Alcor memory already exists; existing content was preserved')
  const staging = join(profile, 'alcor/.memory-import-' + randomUUID())
  await mkdir(staging, { recursive: true, mode: 0o700 })
  const receipt = { version: 1, createdAt: new Date().toISOString(), files: [] }
  async function copy(source, destination, personalOnly = false) {
    await cold(source)
    await mkdir(destination, { recursive: true, mode: 0o700 })
    if (await exists(source)) for (const name of await readdir(source)) {
      if (personalOnly && !['pinned.md', 'memory.jsonl'].includes(name)) continue
      if (name === '.lock' || name === 'backups' || name === 'undo' || name.includes('.bak') || name.includes('.tmp')) continue
      const file = join(source, name), info = await lstat(file)
      if (info.isSymbolicLink()) throw Error('Memory import refuses symbolic links')
      if (info.isDirectory()) {
        if (name !== 'journal') continue
        await mkdir(join(destination, name), { mode: 0o700 })
        for (const child of await readdir(file)) {
          if (!/^\d{4}-\d{2}-\d{2}\.jsonl$/.test(child)) continue
          const item = join(file, child)
          if (!(await lstat(item)).isFile() || (await lstat(item)).isSymbolicLink()) throw Error('Invalid journal source')
          await cp(item, join(destination, name, child), { errorOnExist: true, force: false })
          await chmod(join(destination, name, child), 0o600)
          receipt.files.push({ source: item, sha256: createHash('sha256').update(await readFile(item)).digest('hex') })
        }
      } else if (info.isFile()) {
        await cp(file, join(destination, name), { errorOnExist: true, force: false }); await chmod(join(destination, name), 0o600)
        receipt.files.push({ source: file, sha256: createHash('sha256').update(await readFile(file)).digest('hex') })
      }
    }
  }
  try {
    await copy(personal, join(staging, 'personal'), true)
    let memory = createScopedMemory({ dataDir: join(staging, 'personal'), audience: 'personal' })
    await memory.dispose()
    const legacyPersona = join(personal, 'persona.md')
    if (await exists(legacyPersona)) {
      if ((await lstat(legacyPersona)).isSymbolicLink()) throw Error('Invalid private persona source')
      const content = await readFile(legacyPersona, 'utf8')
      await writeFile(join(staging, 'personal/legacy-persona.private.md'), content, { mode: 0o600 })
      // Old private prompts can contain owner facts. Import as private data, never as the public SOUL.
      const db = authorityStore(join(staging, 'personal')), data = db.load()
      if (content.trim()) data.entries.push({ id: 'legacy-persona', content: content.trim(), subject: 'owner', audience: 'personal', source: { kind: 'legacy-import', at: '' } })
      db.save(data)
      receipt.files.push({ source: legacyPersona, sha256: createHash('sha256').update(content).digest('hex') })
    }
    if (groups && await exists(groups)) {
      await cold(groups)
      for (const id of await readdir(groups)) {
        if (!/^[1-9]\d{0,19}$/.test(id)) continue
        const destination = join(staging, 'groups', id)
        await copy(join(groups, id), destination)
        memory = createScopedMemory({ dataDir: destination, audience: 'group:' + id })
        await memory.dispose()
        if (participants[id]) {
          if (!Array.isArray(participants[id]) || participants[id].some(value => !/^[1-9]\d{0,19}$/.test(value))) throw Error('Invalid verified participant')
          const db = authorityStore(destination), data = db.load()
          data.participants = [...new Set([...data.participants, ...participants[id]])]
          db.save(data)
        }
      }
    }
    await writeFile(join(staging, '.import.json'), JSON.stringify(receipt, null, 2) + '\n', { mode: 0o600 })
    await rename(staging, target)
    return { files: receipt.files.length, target }
  } finally { await rm(staging, { recursive: true, force: true }) }
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const options = JSON.parse(await readFile(process.argv[2], 'utf8'))
  console.log(JSON.stringify(await migrateAssistantMemory(options)))
}
