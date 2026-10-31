const fs = require('node:fs/promises')
const path = require('node:path')
const RESULT = 'PREVIEW_INTERACTION:'

async function browserFixture() {
  const { app, BrowserWindow, protocol } = require('electron')
  const directory = process.argv[2], root = path.resolve(__dirname, '..')
  app.setPath('userData', directory)
  protocol.registerSchemesAsPrivileged([{ scheme: 'lumi-file', privileges: { standard: true, secure: true, supportFetchAPI: true } }])
  await app.whenReady()
  const dimensions = { portrait: [100, 4000], wide: [4000, 100], square: [4000, 4000] }
  protocol.handle('lumi-file', request => {
    const name = path.basename(new URL(request.url).pathname).split('.')[0]
    if (name === 'page') return new Response('<!doctype html><body style="margin:0;height:100vh">Interactive HTML</body>', { headers: { 'Content-Type': 'text/html' } })
    const [width, height] = dimensions[name]
    return new Response(`<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}"><rect width="100%" height="100%" fill="gold"/></svg>`, { headers: { 'Content-Type': 'image/svg+xml' } })
  })
  const { build } = await import('vite')
  const tailwind = (await import('@tailwindcss/vite')).default
  await build({ configFile: false, root, plugins: [tailwind()], logLevel: 'silent', build: {
    outDir: path.join(directory, 'css'), emptyOutDir: false,
    rollupOptions: { input: path.join(root, 'src/index.css'), output: { assetFileNames: 'lumi.css' } },
  } })
  const { outputFiles } = await require('esbuild').build({
    stdin: { resolveDir: root, loader: 'tsx', contents: `
      import {useState} from 'react'; import {createRoot} from 'react-dom/client';
      import {PreviewWindow} from './src/components/PreviewWindow';
      import {WorkspaceWindow} from './src/components/WorkspaceWindow';
      import {useResizableWidth} from './src/components/ResizeHandle';
      window.lumi={};
      const files=['page','portrait','wide','square'].map(name=>({path:'/'+name+(name==='page'?'.html':'.svg'),kind:name==='page'?'file':'image'}));
      const noop=()=>{};
      function Fixture(){const [mode,setMode]=useState('page');const [open,setOpen]=useState(true);const width=useResizableWidth('preview-test-width',440,360,920);
        Object.assign(window,{__fixture:{mode,choose:setMode,show:setOpen}});
        return <WorkspaceWindow {...width} open={open} expanded={false}>
          <PreviewWindow files={files} activePath={'/'+mode+(mode==='page'?'.html':'.svg')} expanded={false} remote={false}
            onSelect={path=>setMode(path.slice(1).split('.')[0])} onCloseTab={noop} onClose={noop} onToggleExpanded={noop}/>
        </WorkspaceWindow>}
      createRoot(document.getElementById('root')).render(<Fixture/>);` },
    bundle: true, write: false, format: 'iife', jsx: 'automatic', loader: { '.css': 'empty' },
    define: { 'process.env.NODE_ENV': '"production"' },
    plugins: [{ name: 'test-translation', setup(build) {
      build.onLoad({ filter: /i18n\.ts$/ }, () => ({ contents: 'export const useI18n=()=>({t:key=>key})', loader: 'ts' }))
    } }],
  })
  const entry = path.join(directory, 'index.html'), css = await fs.readFile(path.join(directory, 'css/lumi.css'), 'utf8')
  await fs.writeFile(entry, `<meta charset="utf-8"><style>${css}</style><div id="root"></div>`)
  const win = new BrowserWindow({ width: 1000, height: 700, show: false, webPreferences: { contextIsolation: true, sandbox: true } })
  const run = code => win.webContents.executeJavaScript(code)
  const pause = ms => new Promise(resolve => setTimeout(resolve, ms))
  const wait = async code => { for (let i = 0; i < 100; i++) { if (await run(code)) return; await pause(30) } throw Error('Timed out: ' + code) }
  await win.loadFile(entry)
  await run(outputFiles[0].text)
  await wait('!!document.querySelector("iframe")')
  await pause(250)
  await run('window.__fixture.show(false)')
  const hidden = await run('getComputedStyle(document.querySelector(".preview-document[aria-hidden=false]")).visibility==="hidden" && getComputedStyle(document.querySelector("iframe")).visibility==="hidden"')
  await run('window.__fixture.show(true)')
  const start = await run('(()=>{const r=document.querySelector(".workspace-window-resize > div").getBoundingClientRect();return{x:r.x+5,y:r.y+200}})()')
  win.webContents.debugger.attach('1.3')
  const input = event => win.webContents.debugger.sendCommand('Input.dispatchMouseEvent', event)
  await input({ type: 'mouseMoved', x: start.x, y: start.y })
  await input({ type: 'mousePressed', x: start.x, y: start.y, button: 'left', clickCount: 1 })
  const during = await run('document.body.classList.contains("resizing-col") && getComputedStyle(document.querySelector("iframe")).pointerEvents==="none"')
  await input({ type: 'mouseMoved', x: start.x + 100, y: start.y, button: 'left', buttons: 1 })
  await pause(80)
  await input({ type: 'mouseReleased', x: start.x + 100, y: start.y, button: 'left', clickCount: 1 })
  await pause(250)
  const drag = await run('({width:document.querySelector(".workspace-window").clientWidth,resizing:document.body.classList.contains("resizing-col"),pointer:getComputedStyle(document.querySelector("iframe")).pointerEvents})')
  await input({ type: 'mouseMoved', x: 100, y: start.y })
  await pause(80)
  drag.afterMove = await run('document.querySelector(".workspace-window").clientWidth')
  win.webContents.debugger.detach()
  const images = []
  for (const [name, [naturalWidth]] of Object.entries(dimensions)) {
    await run(`window.__fixture.choose(${JSON.stringify(name)})`)
    await wait(`document.querySelector('.preview-document[aria-hidden=false] img')?.complete && document.querySelector('.preview-document[aria-hidden=false] img').naturalWidth===${naturalWidth}`)
    images.push(await run(`(()=>{const img=document.querySelector('.preview-document[aria-hidden=false] img'),v=document.querySelector('.preview-viewport'),r=img.getBoundingClientRect();return{name:${JSON.stringify(name)},width:r.width,height:r.height,viewportWidth:v.clientWidth,viewportHeight:v.clientHeight}})()`))
  }
  console.log(RESULT + JSON.stringify({ hidden, during, drag, images }))
  win.destroy()
  app.quit()
}

if (process.versions.electron) {
  browserFixture().catch(error => { console.error(error); require('electron').app.exit(1) })
} else {
  const test = require('node:test'), assert = require('node:assert/strict')
  test('shared preview window hides its content, releases iframe drags and fits image aspect ratios', { timeout: 30000 }, async () => {
    const directory = await fs.mkdtemp(path.join(require('node:os').tmpdir(), 'lumi-preview-interaction-'))
    const env = { ...process.env }; delete env.ELECTRON_RUN_AS_NODE
    try {
      const { stdout } = await require('node:util').promisify(require('node:child_process').execFile)(require('electron'), [__filename, directory], { env, timeout: 25000, maxBuffer: 1024 * 1024 })
      const { hidden, during, drag, images } = JSON.parse(stdout.split('\n').find(line => line.startsWith(RESULT)).slice(RESULT.length))
      assert.equal(hidden, true, 'The active document must inherit the closed window visibility')
      assert.equal(during, true, 'The parent must own drag events over iframe content')
      assert.equal(drag.width, 364, 'Dragging right must narrow to the configured minimum plus its edge gap')
      assert.equal(drag.resizing, false, 'Mouse release must clean up drag state')
      assert.equal(drag.pointer, 'auto', 'The HTML document must become interactive again')
      assert.equal(drag.afterMove, drag.width, 'Moving after release must not resize the pane')
      for (const image of images) {
        assert.ok(image.width > 0 && image.width <= image.viewportWidth, image.name + ' width')
        assert.ok(image.height > 0 && image.height <= image.viewportHeight, image.name + ' height')
      }
    } finally { await fs.rm(directory, { recursive: true, force: true }) }
  })
}
