import { createRoot } from 'react-dom/client'
import App from './App'
import { I18nProvider } from './i18n'
import { TooltipProvider } from './components/ui/tooltip'
import { ToastHost } from './components/Toast'
import './index.css'

// 挂载前同步应用主题，避免亮色模式首帧闪暗背景。pref: system/light/dark。
const saved = localStorage.getItem('lumi-theme')
const systemLight = window.matchMedia?.('(prefers-color-scheme: light)').matches
const light = saved === 'light' || ((saved == null || saved === 'system') && systemLight)
document.documentElement.classList.toggle('light', !!light)
document.documentElement.classList.toggle('dark', !light)

// 不用 StrictMode：避免 dev 下 effect 双调用导致 WS 双连、sidecar 端双 bridge。
createRoot(document.getElementById('root')!).render(
  <I18nProvider>
    <TooltipProvider delayDuration={200}>
      <App />
      <ToastHost />
    </TooltipProvider>
  </I18nProvider>,
)
