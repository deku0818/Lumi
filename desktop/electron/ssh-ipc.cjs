const fs = require('node:fs')
const fsp = require('node:fs/promises')
const path = require('node:path')
const crypto = require('node:crypto')
const { execFile } = require('node:child_process')
const { promisify } = require('node:util')
const { SshManager } = require('./ssh-manager.cjs')
const { sshArgs } = require('./ssh-config.cjs')
const { createRemoteFileHandler } = require('./remote-files.cjs')
const execFileAsync = promisify(execFile)
const REMOTE_FIELDS = ['name', 'host', 'user', 'port', 'identityFile', 'dataDir', 'enabled', 'authMethod']

function validateBackendId(id) {
  if (!/^[A-Za-z0-9_-]{1,64}$/.test(id)) throw new Error('SSH 主机标识无效。')
}

function isManagedKey(file, id) {
  return file === id + '.pem' || file.startsWith(id + '-')
}

function setupSsh({ app, ipcMain, BrowserWindow, dialog, projectRoot, environmentPath }) {
  const registry = path.join(app.getPath('userData'), 'backends.json')
  const keyDirectory = path.join(app.getPath('userData'), 'ssh-keys')
  const pending = new Map()
  let bundle
  const broadcast = (event, value) => {
    for (const win of BrowserWindow.getAllWindows()) if (!win.webContents.isDestroyed()) win.webContents.send(event, value)
  }
  function ask(question) {
    const id = crypto.randomUUID()
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { pending.delete(id); broadcast('lumi:ssh:question-closed', id); reject(new Error('SSH 认证等待超时。')) }, 5 * 60 * 1000)
      pending.set(id, { question: { ...question, id }, answer: value => { clearTimeout(timer); pending.delete(id); broadcast('lumi:ssh:question-closed', id); resolve(value) } })
      broadcast('lumi:ssh:question', { ...question, id })
    })
  }
  const manager = new SshManager({
    directory: app.getPath('userData'),
    version: app.isPackaged ? app.getVersion() : fs.readFileSync(path.join(projectRoot, 'pyproject.toml'), 'utf8').match(/^version = "([^"]+)"/m)[1],
    bootstrapPath: app.isPackaged ? path.join(process.resourcesPath, 'remote-backend/bootstrap.py') : path.join(projectRoot, 'lumi/remote/bootstrap.py'),
    emit: state => {
      if (state.phase === 'offline' || state.phase === 'error') for (const item of pending.values()) if (item.question.machine === state.id) item.answer(null)
      broadcast('lumi:ssh:status', state)
    },
    ask,
    wheel: () => {
      if (!bundle) bundle = (async () => {
        const directory = app.isPackaged ? path.join(process.resourcesPath, 'remote-backend') : path.join(projectRoot, 'dist/remote')
        if (!app.isPackaged) await execFileAsync('uv', ['build', '--wheel', '--out-dir', directory], { cwd: projectRoot, env: { ...process.env, PATH: await environmentPath() }, timeout: 120000 })
        const file = (await fsp.readdir(directory)).find(name => name.startsWith('lumi_harness-' + manager.version + '-') && name.endsWith('.whl'))
        if (!file) throw new Error('桌面安装包缺少远端 Lumi wheel。')
        return path.join(directory, file)
      })().catch(error => { bundle = null; throw error })
      return bundle
    },
  })
  function write(value) {
    fs.mkdirSync(path.dirname(registry), { recursive: true })
    fs.writeFileSync(registry + '.tmp', JSON.stringify(value, null, 2), { mode: 0o600 })
    fs.chmodSync(registry + '.tmp', 0o600)
    fs.renameSync(registry + '.tmp', registry)
  }
  function read() {
    if (!fs.existsSync(registry)) return { remotes: [] }
    const value = JSON.parse(fs.readFileSync(registry, 'utf8'))
    const fields = ['id', ...REMOTE_FIELDS]
    const remotes = (value.remotes || []).filter(item => item.host).map(item => {
      const entries = fields.filter(key => item[key] !== undefined).map(key => [key, item[key]])
      return Object.fromEntries(entries)
    })
    return { remotes }
  }
  ipcMain.handle('lumi:backends:list', read)
  ipcMain.handle('lumi:backends:save', async (_event, draft, credentials = {}) => {
    const value = read()
    const index = value.remotes.findIndex(item => item.id === draft.id)
    const previous = index >= 0 ? value.remotes[index] : {}
    const id = previous.id || crypto.randomBytes(6).toString('hex')
    validateBackendId(id)
    const next = { ...previous, id }
    for (const key of REMOTE_FIELDS) {
      if (draft[key] !== undefined) next[key] = draft[key]
    }
    if (draft.host !== undefined) {
      for (const key of ['user', 'identityFile', 'dataDir']) next[key] = draft[key] || ''
      next.port = draft.port || null
      next.authMethod = draft.authMethod || 'config'
    }
    if (next.host) sshArgs(next)
    if (credentials.privateKey && credentials.keepKey) {
      await fsp.mkdir(keyDirectory, { recursive: true, mode: 0o700 })
      await fsp.chmod(keyDirectory, 0o700)
      next.authMethod = 'file'
      next.identityFile = path.join(keyDirectory, id + '-' + crypto.randomUUID() + '.pem')
      await fsp.writeFile(next.identityFile, credentials.privateKey.trim() + '\n', { mode: 0o600 })
      await fsp.chmod(next.identityFile, 0o600)
    }
    if (index >= 0) value.remotes[index] = next
    else value.remotes.push(next)
    write(value)
    if (next.enabled === false || (index >= 0 && JSON.stringify(previous) !== JSON.stringify(next))) await manager.disconnect(id)
    return value
  })
  ipcMain.handle('lumi:backends:remove', async (_event, id) => {
    validateBackendId(id)
    await manager.disconnect(id)
    const value = read()
    value.remotes = value.remotes.filter(item => item.id !== id)
    write(value)
    if (fs.existsSync(keyDirectory)) {
      for (const file of await fsp.readdir(keyDirectory)) {
        if (isManagedKey(file, id)) await fsp.rm(path.join(keyDirectory, file), { force: true })
      }
    }
    return value
  })
  function configFor(id) {
    const config = read().remotes.find(item => item.id === id)
    if (!config || config.enabled === false) throw new Error('该 SSH 主机已移除或断开。')
    return config
  }
  function connection(id, credentials) { return manager.connect(configFor(id), credentials) }
  const channels = new Map()
  const mainFrame = event => {
    if (event.senderFrame?.frameTreeNodeId !== event.sender.mainFrame.frameTreeNodeId) throw Error('Remote IPC is restricted to the application frame')
  }
  const watched = new WeakSet()
  const watch = owner => {
    if (watched.has(owner)) return
    watched.add(owner)
    const release = () => {
      for (const [channel, record] of channels) if (record.owner === owner) {
        channels.delete(channel)
        void manager.send(record.machine, { op: 'close', channel }).catch(() => {})
      }
    }
    owner.on('did-start-navigation', details => { if (details.isMainFrame && !details.isSameDocument) release() })
    owner.on('render-process-gone', release)
    owner.once('destroyed', release)
  }
  const owned = (event, channel) => {
    mainFrame(event)
    const record = channels.get(channel)
    if (!record || record.owner !== event.sender) throw Error('Remote channel is closed')
    return record
  }
  manager.onFrame = (_machine, frame) => {
    const record = channels.get(frame.channel)
    if (record && !record.owner.isDestroyed()) record.owner.send('lumi:remote:event', frame)
  }
  manager.onDisconnect = (machine, message) => {
    for (const [channel, record] of channels) if (record.machine === machine) {
      channels.delete(channel)
      if (!record.owner.isDestroyed()) record.owner.send('lumi:remote:event', { type: 'close', channel, message })
    }
  }
  ipcMain.handle('lumi:remote:open', async (event, machine, channel, options) => {
    mainFrame(event)
    if (!/^[a-zA-Z0-9-]{1,64}$/.test(channel) || channels.has(channel)) throw Error('Invalid channel identifier')
    const record = { machine, owner: event.sender }
    channels.set(channel, record)
    const close = () => {
      if (channels.get(channel) !== record) return
      channels.delete(channel)
      void manager.send(machine, { op: 'close', channel }).catch(() => {})
    }
    watch(event.sender)
    try {
      const config = configFor(machine)
      await manager.connect(config)
      if (channels.get(channel) !== record || event.sender.isDestroyed()) throw Error('Remote channel was cancelled')
      return await manager.request(config, { op: 'open', channel, workspace: options.workspace || '', thread: options.thread || '' })
    } catch (error) { close(); throw error }
  })
  ipcMain.handle('lumi:remote:send', (event, channel, frame) => {
    const record = owned(event, channel)
    return manager.send(record.machine, { op: 'frame', channel, frame })
  })
  ipcMain.handle('lumi:remote:close', (event, channel) => {
    mainFrame(event)
    const record = channels.get(channel)
    if (!record || record.owner !== event.sender) return
    channels.delete(channel)
    return manager.closeChannel(record.machine, channel).catch(() => {})
  })
  ipcMain.handle('lumi:remote:upload', (event, machine, operation, value) => {
    mainFrame(event)
    if (!['begin', 'chunk', 'finish', 'cancel'].includes(operation)) throw Error('Invalid upload operation')
    const message = { op: 'upload_' + operation }
    if (operation === 'begin') message.name = value.name
    else message.upload = value.upload
    if (operation === 'chunk') {
      const bytes = Buffer.from(value.bytes)
      if (bytes.length > 1024 * 1024) throw Error('Upload chunk is too large')
      message.data = bytes.toString('base64')
    }
    return manager.request(configFor(machine), message)
  })
  ipcMain.handle('lumi:ssh:hosts', () => manager.hosts())
  ipcMain.handle('lumi:ssh:resolve', (_event, config) => manager.resolve(config))
  ipcMain.handle('lumi:ssh:connect', (_event, id, credentials) => connection(id, credentials))
  ipcMain.handle('lumi:ssh:disconnect', (_event, id) => manager.disconnect(id))
  ipcMain.handle('lumi:ssh:statuses', () => [...manager.statuses.values()])
  ipcMain.handle('lumi:ssh:questions', () => [...pending.values()].map(item => item.question))
  ipcMain.handle('lumi:ssh:answer', (_event, id, answer) => pending.get(id)?.answer(answer))
  ipcMain.handle('lumi:ssh:clean-keys', async (_event, id) => {
    validateBackendId(id)
    const current = read().remotes.find(item => item.id === id)?.identityFile
    if (!fs.existsSync(keyDirectory)) return
    for (const file of await fsp.readdir(keyDirectory)) {
      const target = path.join(keyDirectory, file)
      if (isManagedKey(file, id) && target !== current) await fsp.rm(target, { force: true })
    }
  })
  ipcMain.handle('lumi:ssh:pick-key', async event => {
    const result = await dialog.showOpenDialog(BrowserWindow.fromWebContents(event.sender), { properties: ['openFile'], title: '选择 SSH 私钥文件' })
    return result.canceled ? null : result.filePaths[0]
  })
  const file = createRemoteFileHandler(manager, configFor)
  return { connection, file, async close() { for (const item of pending.values()) item.answer(null); await manager.close() } }
}
module.exports = { setupSsh }
