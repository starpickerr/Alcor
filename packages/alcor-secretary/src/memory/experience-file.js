import { experiences } from './experience-schema.js'
const encoder = new TextEncoder()
const magic = encoder.encode('ALCOR-MEM1')
export const fileLimit = 4 * 1024 * 1024

async function keyFor(password, salt) {
  if (typeof password !== 'string' || password.length < 12) throw Error('请使用至少 12 个字符的档案口令。')
  const material = await crypto.subtle.importKey('raw', encoder.encode(password), 'PBKDF2', false, ['deriveKey'])
  return crypto.subtle.deriveKey({ name: 'PBKDF2', salt, iterations: 600000, hash: 'SHA-256' }, material,
    { name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt'])
}
export async function sealExperiences(document, password) {
  const bytes = encoder.encode(JSON.stringify(experiences.parse(document)))
  if (bytes.length + magic.length + 44 > fileLimit) throw Error('经历档案超过 4 MiB。')
  const salt = crypto.getRandomValues(new Uint8Array(16)), iv = crypto.getRandomValues(new Uint8Array(12))
  const key = await keyFor(password, salt)
  const cipher = await crypto.subtle.encrypt({ name: 'AES-GCM', iv, additionalData: magic }, key, bytes)
  const packed = new Uint8Array(magic.length + 28 + cipher.byteLength)
  packed.set(magic); packed.set(salt, magic.length); packed.set(iv, magic.length + 16)
  packed.set(new Uint8Array(cipher), magic.length + 28)
  return packed
}
export async function openExperiences(bytes, password) {
  if (bytes.length < magic.length + 44 || bytes.length > fileLimit || !magic.every((v,i) => bytes[i] === v)) {
    throw Error('请选择有效的 Alcor 加密经历档案。')
  }
  const key = await keyFor(password, bytes.slice(magic.length, magic.length + 16))
  let clear
  try {
    clear = await crypto.subtle.decrypt({ name: 'AES-GCM', iv: bytes.slice(magic.length + 16, magic.length + 28), additionalData: magic },
      key, bytes.slice(magic.length + 28))
  } catch { throw Error('口令不匹配或档案已损坏。') }
  return experiences.parse(JSON.parse(new TextDecoder().decode(clear)))
}
