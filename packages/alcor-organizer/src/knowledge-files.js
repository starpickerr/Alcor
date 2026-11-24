import { createHash } from 'node:crypto'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { basename, extname, isAbsolute, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const run = promisify(execFile)
const limit = 5 * 1024 * 1024
const kinds = new Map([
  ['.pdf', 'pdf'],
  ['.png', 'image'],
  ['.jpg', 'image'],
  ['.jpeg', 'image'],
  ['.txt', 'text'],
  ['.md', 'text'],
])
const helper = new URL('../native/knowledge-text.js', import.meta.url)
const windowsHelper = new URL('../native/knowledge-text-windows.ps1', import.meta.url)
const extractionOptions = { timeout: 30000, maxBuffer: 10_000_000 }

async function extractWindowsText(path, kind) {
  const executable = join(process.env.SystemRoot, 'System32/WindowsPowerShell/v1.0/powershell.exe')
  const script = await readFile(windowsHelper, 'utf8')
  return run(executable, ['-NoProfile', '-NonInteractive', '-EncodedCommand', Buffer.from(script, 'utf16le').toString('base64')], {
    ...extractionOptions,
    windowsHide: true,
    env: { ...process.env, ALCOR_KNOWLEDGE_SOURCE: path, ALCOR_KNOWLEDGE_KIND: kind },
  })
}

export async function readKnowledgeFile(fs, path, signal) {
  if (
    typeof path !== 'string' ||
    path.includes('\0') ||
    !isAbsolute(path) ||
    !kinds.has(extname(path).toLowerCase())
  )
    throw new Error('请选择 PDF、PNG、JPEG、TXT 或 Markdown 文件的绝对路径。')
  const target = await fs.resolve(path, { signal })
  const info = await fs.stat(target, signal)
  if (info?.type !== 'file' || info.size > limit) throw new Error('请选择 5 MB 以内的文件。')
  const bytes = await fs.readBytes(target, signal, limit)
  if (!bytes.length || bytes.length > limit) throw new Error('请选择 5 MB 以内的非空文件。')
  return { filename: basename(path), content: Buffer.from(bytes).toString('base64') }
}

export async function indexKnowledgeFile(args, lookup, script = helper) {
  const { filename, content } = args
  if (typeof filename !== 'string' || filename.length > 240 || /[\\/\x00-\x1f]/.test(filename))
    throw new Error('文件名无效。')
  const ext = extname(filename).toLowerCase(),
    kind = kinds.get(ext)
  if (!kind || typeof content !== 'string' || !content.length || content.length > Math.ceil(limit / 3) * 4)
    throw new Error('请选择 5 MB 以内的 PDF、图片或文本。')
  const bytes = Buffer.from(content, 'base64')
  if (!bytes.length || bytes.length > limit || bytes.toString('base64') !== content)
    throw new Error('文件内容无效。')
  if (
    (ext === '.pdf' && !bytes.subarray(0, 1024).includes('%PDF-')) ||
    (ext === '.png' && !bytes.subarray(0, 8).equals(Buffer.from('89504e470d0a1a0a', 'hex'))) ||
    (['.jpeg', '.jpg'].includes(ext) && !bytes.subarray(0, 3).equals(Buffer.from('ffd8ff', 'hex')))
  )
    throw new Error('文件内容与格式不符。')
  const base = {
    title: args.title || filename,
    text: args.text || '',
    collection: args.collection ?? '',
    filename,
    kind,
    content,
  }
  const cached = await lookup?.(createHash('sha256').update(bytes).digest('hex'))
  if (cached) return { ...base, ...cached }
  let pages = [''],
    truncated = false
  if (kind === 'text') {
    try {
      const text = new TextDecoder('utf-8', { fatal: true }).decode(bytes)
      pages = text.match(/[\s\S]{1,48000}/gu) || ['']
    } catch {
      throw new Error('请使用 UTF-8 编码的文本。')
    }
  } else if (process.platform === 'darwin' || process.platform === 'win32') {
    const dir = await mkdtemp(join(tmpdir(), 'alcor-knowledge-'))
    try {
      const path = join(dir, 'source' + ext)
      await writeFile(path, bytes, { mode: 0o600 })
      const { stdout } = process.platform === 'win32'
        ? await extractWindowsText(path, kind)
        : await run('/usr/bin/osascript', ['-l', 'JavaScript', fileURLToPath(script), path, kind], extractionOptions)
      const result = JSON.parse(stdout)
      pages = Array.isArray(result) ? result : result.pages
      truncated = result.truncated === true
    } catch {
      /* An unreadable scan can still be saved, annotated, and opened. */
    } finally {
      await rm(dir, { recursive: true, force: true })
    }
  }
  let room = kind === 'text' ? limit : 2_000_000
  pages = pages.map((text) => {
    const excerpt = text.slice(0, Math.min(room, 64000)).replaceAll('\0', '')
    truncated ||= excerpt.length < text.length
    room -= excerpt.length
    return excerpt
  })
  const index_notice = !pages.some((p) => p.trim())
    ? '原件已保存。可添加文字摘录以便检索。'
    : truncated || pages.some((p) => !p.trim())
      ? '部分内容可搜索，完整内容请查看原件。'
      : ''
  return { ...base, pages, index_notice }
}
