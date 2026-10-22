import fs from 'node:fs'
import path from 'node:path'

const MAX_LINE = 16 * 1024
const logs = new Map<string, RuntimeLog>()

export function redactLog(value: string): string {
  if (value.trimStart().startsWith('{')) {
    try {
      const secret = /^(password|passwd|token|api[_-]?key|cookie|authorization|client[_-]?secret|access[_-]?token|refresh[_-]?token)$/i
      const clean = (item: unknown, depth = 0): unknown => {
        if (depth > 40) return '[Nested diagnostic omitted]'
        if (typeof item === 'string') return redactLog(item)
        if (Array.isArray(item)) return item.map(entry => clean(entry, depth + 1))
        if (item && typeof item === 'object') return Object.fromEntries(Object.entries(item)
          .map(([key, entry]) => [key, secret.test(key) ? '[REDACTED]' : clean(entry, depth + 1)]))
        return item
      }
      return JSON.stringify(clean(JSON.parse(value)))
    } catch { return '[Malformed structured diagnostic omitted]' }
  }
  return value
    .replace(/([?&](?:token|access_token|api_key|key|password|secret|code)=)[^\s&#"']+/gi, '$1[REDACTED]')
    .replace(/\bBearer\s+[^\s,;"']+/gi, 'Bearer [REDACTED]')
    .replace(/\bsk-[A-Za-z0-9_-]{16,}/gi, 'sk-[REDACTED]')
    .replace(/(["']?(?:password|passwd|token|api[_-]?key|cookie|authorization|client[_-]?secret|access[_-]?token|refresh[_-]?token)["']?\s*[:=]\s*)("[^"\n]*"|'[^'\n]*'|[^\s,;}]+)/gi,
      (_match, prefix: string, secret: string) => prefix + (secret.startsWith('"') ? '"[REDACTED]"' : secret.startsWith("'") ? "'[REDACTED]'" : '[REDACTED]'))
}

/** One writer per desktop data directory; backend chunks never bypass redaction. */
export class RuntimeLog {
  private file: string
  private maxBytes: number
  private backups: number
  private pending = ''
  private dropping = false

  constructor(directory: string, maxBytes = 5 * 1024 * 1024, backups = 3) {
    this.file = path.join(directory, 'run.log')
    this.maxBytes = maxBytes
    this.backups = backups
  }

  private append(text: string): void {
    try {
      fs.mkdirSync(path.dirname(this.file), { recursive: true })
      if (fs.existsSync(this.file) && !fs.lstatSync(this.file).isFile()) return
      const bytes = Buffer.byteLength(text)
      const size = fs.existsSync(this.file) ? fs.statSync(this.file).size : 0
      if (size && size + bytes > this.maxBytes) {
        for (let index = this.backups; index >= 1; index--) {
          const from = index === 1 ? this.file : `${this.file}.${index - 1}`
          const to = `${this.file}.${index}`
          if (fs.existsSync(to)) fs.unlinkSync(to)
          if (fs.existsSync(from)) fs.renameSync(from, to)
        }
      }
      const descriptor = fs.openSync(this.file, fs.constants.O_WRONLY | fs.constants.O_APPEND | fs.constants.O_CREAT |
        (fs.constants.O_NONBLOCK ?? 0) | (fs.constants.O_NOFOLLOW ?? 0), 0o600)
      try {
        if (fs.fstatSync(descriptor).isFile()) fs.writeFileSync(descriptor, text, 'utf8')
      } finally { fs.closeSync(descriptor) }
    } catch { /* Logging must never fail a business operation or startup. */ }
  }

  write(chunk: string): void {
    const pieces = chunk.split('\n')
    for (let index = 0; index < pieces.length; index++) {
      const piece = pieces[index]
      if (!this.dropping) {
        if (this.pending.length + piece.length > MAX_LINE * 2) {
          this.pending = ''
          this.dropping = true
        } else this.pending += piece
      }
      if (index < pieces.length - 1) {
        if (this.dropping) this.event('shell', 'WARNING', 'Oversized diagnostic line omitted')
        else this.append(redactLog(this.pending).slice(0, MAX_LINE) + '\n')
        this.pending = ''
        this.dropping = false
      }
    }
  }

  event(source: string, level: string, message: string, location = ''): void {
    this.append(JSON.stringify({ timestamp: new Date().toISOString(), component: 'desktop',
      source, level, message: redactLog(message).slice(0, MAX_LINE), location: redactLog(location).slice(0, 512) }) + '\n')
  }

  end(): void {
    if (this.pending && !this.dropping) this.append(redactLog(this.pending).slice(0, MAX_LINE) + '\n')
    this.pending = ''
    this.dropping = false
  }
}

export function getRuntimeLog(directory: string): RuntimeLog {
  let log = logs.get(directory)
  if (!log) { log = new RuntimeLog(directory); logs.set(directory, log) }
  return log
}
