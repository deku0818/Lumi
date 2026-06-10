// Desktop 多语言（i18n）。当前支持中/英，未来可加日/韩/俄等：
// 只需在 LANGS 增项、给 DICT 补该语言的词条即可。
// 用法：const { t, lang, setLang } = useI18n(); t('composer.send')
import { createContext, createElement, useContext, useEffect, useState, type ReactNode } from 'react'

export type Lang = 'zh' | 'en'

// 语言清单（顺序即菜单展示顺序）；label 用各语言自称，便于用户辨认
export const LANGS: { code: Lang; label: string }[] = [
  { code: 'zh', label: '中文' },
  { code: 'en', label: 'English' },
]

const KEY = 'lumi-lang'

type Dict = Record<string, string>

const ZH: Dict = {
  'sidebar.newChat': '新对话',
  'sidebar.recent': '最近',
  'sidebar.sessionActions': '会话操作',
  'sidebar.pin': '置顶',
  'sidebar.unpin': '取消置顶',
  'sidebar.rename': '重命名',
  'sidebar.delete': '删除',
  'sidebar.untitled': '新对话',
  'sidebar.disconnected': '未连接',
  'sidebar.processing': '处理中',
  'sidebar.needsYou': '等待你处理',
  'menu.settings': '设置',
  'menu.language': '语言',
  'composer.reply': '回复 Lumi…',
  'composer.empty': '有什么可以帮你的？',
  'composer.send': '发送',
  'composer.stop': '停止',
  'composer.attach': '添加图片',
  'composer.removeImage': '移除图片',
  'model.default': '默认模型',
  'model.switch': '切换模型',
  'model.noModels': '（无模型）',
  'common.thinking': '正在思考…',
  'common.copy': '复制',
  'common.copied': '已复制',
  'common.cancel': '取消',
  'common.delete': '删除',
  'common.save': '保存',
  'common.add': '添加',
  'common.close': '关闭',
  'common.truncated': '…（已截断）',
  'confirm.deleteTitle': '删除对话',
  'confirm.deleteMessage': '确定删除「{name}」？此操作不可恢复。',
  'approval.title': '需要你的许可',
  'approval.boundary': '超出工作区：',
  'approval.reject': '拒绝',
  'approval.allow': '允许执行',
  'approval.memoryNote': '「始终允许 / 本次会话自动编辑」等记忆选项将在后续版本支持。',
  'clarify.title': 'Lumi 想和你确认一下',
  'clarify.customPlaceholder': '或自定义输入…',
  'clarify.submit': '提交',
  'plan.title': '计划审批',
  'plan.empty': '（无计划内容）',
  'plan.reject': '拒绝 — 继续修改',
  'plan.approve': '批准 — 开始实施',
  'settings.title': '设置',
  'settings.general': '通用',
  'settings.models': '模型',
  'settings.preferences': '偏好',
  'settings.appearance': '外观',
  'settings.theme.system': '跟随系统',
  'settings.theme.light': '浅色',
  'settings.theme.dark': '深色',
  'settings.language': '语言',
  'settings.notifications': '通知',
  'settings.respDone': '回复完成通知',
  'settings.respDoneHint': '当 Lumi 完成一次回复时通知你，适合长时间任务。',
  'notify.responseDone': 'Lumi 回复已完成',
  'notify.enabled': '通知已开启',
  'providers.title': '模型提供商',
  'providers.none': '还没有配置提供商',
  'providers.addTitle': '添加提供商',
  'providers.editTitle': '编辑提供商',
  'providers.name': '提供商名称',
  'providers.namePlaceholder': '例如 OpenAI',
  'providers.baseUrl': 'Base URL',
  'providers.baseUrlPlaceholder': 'https://api.openai.com/v1',
  'providers.apiKey': 'API Key',
  'providers.models': '模型',
  'providers.modelPlaceholder': '模型名称',
  'providers.addModel': '添加模型',
  'providers.removeModel': '移除模型',
  'providers.test': '测试',
  'providers.testing': '测试中…',
  'providers.ok': '正常',
  'providers.costHint': '测试会向该模型发送一次真实请求，可能产生少量费用。',
  'providers.requestFailed': '请求失败',
  'providers.inUse': '使用中',
  'providers.switchHint': '点击切换为当前模型',
  'providers.edit': '编辑',
}

const EN: Dict = {
  'sidebar.newChat': 'New chat',
  'sidebar.recent': 'Recents',
  'sidebar.sessionActions': 'Session actions',
  'sidebar.pin': 'Pin',
  'sidebar.unpin': 'Unpin',
  'sidebar.rename': 'Rename',
  'sidebar.delete': 'Delete',
  'sidebar.untitled': 'New chat',
  'sidebar.disconnected': 'Disconnected',
  'sidebar.processing': 'Processing',
  'sidebar.needsYou': 'Waiting for you',
  'menu.settings': 'Settings',
  'menu.language': 'Language',
  'composer.reply': 'Reply to Lumi…',
  'composer.empty': 'How can I help?',
  'composer.send': 'Send',
  'composer.stop': 'Stop',
  'composer.attach': 'Add image',
  'composer.removeImage': 'Remove image',
  'model.default': 'Default model',
  'model.switch': 'Switch model',
  'model.noModels': '(no models)',
  'common.thinking': 'Thinking…',
  'common.copy': 'Copy',
  'common.copied': 'Copied',
  'common.cancel': 'Cancel',
  'common.delete': 'Delete',
  'common.save': 'Save',
  'common.add': 'Add',
  'common.close': 'Close',
  'common.truncated': '…(truncated)',
  'confirm.deleteTitle': 'Delete chat',
  'confirm.deleteMessage': 'Delete “{name}”? This can’t be undone.',
  'approval.title': 'Permission needed',
  'approval.boundary': 'Outside workspace: ',
  'approval.reject': 'Reject',
  'approval.allow': 'Allow',
  'approval.memoryNote': 'Memory options like “Always allow / Auto-edit this session” are coming in a later version.',
  'clarify.title': 'Lumi wants to check with you',
  'clarify.customPlaceholder': 'Or type your own…',
  'clarify.submit': 'Submit',
  'plan.title': 'Plan review',
  'plan.empty': '(No plan content)',
  'plan.reject': 'Reject — keep editing',
  'plan.approve': 'Approve — start',
  'settings.title': 'Settings',
  'settings.general': 'General',
  'settings.models': 'Models',
  'settings.preferences': 'Preferences',
  'settings.appearance': 'Appearance',
  'settings.theme.system': 'System',
  'settings.theme.light': 'Light',
  'settings.theme.dark': 'Dark',
  'settings.language': 'Language',
  'settings.notifications': 'Notifications',
  'settings.respDone': 'Response completions',
  'settings.respDoneHint': 'Get notified when Lumi finishes a response. Useful for long-running tasks.',
  'notify.responseDone': 'Lumi finished a response',
  'notify.enabled': 'Notifications enabled',
  'providers.title': 'Model providers',
  'providers.none': 'No providers yet',
  'providers.addTitle': 'Add provider',
  'providers.editTitle': 'Edit provider',
  'providers.name': 'Provider name',
  'providers.namePlaceholder': 'e.g. OpenAI',
  'providers.baseUrl': 'Base URL',
  'providers.baseUrlPlaceholder': 'https://api.openai.com/v1',
  'providers.apiKey': 'API Key',
  'providers.models': 'Models',
  'providers.modelPlaceholder': 'Model name',
  'providers.addModel': 'Add model',
  'providers.removeModel': 'Remove model',
  'providers.test': 'Test',
  'providers.testing': 'Testing…',
  'providers.ok': 'OK',
  'providers.costHint': 'Testing sends one real request to this model and may incur a small cost.',
  'providers.requestFailed': 'Request failed',
  'providers.inUse': 'In use',
  'providers.switchHint': 'Click to use this model',
  'providers.edit': 'Edit',
}

const DICT: Record<Lang, Dict> = { zh: ZH, en: EN }

function initialLang(): Lang {
  const saved = localStorage.getItem(KEY)
  if (saved === 'zh' || saved === 'en') return saved
  // 首次：跟随系统语言，中文环境用中文，否则英文
  return navigator.language?.toLowerCase().startsWith('zh') ? 'zh' : 'en'
}

export type Translate = (key: string, vars?: Record<string, string | number>) => string

type I18nCtx = { lang: Lang; setLang: (l: Lang) => void; t: Translate }
const Ctx = createContext<I18nCtx | null>(null)

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLang] = useState<Lang>(initialLang)

  useEffect(() => {
    localStorage.setItem(KEY, lang)
    document.documentElement.lang = lang
  }, [lang])

  const t: Translate = (key, vars) => {
    let s = DICT[lang][key] ?? DICT.en[key] ?? key
    if (vars) for (const k in vars) s = s.replaceAll(`{${k}}`, String(vars[k]))
    return s
  }

  return createElement(Ctx.Provider, { value: { lang, setLang, t } }, children)
}

export function useI18n(): I18nCtx {
  const ctx = useContext(Ctx)
  if (!ctx) throw new Error('useI18n 必须在 I18nProvider 内使用')
  return ctx
}
