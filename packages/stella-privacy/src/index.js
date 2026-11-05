import { Service } from '@deepseek-ai/cordis'
import z from '@deepseek-ai/schemastery'
import { CredentialProvider, isCredentialKeySegment, isCredentialRefName } from '@deepseek-ai/dsh-credentials'
import { withFileLock } from '@deepseek-ai/dsh-atomic-write'
import { resolveDshHome } from '@deepseek-ai/dsh-home-paths'
import { createHash } from 'node:crypto'
import { realpath } from 'node:fs/promises'
import { join } from 'node:path'
import { keychainRequest } from './keychain.js'

function plain(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    && [Object.prototype, null].includes(Object.getPrototypeOf(value))
}

function reference(value) {
  if (typeof value !== 'string' || !isCredentialRefName(value)) throw new Error('Invalid credential reference.')
}

function recordKey(value) {
  if (typeof value !== 'string' || value.split('/').length !== 2 || !value.split('/').every(isCredentialKeySegment)) {
    throw new Error('Invalid credential record key.')
  }
}

function jsonValue(value, seen = new Set()) {
  if (value === null || ['string', 'boolean'].includes(typeof value) || (typeof value === 'number' && Number.isFinite(value))) return
  if ((!Array.isArray(value) && !plain(value)) || seen.has(value) || Object.getOwnPropertySymbols(value).length) {
    throw new Error('Credential data must contain only finite JSON values.')
  }
  seen.add(value)
  for (const child of Array.isArray(value) ? value : Object.values(value)) jsonValue(child, seen)
  seen.delete(value)
}

function recordValue(value) {
  if (!plain(value)) throw new Error('Invalid credential record.')
  if (value.kind === 'grant' && Object.hasOwn(value, 'payload') && Object.keys(value).every(key => ['kind', 'payload'].includes(key))) {
    jsonValue(value.payload)
  } else if (value.kind === 'api-key' && Object.keys(value).every(key => ['kind', 'key', 'env'].includes(key))
    && (!Object.hasOwn(value, 'key') || (typeof value.key === 'string' && value.key.length > 0))
    && (!Object.hasOwn(value, 'env') || (plain(value.env) && Object.entries(value.env).every(([name, entry]) => isCredentialRefName(name) && typeof entry === 'string')))) {
    jsonValue(value)
  } else {
    throw new Error('Invalid credential record.')
  }
  return JSON.parse(JSON.stringify(value))
}

function documentValue(text) {
  if (text === undefined) return { version: 1, refs: {}, records: {} }
  try {
    const value = JSON.parse(text)
    if (!plain(value) || value.version !== 1 || !plain(value.refs) || !plain(value.records)
      || Object.keys(value).some(key => !['version', 'refs', 'records'].includes(key))) throw new Error()
    for (const [name, entry] of Object.entries(value.refs)) {
      reference(name)
      if (typeof entry !== 'string' || entry.length === 0) throw new Error()
    }
    for (const [key, entry] of Object.entries(value.records)) { recordKey(key); recordValue(entry) }
    return value
  } catch {
    throw new Error('Stored Keychain credentials are invalid; repair or restore them before continuing.')
  }
}

export default class KeychainCredentials extends CredentialProvider {
  static Config = z.object({ dshHome: z.string(), keychainPath: z.string() })

  constructor(ctx, config) {
    super(ctx)
    this.config = config
    this.closed = false
    ctx.effect(() => () => { this.closed = true })
  }

  async [Service.init]() {
    if (process.platform !== 'darwin') throw new Error('macOS Keychain is required.')
    const home = await realpath(resolveDshHome(this.config.dshHome))
    this.lockPath = join(home, '.stella-keychain-credentials')
    this.keychainPath = this.config.keychainPath === undefined ? undefined : await realpath(this.config.keychainPath)
    this.service = 'com.stella.dsh.credentials.v1.' + createHash('sha256').update(home).digest('hex')
  }

  assertActive() {
    if (this.closed) throw new Error('Keychain credentials provider is stopped.')
  }

  async readDocument() {
    this.assertActive()
    const value = await keychainRequest({ service: this.service, keychainPath: this.keychainPath, operation: 'read' })
    this.assertActive()
    return documentValue(value)
  }

  async change(mutate) {
    this.assertActive()
    return withFileLock(this.lockPath, async () => {
      const current = await this.readDocument()
      const result = await mutate(current)
      if (!result.changed) return result.value
      this.assertActive()
      const value = JSON.stringify(current)
      if (Buffer.byteLength(value) > 1_048_576) throw new Error('Keychain credential storage exceeds its size limit; existing values are retained.')
      await keychainRequest({ service: this.service, keychainPath: this.keychainPath, operation: 'write', value })
      if (result.ref !== undefined) this.notifyUpdated(result.ref)
      if (result.key !== undefined) this.notifyRecordUpdated(result.key)
      return result.value
    }, { waitMs: 30_000 })
  }

  async resolve(ref) {
    reference(ref)
    const document = await this.readDocument()
    return Object.hasOwn(document.refs, ref) ? { value: document.refs[ref], source: 'keychain' } : undefined
  }

  async describe(ref) {
    const value = await this.resolve(ref)
    return { configured: value !== undefined, writable: true, ...(value === undefined ? {} : { source: 'keychain' }) }
  }

  async set(ref, value) {
    reference(ref)
    if (typeof value !== 'string' || !value.length) throw new Error('Credential value must be a non-empty string.')
    await this.change(document => {
      if (Object.hasOwn(document.refs, ref) && document.refs[ref] === value) return { changed: false }
      document.refs = { ...document.refs, [ref]: value }
      return { changed: true, ref }
    })
  }

  async unset(ref) {
    reference(ref)
    await this.change(document => {
      if (!Object.hasOwn(document.refs, ref)) return { changed: false }
      delete document.refs[ref]
      return { changed: true, ref }
    })
  }

  async readRecord(key) {
    recordKey(key)
    const document = await this.readDocument()
    return Object.hasOwn(document.records, key) ? document.records[key] : undefined
  }

  async describeRecord(key) {
    const value = await this.readRecord(key)
    return { configured: value !== undefined, writable: true, ...(value === undefined ? {} : { kind: value.kind }) }
  }

  async listRecords() {
    const document = await this.readDocument()
    return Object.entries(document.records).map(([key, value]) => ({ key, kind: value.kind }))
  }

  async modifyRecord(key, mutate) {
    recordKey(key)
    return this.change(async document => {
      const current = Object.hasOwn(document.records, key) ? document.records[key] : undefined
      let next
      try { next = await mutate(current === undefined ? undefined : structuredClone(current)) }
      catch { throw new Error('Credential update failed; the stored value is unchanged.') }
      if (next === undefined) return { changed: false, value: current }
      const value = recordValue(next)
      if (JSON.stringify(current) === JSON.stringify(value)) return { changed: false, value }
      document.records = { ...document.records, [key]: value }
      return { changed: true, key, value }
    })
  }

  async deleteRecord(key) {
    recordKey(key)
    await this.change(document => {
      if (!Object.hasOwn(document.records, key)) return { changed: false }
      delete document.records[key]
      return { changed: true, key }
    })
  }
}
