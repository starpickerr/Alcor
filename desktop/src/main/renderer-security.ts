import { BrowserWindow, ipcMain, systemPreferences } from 'electron'
import type { IpcMainEvent, IpcMainInvokeEvent } from 'electron'
import path from 'node:path'
import fs from 'node:fs'
import { fileURLToPath } from 'node:url'

/** Hash routes do not change the trusted document; prefix matches do. */
export function isTrustedRendererUrl(value: string, expected: string): boolean {
  try {
    const actual = new URL(value)
    const trusted = new URL(expected)
    return !actual.username && !actual.password && actual.protocol === trusted.protocol &&
      actual.host === trusted.host && actual.pathname === trusted.pathname
  } catch { return false }
}

export type TrustedIpc = Pick<typeof ipcMain, 'handle' | 'on'>

export function createTrustedIpc(getWindow: () => BrowserWindow | null, getRendererUrl: () => string): TrustedIpc {
  const allowed = (event: IpcMainEvent | IpcMainInvokeEvent) => {
    const window = getWindow()
    return !!window && !window.isDestroyed() && event.sender === window.webContents &&
      event.senderFrame === window.webContents.mainFrame &&
      isTrustedRendererUrl(event.senderFrame.url, getRendererUrl())
  }
  return {
    handle(channel, listener) {
      ipcMain.handle(channel, (event, ...args) => {
        if (!allowed(event)) throw new Error('Privileged IPC requires the bound main renderer document')
        return listener(event, ...args)
      })
    },
    on(channel, listener) {
      ipcMain.on(channel, (event, ...args) => {
        if (!allowed(event)) { event.returnValue = null; return }
        listener(event, ...args)
      })
      return ipcMain
    },
  }
}

export function safeExternalUrl(value: string): boolean {
  try {
    const url = new URL(value)
    return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password
  } catch { return false }
}

const documentExtensions = new Set(['.txt', '.md', '.csv', '.tsv', '.json', '.pdf', '.png', '.jpg', '.jpeg',
  '.gif', '.webp', '.heic', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.mp3', '.mp4', '.wav', '.m4a', '.mov'])

/** Unknown/executable documents are revealed in Finder rather than executed. */
export function localPathAction(value: string): 'open' | 'reveal' {
  const target = fs.realpathSync(value)
  const stat = fs.statSync(target)
  if (stat.isDirectory()) return /\.(app|bundle|plugin)$/i.test(target) ? 'reveal' : 'open'
  return stat.isFile() && !(stat.mode & 0o111) && documentExtensions.has(path.extname(target).toLowerCase())
    ? 'open' : 'reveal'
}

export function decodeIconDataUrl(value: string): Buffer | null {
  if (value.length > 6 * 1024 * 1024) return null
  const match = /^data:image\/(?:png|x-icon|vnd\.microsoft\.icon);base64,([A-Za-z0-9+/]+={0,2})$/.exec(value)
  if (!match) return null
  const bytes = Buffer.from(match[1], 'base64')
  return bytes.length <= 4 * 1024 * 1024 ? bytes : null
}

export function hardenRenderer(window: BrowserWindow, rendererUrl: string,
  getBackendOrigin: () => string, openExternal: (url: string) => void, getBackendToken?: () => string): void {
  const contents = window.webContents
  const session = contents.session
  const navigation = (event: Electron.Event, url: string) => {
    if (!isTrustedRendererUrl(url, rendererUrl)) event.preventDefault()
  }
  contents.on('will-navigate', navigation)
  contents.on('will-redirect', navigation)
  contents.on('will-attach-webview', event => event.preventDefault())
  contents.setWindowOpenHandler(({ url }) => {
    if (safeExternalUrl(url)) openExternal(url)
    return { action: 'deny' }
  })
  const trusted = (webContents: Electron.WebContents | null, requestingUrl?: string) =>
    webContents === contents && !!requestingUrl && isTrustedRendererUrl(requestingUrl, rendererUrl) &&
    isTrustedRendererUrl(contents.getURL(), rendererUrl)
  session.setPermissionCheckHandler((webContents, permission, _origin, details) =>
    trusted(webContents, details.requestingUrl) && details.isMainFrame &&
    (['local-network', 'local-network-access', 'loopback-network'].includes(permission) ||
     permission === 'media' && details.mediaType === 'audio' &&
       (process.platform !== 'darwin' || systemPreferences.getMediaAccessStatus('microphone') === 'granted')))
  session.setPermissionRequestHandler((webContents, permission, callback, details) => {
    const audio = 'mediaTypes' in details && details.mediaTypes?.length === 1 && details.mediaTypes[0] === 'audio'
    if (!trusted(webContents, details.requestingUrl) || !details.isMainFrame) { callback(false); return }
    if (permission === 'media' && audio && process.platform === 'darwin') {
      // Request native microphone access only when the UI actually requests
      // audio. Do not prompt for system permissions during application startup.
      void systemPreferences.askForMediaAccess('microphone').then(
        granted => callback(granted && trusted(webContents, details.requestingUrl)), () => callback(false))
    } else callback(['local-network', 'local-network-access', 'loopback-network'].includes(permission) || permission === 'media' && !!audio)
  })
  // Renderer requests never become a general host network/file proxy. Main
  // process connectors use their own transports, not this renderer session.
  session.webRequest.onBeforeRequest((details, callback) => {
    try {
      const url = new URL(details.url)
      if (url.protocol === 'data:' || url.protocol === 'blob:') { callback({}); return }
      const renderer = new URL(rendererUrl)
      let allowed = false
      if (url.protocol === 'file:' && renderer.protocol === 'file:') {
        const relative = path.relative(path.dirname(fileURLToPath(renderer)), fileURLToPath(url))
        allowed = relative !== '..' && !relative.startsWith('..' + path.sep) && !path.isAbsolute(relative)
      } else if (['http:', 'https:', 'ws:', 'wss:'].includes(url.protocol) && !url.username && !url.password) {
        const origin = url.origin.replace(/^ws/, 'http')
        allowed = [renderer.origin, getBackendOrigin()].includes(origin)
      }
      callback({ cancel: details.webContentsId !== contents.id || !allowed })
    } catch { callback({ cancel: true }) }
  })
  session.webRequest.onBeforeSendHeaders((details, callback) => {
    const headers = { ...details.requestHeaders }
    // Never forward a host token across a redirect or from a plugin subframe.
    for (const key of Object.keys(headers)) {
      if (key.toLowerCase() === 'x-stella-desktop-token') delete headers[key]
    }
    try {
      const token = getBackendToken?.()
      if (token && new URL(details.url).origin === getBackendOrigin() &&
          details.webContentsId === contents.id && details.frame === contents.mainFrame &&
          isTrustedRendererUrl(details.frame.url, rendererUrl)) {
        headers['X-Stella-Desktop-Token'] = token
      }
    } catch { /* Missing/destroyed frames receive no host credential. */ }
    callback({ requestHeaders: headers })
  })
}
