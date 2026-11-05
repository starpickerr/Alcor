import { basename, extname, isAbsolute } from 'node:path'

export async function readBillFiles(fs, paths, signal) {
  if (!Array.isArray(paths) || !paths.length || paths.length > 5 || paths.some(path => typeof path !== 'string' || path.length > 4096 || path.includes('\0') || !isAbsolute(path) || !['.xlsx', '.csv'].includes(extname(path).toLowerCase()))) throw new Error('请提供 1 至 5 个 CSV／XLSX 账单的绝对路径。')
  const files = []
  let total = 0
  for (const path of paths) {
    signal?.throwIfAborted()
    const target = await fs.resolve(path, { signal })
    const info = await fs.stat(target, signal)
    if (info?.type !== 'file' || (info.size !== undefined && info.size > 5 * 1024 * 1024)) throw new Error('账单必须是普通文件，且不能超过 5 MB。')
    const bytes = await fs.readBytes(target, signal, 5 * 1024 * 1024)
    total += bytes.length
    if (bytes.length > 5 * 1024 * 1024 || total > 20 * 1024 * 1024) throw new Error('账单文件大小超限。')
    files.push({ name: basename(path), content: Buffer.from(bytes).toString('base64') })
  }
  return files
}
