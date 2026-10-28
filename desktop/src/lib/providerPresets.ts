import type { ProviderProtocol } from '../types'

export const protocolOptions: Array<[ProviderProtocol, string]> = [
  ['openai', 'OpenAI Chat Completions'], ['responses', 'OpenAI Responses'],
  ['anthropic', 'Anthropic Messages'], ['gemini', 'Gemini'],
]

export const compatibilityOptions: Record<ProviderProtocol, Array<[string, string]>> = {
  openai: [['standard', 'OpenAI'], ['qwen', 'Qwen / DashScope'], ['deepseek', 'DeepSeek'],
    ['kimi', 'Kimi / Moonshot'], ['glm', 'GLM'], ['mimo', 'MiMo'], ['minimax', 'MiniMax'],
    ['openrouter', 'OpenRouter'], ['local', 'OpenAI compatible']],
  responses: [['standard', 'OpenAI'], ['openrouter', 'OpenRouter']],
  anthropic: [['claude', 'Anthropic / Claude'], ['minimax', 'MiniMax'], ['compatible', 'Anthropic compatible']],
  gemini: [['gemini', 'Google Gemini']],
}

export const providerPresets: Array<{
  id: string; name: string; mark: string; protocol: ProviderProtocol
  compatibility: string; base_url: string; model: string
}> = [
  { id: 'qwen', name: 'Qwen / 阿里云百炼', mark: 'Q', protocol: 'openai', compatibility: 'qwen', base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1', model: 'qwen3.7-plus' },
  { id: 'deepseek', name: 'DeepSeek', mark: 'D', protocol: 'openai', compatibility: 'deepseek', base_url: 'https://api.deepseek.com/v1', model: 'deepseek-v4-pro' },
  { id: 'kimi', name: 'Kimi', mark: 'K', protocol: 'openai', compatibility: 'kimi', base_url: 'https://api.moonshot.cn/v1', model: 'kimi-k2.6' },
  { id: 'claude', name: 'Anthropic', mark: 'A', protocol: 'anthropic', compatibility: 'claude', base_url: 'https://api.anthropic.com', model: 'claude-sonnet-4-6' },
  { id: 'openai', name: 'OpenAI', mark: 'O', protocol: 'openai', compatibility: 'standard', base_url: 'https://api.openai.com/v1', model: 'gpt-5.2' },
  { id: 'minimax', name: 'MiniMax', mark: 'M', protocol: 'anthropic', compatibility: 'minimax', base_url: 'https://api.minimaxi.com/anthropic', model: 'MiniMax-M2.7' },
  { id: 'openrouter', name: 'OpenRouter', mark: '↗', protocol: 'openai', compatibility: 'openrouter', base_url: 'https://openrouter.ai/api/v1', model: 'anthropic/claude-sonnet-4.6' },
  { id: 'gemini', name: 'Google Gemini', mark: 'G', protocol: 'gemini', compatibility: 'gemini', base_url: 'https://generativelanguage.googleapis.com', model: 'gemini-3-pro-preview' },
  { id: 'custom', name: '', mark: '+', protocol: 'openai', compatibility: 'standard', base_url: '', model: '' },
]
