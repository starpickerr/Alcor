import { extname, isAbsolute } from 'node:path'

function filePath(path, extensions) {
  if (typeof path !== 'string' || path.length > 4096 || path.includes('\0') || !isAbsolute(path) || !extensions.includes(extname(path).toLowerCase())) throw new Error('文件路径或格式无效。')
}

export async function readDateFile(fs, path, signal) {
  filePath(path, ['.ics', '.csv'])
  const target = await fs.resolve(path, { signal })
  const info = await fs.stat(target, signal)
  if (info?.type !== 'file' || info.size > 1024 * 1024) throw new Error('日期文件必须为普通文件，且不超过 1 MB。')
  const bytes = await fs.readBytes(target, signal, 1024 * 1024)
  if (bytes.length > 1024 * 1024) throw new Error('日期文件不能超过 1 MB。')
  return { content: new TextDecoder('utf-8', { fatal: true }).decode(bytes), format: extname(path).slice(1).toLowerCase() }
}

export async function writeBillCsv(ctx, path, content, signal, session) {
  filePath(path, ['.csv'])
  const fs = ctx.get('fs')
  if (!fs) throw new Error('当前对话无法访问文件。')
  const provider = ctx.get('sandboxPolicy')
  if (fs.sandboxMode !== undefined && !provider) throw new Error('当前文件写入策略不可用。')
  const policy = provider?.resolve({ session })
  const target = await fs.resolve(path, { signal })
  await fs.writeText(target, '\ufeff' + content, { kind: 'createIfAbsent' }, signal, policy)
  return { path, saved: true }
}
