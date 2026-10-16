const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')

function words(line) {
  const result = []
  let word = ''
  let quote = ''
  let escaped = false
  for (const char of line) {
    if (escaped) {
      word += char
      escaped = false
      continue
    }
    if (char === '\\' && quote !== "'") {
      escaped = true
      continue
    }
    if (quote) {
      if (char === quote) quote = ''
      else word += char
      continue
    }
    if (char === '"' || char === "'") {
      quote = char
      continue
    }
    if (char === '#') break
    if (/\s/.test(char)) {
      if (word) result.push(word)
      word = ''
      continue
    }
    word += char
  }
  if (word) result.push(word)
  return result
}

function sshHosts(config = path.join(os.homedir(), '.ssh/config')) {
  const seen = new Set(), hosts = new Set()
  const base = path.dirname(config)
  function read(file) {
    if (!fs.existsSync(file)) return
    const real = fs.realpathSync(file)
    if (seen.has(real)) return
    seen.add(real)
    for (const line of fs.readFileSync(real, 'utf8').split(/\r?\n/)) {
      const [raw, ...args] = words(line.replace(/^(\s*\w+)\s*=/, '$1 '))
      const key = raw?.toLowerCase()
      if (key === 'host') {
        for (const alias of args) if (!/[*!?]/.test(alias)) hosts.add(alias)
      }
      if (key === 'include') {
        for (let pattern of args) {
          if (pattern.startsWith('~/')) pattern = path.join(os.homedir(), pattern.slice(2))
          if (!path.isAbsolute(pattern)) pattern = path.join(base, pattern)
          for (const included of fs.globSync(pattern).sort()) read(included)
        }
      }
    }
  }
  read(config)
  return [...hosts].sort().map(alias => ({ alias }))
}

function sshArgs(config) {
  if (!config.host || /[\x00-\x20]/.test(config.host) || config.host.startsWith('-')) {
    throw new Error('请填写有效的 SSH 主机。')
  }
  const args = []
  if (config.configFile) args.push('-F', config.configFile)
  if (config.authMethod === 'password') args.push('-o', 'PreferredAuthentications=password,keyboard-interactive', '-o', 'PubkeyAuthentication=no')
  if (config.user) args.push('-l', config.user)
  if (config.port) {
    if (!Number.isInteger(Number(config.port)) || config.port < 1 || config.port > 65535) throw new Error('SSH 端口无效。')
    args.push('-p', String(config.port))
  }
  if (config.identityFile) args.push('-i', config.identityFile.replace(/^~(?=[\\/])/, os.homedir()), '-o', 'IdentitiesOnly=yes')
  args.push('-o', 'StrictHostKeyChecking=ask', '-o', 'ControlMaster=no', '-o', 'ControlPath=none', '-o', 'ControlPersist=no')
  args.push('-o', 'ConnectTimeout=15', '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3')
  return args
}

module.exports = { sshHosts, sshArgs, words }
