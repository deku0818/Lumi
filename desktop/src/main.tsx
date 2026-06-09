import { createRoot } from 'react-dom/client'
import App from './App'
import './index.css'

// 挂载前同步应用主题，避免亮色模式首帧闪暗背景。
const saved = localStorage.getItem('lumi-theme')
const light =
  saved === 'light' ||
  (saved == null && window.matchMedia?.('(prefers-color-scheme: light)').matches)
document.documentElement.classList.toggle('light', !!light)

// 不用 StrictMode：避免 dev 下 effect 双调用导致 WS 双连、sidecar 端双 bridge。
createRoot(document.getElementById('root')!).render(<App />)
