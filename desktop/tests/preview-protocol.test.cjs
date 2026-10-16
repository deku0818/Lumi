const fs = require('node:fs')
const path = require('node:path')
const { registerRemoteFilePolicy } = require('../electron/remote-files.cjs')
const RESULT_PREFIX = 'PREVIEW_RESULT:'

// A real Chromium PDF document, with a valid xref table.
function pdfBytes() {
  const text = 'BT /F1 16 Tf 30 100 Td (Lumi PDF preview) Tj ET'
  const objects = ['<< /Type /Catalog /Pages 2 0 R >>', '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>', `<< /Length ${text.length} >>\nstream\n${text}\nendstream`]
  let pdf = '%PDF-1.4\n'
  const offsets = [0]
  for (const [index, object] of objects.entries()) {
    offsets.push(Buffer.byteLength(pdf))
    pdf += `${index + 1} 0 obj\n${object}\nendobj\n`
  }
  const xref = Buffer.byteLength(pdf)
  pdf += `xref\n0 6\n0000000000 65535 f \n${offsets.slice(1).map(offset => String(offset).padStart(10, '0') + ' 00000 n \n').join('')}trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF`
  return Buffer.from(pdf)
}

async function electronFixture() {
  const { app, BrowserWindow, protocol, session: electronSession } = require('electron')
  app.setPath('userData', process.argv[2])
  protocol.registerSchemesAsPrivileged([{ scheme: 'lumi-remote', privileges: {
    standard: true, secure: true, supportFetchAPI: true, stream: true, corsEnabled: true,
  } }])
  await app.whenReady()
  const win = new BrowserWindow({ show: false, webPreferences: { sandbox: true, contextIsolation: true } })
  const requests = []
  const served = []
  protocol.handle('lumi-remote', request => {
    served.push(request.url)
    if (request.url.endsWith('.pdf')) return new Response(pdfBytes(), { headers: { 'Content-Type': 'application/pdf', 'Access-Control-Allow-Origin': '*' } })
    return new Response('<script>fetch("lumi-remote://file/fixture/secret.txt").catch(()=>{})</script>', {
      headers: { 'Content-Type': 'text/html', 'Access-Control-Allow-Origin': '*' },
    })
  })
  const session = { defaultSession: { webRequest: { onBeforeRequest(filter, handler) {
    electronSession.defaultSession.webRequest.onBeforeRequest(filter, (details, callback) => {
      handler(details, result => { requests.push({ url: details.url, blocked: !!result.cancel }); callback(result) })
    })
  } } } }
  registerRemoteFilePolicy(session.defaultSession, BrowserWindow)
  const { outputFiles } = await require('esbuild').build({
    stdin: { contents: `import React from 'react'; import {createRoot} from 'react-dom/client';
      import {PreviewBody} from './src/components/Artifacts';
      const gw={fileUrl:path=>'lumi-remote://file/fixture'+path};
      createRoot(document.getElementById('root')).render(<>
        <PreviewBody remote gw={gw} file={{path:'/test.pdf',name:'PDF',kind:'pdf'}} />
        <PreviewBody remote gw={gw} file={{path:'/test.html',name:'HTML',kind:'html'}} />
      </>);`, resolveDir: path.join(__dirname, '..'), loader: 'tsx' },
    bundle: true, write: false, format: 'iife', loader: { '.css': 'empty' },
    define: { 'process.env.NODE_ENV': '"production"' },
    plugins: [{ name: 'preview-test', setup(build) {
      build.onLoad({ filter: /i18n\.ts$/ }, () => ({ contents: 'export const useI18n=()=>({t:key=>key})', loader: 'ts' }))
    } }],
  })
  const entry = path.join(process.argv[2], 'index.html')
  fs.writeFileSync(entry, '<div id="root"></div>')
  await win.loadFile(entry)
  await win.webContents.executeJavaScript(outputFiles[0].text)
  let frames = []
  let pdfViewer = false
  for (let i = 0; i < 100; i++) {
    frames = await win.webContents.executeJavaScript(`Array.from(document.querySelectorAll('iframe'),f=>({title:f.title,src:f.src,sandbox:f.getAttribute('sandbox')}))`)
    const pdfSource = frames.find(frame => frame.title === 'PDF')?.src
    const pdf = win.webContents.mainFrame.frames.find(frame => frame.url === pdfSource)
    const viewer = pdf?.frames.find(frame => frame.url.startsWith('chrome-extension://'))
    pdfViewer = viewer ? await viewer.executeJavaScript("!!document.querySelector('pdf-viewer')") : false
    if (frames.length === 2 && pdfViewer && requests.some(request => request.url.endsWith('secret.txt'))) break
    await new Promise(resolve => setTimeout(resolve, 50))
  }
  console.log(RESULT_PREFIX + JSON.stringify({ frames, pdfViewer, requests, served }))
  win.destroy()
  app.quit()
}

if (process.versions.electron) {
  electronFixture().catch(error => { console.error(error); require('electron').app.exit(1) })
} else {
  const test = require('node:test'), assert = require('node:assert/strict')
  test('remote PDF opens in Chromium while sandboxed artifact scripts cannot read remote files', { timeout: 20000 }, async () => {
    const directory = fs.mkdtempSync(path.join(require('node:os').tmpdir(), 'lumi-preview-test-'))
    const env = { ...process.env }
    delete env.ELECTRON_RUN_AS_NODE
    try {
      const { stdout } = await require('node:util').promisify(require('node:child_process').execFile)(
        require('electron'), [__filename, directory], { env, timeout: 15000, maxBuffer: 1024 * 1024 },
      )
      const result = JSON.parse(stdout.split('\n').find(line => line.startsWith(RESULT_PREFIX)).slice(RESULT_PREFIX.length))
      const pdf = result.frames.find(frame => frame.title === 'PDF')
      const html = result.frames.find(frame => frame.title === 'HTML')
      assert.ok(pdf?.src.startsWith('blob:'))
      assert.equal(result.pdfViewer, true, 'Chromium must create its PDF viewer')
      assert.equal(pdf.sandbox, null)
      assert.equal(html.sandbox, 'allow-scripts')
      assert.ok(result.served.some(url => url.endsWith('test.pdf')))
      assert.ok(result.requests.some(request => request.url.endsWith('secret.txt') && request.blocked))
      assert.equal(result.served.some(url => url.endsWith('secret.txt')), false)
    } finally { fs.rmSync(directory, { recursive: true, force: true }) }
  })
}
