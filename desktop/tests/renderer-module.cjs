const path = require('node:path')
const vm = require('node:vm')
const { buildSync } = require('esbuild')

// Compile an exported module as a unit; tests do not depend on private function names or source layout.
function loadRenderer(file, globals = {}, external = []) {
  const { outputFiles } = buildSync({
    entryPoints: [path.join(__dirname, '..', file)],
    bundle: true, write: false, platform: 'node', format: 'cjs',
    loader: { '.css': 'empty', '.svg': 'dataurl' }, external,
  })
  const module = { exports: {} }
  vm.runInNewContext(outputFiles[0].text, {
    module, exports: module.exports, require, console,
    setTimeout, clearTimeout, queueMicrotask, Uint8Array,
    crypto: globalThis.crypto, WebSocket: globalThis.WebSocket,
    ...globals,
  })
  return module.exports
}

module.exports = { loadRenderer }
