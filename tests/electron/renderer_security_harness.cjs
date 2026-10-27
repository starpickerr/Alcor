const { app, BrowserWindow } = require('electron')
const http = require('node:http')
const fs = require('node:fs')
const path = require('node:path')
const { pathToFileURL } = require('node:url')

const work = process.env.STELLA_SECURITY_WORK
app.setPath('userData', path.join(work, 'electron-state'))
const preload = path.join(work, 'preload.cjs')
fs.writeFileSync(preload, "const {contextBridge,ipcRenderer}=require('electron');contextBridge.exposeInMainWorld('probe',{invoke:()=>ipcRenderer.invoke('security-probe')});")
const servers = []
let otherPortRequests = 0
const serve = async (fn) => {
  const server = http.createServer(fn)
  servers.push(server)
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  return `http://127.0.0.1:${server.address().port}`
}
const result = (report, code = 0) => {
  console.log('SECURITY_RESULT ' + JSON.stringify(report))
  for (const server of servers) server.close()
  app.exit(code)
}

app.whenReady().then(async () => {
  try {
    const { createTrustedIpc, hardenRenderer, isTrustedRendererUrl, localPathAction, decodeIconDataUrl } = require(process.env.STELLA_RENDERER_SECURITY_JS)
    const origin = await serve((_req, res) => {
      res.setHeader('Access-Control-Allow-Origin', '*')
      res.end('<!doctype html><title>Isolated test renderer</title>')
    })
    const backend = await serve((req, res) => {
      res.setHeader('Access-Control-Allow-Origin', '*')
      if (req.url === '/plugin-content') {
        res.setHeader('Content-Type', 'text/html')
        res.end('<script>fetch("/private").then(r=>parent.postMessage({childStatus:r.status},"*"))</script>')
        return
      }
      if (req.headers['x-stella-desktop-token'] !== 'synthetic-desktop-token') {
        res.statusCode = 403
        res.end('parent-only-backend')
        return
      }
      res.end('test-backend-ok')
    })
    const otherPort = await serve((_req, res) => {
      otherPortRequests++
      res.setHeader('Access-Control-Allow-Origin', '*')
      res.end('must-not-be-readable')
    })
    const rendererFile = path.join(work, 'renderer 页面.html')
    fs.writeFileSync(rendererFile, '<!doctype html><title>Packaged renderer test</title>')
    const rendererUrl = process.env.STELLA_RENDERER_MODE === 'file' ? pathToFileURL(rendererFile).href : origin + '/index.html'
    const main = new BrowserWindow({ show: false, webPreferences: {
      preload, sandbox: true, nodeIntegration: false, contextIsolation: true,
      partition: 'security-integration'
    } })
    const ipc = createTrustedIpc(() => main, () => rendererUrl)
    ipc.handle('security-probe', () => 'owner-ui-ok')
    const external = []
    hardenRenderer(main, rendererUrl, () => backend, url => external.push(url), () => 'synthetic-desktop-token')
    await main.loadURL(rendererUrl)
    const normalIpc = await main.webContents.executeJavaScript('window.probe.invoke()')
    const nodeAbsent = await main.webContents.executeJavaScript('typeof require === "undefined" && typeof process === "undefined"')
    const normalBackend = await main.webContents.executeJavaScript(`fetch(${JSON.stringify(backend + '/read')}).then(r=>r.text())`)
    const childStatus = await main.webContents.executeJavaScript(`new Promise(resolve=>{
      const child=document.createElement('iframe');child.sandbox='allow-scripts';
      addEventListener('message',event=>{if(event.source===child.contentWindow)resolve(event.data.childStatus)},{once:true});
      child.src=${JSON.stringify(backend + '/plugin-content')};document.body.appendChild(child);
    })`)
    const otherPortDenied = await main.webContents.executeJavaScript(`fetch(${JSON.stringify(otherPort + '/private')}).then(()=>false,()=>true)`)
    await main.webContents.executeJavaScript(`void location.assign(${JSON.stringify(otherPort + '/navigate')})`)
    await new Promise(resolve => setTimeout(resolve, 100))
    const navigationBlocked = main.webContents.getURL() === rendererUrl
    await main.webContents.executeJavaScript('void window.open("file:///tmp/stella-test.command")')
    const unsafePopupDenied = external.length === 0
    const foreign = new BrowserWindow({ show: false, webPreferences: { preload, sandbox: true, contextIsolation: true, nodeIntegration: false } })
    await foreign.loadURL(rendererUrl)
    const foreignWindowDenied = await foreign.webContents.executeJavaScript('window.probe.invoke().then(()=>false,()=>true)')
    await main.loadURL('data:text/html,Changed%20untrusted%20page')
    const changedPageDenied = await main.webContents.executeJavaScript('window.probe.invoke().then(()=>false,()=>true)')
    const foreignUrl = process.env.STELLA_RENDERER_MODE === 'file' ? origin + '/index.html' : rendererUrl.replace('127.0.0.1', '127.0.0.1.evil.invalid')
    const urlBoundary = [rendererUrl + '#/models', rendererUrl + '.evil', 'file:///tmp/foreign.html', foreignUrl].map(value => isTrustedRendererUrl(value, rendererUrl))
    const script = path.join(work, 'unsafe.command')
    const document = path.join(work, 'notes.txt')
    const appBundle = path.join(work, 'unsafe.app')
    fs.writeFileSync(script, 'echo do-not-execute')
    fs.writeFileSync(document, 'normal owner document')
    fs.mkdirSync(appBundle)
    const localPaths = [script, document, appBundle, work].map(localPathAction)
    const iconRequestsRejected = decodeIconDataUrl(otherPort + '/private-icon') === null &&
      decodeIconDataUrl('file:///tmp/private-icon.png') === null &&
      decodeIconDataUrl('data:image/svg+xml;base64,PHN2Zz4=') === null
    const localIconAccepted = decodeIconDataUrl('data:image/png;base64,iVBORw0KGgo=')?.length === 8
    main.destroy()
    foreign.destroy()
    result({ normalIpc, foreignWindowDenied, changedPageDenied, normalBackend, otherPortDenied, otherPortRequests,
      navigationBlocked, unsafePopupDenied, nodeAbsent, urlBoundary, localPaths, iconRequestsRejected, localIconAccepted, childStatus })
  } catch (error) {
    result({ error: String(error.stack || error) }, 1)
  }
})
