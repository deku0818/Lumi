import { defineConfig } from 'vite'
import { fileURLToPath } from 'node:url'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// 顶层 protocol/ 是语言中立的协议事实来源，desktop 之外，需放开 fs 访问 + alias
const protocolDir = fileURLToPath(new URL('../protocol', import.meta.url))

// base './' 让打包后 electron 以相对路径加载 dist 资源
export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: './',
  resolve: { alias: { '@protocol': protocolDir } },
  server: { port: 5173, strictPort: true, host: '127.0.0.1', fs: { allow: ['..'] } },
  build: { outDir: 'dist' },
})
