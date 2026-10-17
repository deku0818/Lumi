const { spawn, execFile } = require('node:child_process')
const crypto = require('node:crypto')
const fs = require('node:fs/promises')
const path = require('node:path')
const { StringDecoder } = require('node:string_decoder')
const { promisify } = require('node:util')
const { RemoteWire } = require('./remote-wire.cjs')
const { sshArgs, sshHosts } = require('./ssh-config.cjs')
const { prepareSshStorage, startAuthenticationBroker } = require('./ssh-auth.cjs')
const execFileAsync = promisify(execFile)
function configHost(config) {
  return config.host + (config.port ? ':' + config.port : '')
}

function quote(value) {
  return "'" + String(value).replace(/'/g, "'\\''") + "'"
}

class SshManager {
  constructor({ directory, version, bootstrapPath, emit, ask }) {
    Object.assign(this, { directory, version, bootstrapPath, emit, ask })
    this.sessions = new Map()
    this.pendingConnections = new Map()
    this.recoveries = new Map()
    this.recoveryGeneration = new Map()
    this.statuses = new Map()
    this.brokerToken = crypto.randomBytes(32).toString('hex')
  }

  state(id, phase, message = '', details = {}) {
    const state = { id, phase, message, ...details }
    this.statuses.set(id, state)
    this.emit(state)
  }

  async init() {
    if (this.initializing) return this.initializing
    this.initializing = (async () => {
      await prepareSshStorage(this, quote)
      await startAuthenticationBroker(this, configHost)
    })()
    return this.initializing
  }

  async hosts() {
    await execFileAsync('ssh', ['-V'])
    return sshHosts()
  }

  async resolve(config) {
    const { stdout } = await execFileAsync('ssh', ['-G', ...sshArgs(config), '--', config.host], { timeout: 20000, maxBuffer: 2 * 1024 * 1024 })
    const values = {}
    for (const line of stdout.split('\n')) {
      const split = line.indexOf(' ')
      const key = line.slice(0, split), value = line.slice(split + 1)
      if (!values[key]) values[key] = value
    }
    return { hostname: values.hostname, user: values.user, port: values.port, identityFile: values.identityfile, jump: values.proxyjump === 'none' ? '' : values.proxyjump }
  }

  environment(id) {
    return {
      ...process.env,
      ...(process.platform === 'win32' ? {
        SSH_ASKPASS: process.execPath, ELECTRON_RUN_AS_NODE: '1',
        NODE_OPTIONS: '--require ' + JSON.stringify(path.join(__dirname, 'ssh-askpass-preload.cjs').replace('app.asar' + path.sep, 'app.asar.unpacked' + path.sep)),
      } : { SSH_ASKPASS: this.askpass }),
      SSH_ASKPASS_REQUIRE: 'force',
      DISPLAY: process.env.DISPLAY || ':0',
      LUMI_ASKPASS_SOCKET: this.brokerAddress,
      LUMI_ASKPASS_TOKEN: this.brokerToken, LUMI_ASKPASS_MACHINE: id,
    }
  }

  command(session, command, input = '', onLine = () => {}) {
    return new Promise((resolve, reject) => {
      if (session.cancelled) { reject(new Error('连接已取消。')); return }
      const child = spawn('ssh', [...sshArgs(session.config), '--', session.config.host, command], { env: this.environment(session.config.id), stdio: ['pipe', 'pipe', 'pipe'] })
      session.children.add(child)
      let stdout = '', stderr = ''
      let pendingLine = ''
      const decoder = new StringDecoder('utf8')
      session.authenticationDetails = ''
      child.stdout.on('data', chunk => {
        const value = decoder.write(chunk)
        stdout += value
        pendingLine += value
        let newline
        while ((newline = pendingLine.indexOf('\n')) >= 0) {
          onLine(pendingLine.slice(0, newline))
          pendingLine = pendingLine.slice(newline + 1)
        }
        if (stdout.length > 4 * 1024 * 1024) child.kill()
      })
      child.stderr.on('data', chunk => { stderr = (stderr + chunk).slice(-8000); session.authenticationDetails = stderr })
      child.on('error', reject)
      child.on('close', code => {
        session.children.delete(child)
        if (code === 0 && !session.cancelled) resolve(stdout)
        else reject(new Error(stderr.trim() || 'SSH 命令失败或已取消。'))
      })
      child.stdin.on('error', () => {})
      child.stdin.end(input)
    })
  }

  async bootstrap(session, request) {
    const source = await fs.readFile(this.bootstrapPath, 'utf8')
    const payload = Buffer.from(JSON.stringify(request)).toString('base64')
    const script = `${source}
import base64
try:
 print("LUMI_RESULT:"+json.dumps(bootstrap(json.loads(base64.b64decode("${payload}")))))
except Exception as e:
 print("LUMI_ERROR:"+str(e))
`
    const stdout = await this.command(session, 'python3 -u -', script, line => {
      if (!line.startsWith('LUMI_STATUS:') || session.cancelled) return
      const status = JSON.parse(line.slice(12))
      this.state(session.config.id, status.phase, '', { dataDir: status.dataDir })
    })
    const error = /^LUMI_ERROR:([\s\S]*)/m.exec(stdout)
    if (error) throw new Error(error[1].trim())
    const result = stdout.split('\n').find(line => line.startsWith('LUMI_RESULT:'))
    if (!result) throw new Error('远端未返回有效的 Lumi 启动结果。')
    return JSON.parse(result.slice(12))
  }

  connect(config, credentials = {}) {
    const pending = this.pendingConnections.get(config.id)
    if (pending) return pending
    const opening = this.connectSession(config, credentials).finally(() => this.pendingConnections.delete(config.id))
    this.pendingConnections.set(config.id, opening)
    return opening
  }

  async connectSession(config, credentials = {}) {
    const generation = this.recoveryGeneration.get(config.id) || 0
    await this.init()
    if ((this.recoveryGeneration.get(config.id) || 0) !== generation) throw new Error('连接已取消。')
    if (this.closed) throw new Error('SSH 管理器已关闭。')
    const current = this.sessions.get(config.id)
    if (current?.opening) return current.opening
    if (current?.connection && !current.wire?.closed) return current.connection
    const retained = current ? { privateKey: current.privateKey, passphrase: current.passphrase, password: current.password } : {}
    if (current) await this.disconnect(config.id, false)
    const session = { config: { ...config }, children: new Set(), cancelled: false, ...retained, ...credentials }
    this.sessions.set(config.id, session)
    session.opening = this.open(session).catch(async error => {
      const previous = this.statuses.get(config.id)
      if (!session.cancelled) this.state(config.id, 'error', error.message, { stage: previous?.phase, dataDir: previous?.dataDir, preparation: previous?.preparation, code: error.code })
      await this.disconnect(config.id, false)
      throw error
    }).finally(() => { session.opening = null })
    return session.opening
  }

  async open(session) {
    const { config } = session, id = config.id
    if (!config.host) throw new Error('请填写 SSH 主机信息。')
    if (config.authMethod === 'paste' && !session.privateKey) throw new Error('请重新提供本次连接使用的私钥。')
    if (session.privateKey) {
      const file = path.join(this.privateDirectory, 'key-' + crypto.randomUUID())
      await fs.writeFile(file, session.privateKey.trim() + '\n', { mode: 0o600 })
      session.temporaryKey = file; session.config.identityFile = file
    }
    this.state(id, 'connecting', '连接 SSH 主机')
    const probe = await this.bootstrap(session, { action: 'probe', version: this.version, data_dir: config.dataDir })
    this.state(id, 'checking', '检查远端 Lumi', { dataDir: probe.data_dir })
    if (!probe.installed && !probe.runtime && !probe.managed) {
      const answer = await this.ask({ machine: id, kind: 'prepare', installed: false, dataDir: probe.data_dir,
        prompt: '将在当前 SSH 用户下安装 Lumi 常驻进程。数据目录：' + probe.data_dir })
      if (answer !== 'yes') throw new Error('已取消准备远端 Lumi。')
    }
    let instance = probe.runtime
    if (!instance || instance.version !== this.version) {
      let phase = 'installing'
      if (instance) phase = 'updating'
      else if (probe.installed) phase = 'starting'
      this.state(id, phase, '', { dataDir: probe.data_dir })
      instance = await this.bootstrap(session, { action: 'connect', version: this.version, data_dir: config.dataDir })
    } else {
      this.state(id, 'starting', '复用远端 Lumi', { dataDir: probe.data_dir, preparation: 'reuse' })
    }
    if (session.cancelled) throw new Error('连接已取消。')
    if (instance.transport !== 'ssh-stdio' || instance.protocol !== 1 || instance.version !== this.version) throw Error('远端 Lumi 与当前桌面版本不一致。')
    this.state(id, 'attaching', '建立 SSH 数据通道', { dataDir: probe.data_dir })
    const command = quote(instance.executable) + ' -m lumi.remote.attach --data-dir ' + quote(instance.data_dir)
    const child = spawn('ssh', [...sshArgs(session.config), '--', config.host, command], { env: this.environment(id), stdio: ['pipe', 'pipe', 'pipe'] })
    session.children.add(child)
    child.stderr.on('data', chunk => { session.authenticationDetails = ((session.authenticationDetails || '') + chunk).slice(-8000) })
    child.on('close', () => session.children.delete(child))
    session.wire = new RemoteWire(child, {
      onFrame: frame => this.onFrame?.(id, frame),
      onClose: error => {
        const wasReady = Boolean(session.connection)
        session.connection = null
        this.onDisconnect?.(id, error.message)
        if (!session.cancelled) {
          this.state(id, 'offline', error.message)
          if (wasReady) this.recover(config, { privateKey: session.privateKey, passphrase: session.passphrase, password: session.password })
        }
      },
    })
    const hello = await session.wire.ready
    if (session.cancelled) throw Error('连接已取消。')
    if (hello.transport !== 'ssh-stdio' || hello.protocol !== 1 || hello.version !== this.version) throw Error('远端数据通道握手不匹配。')
    session.connection = { kind: 'ssh', backend: id }
    this.state(id, 'ready', '远端 Lumi 已就绪', { dataDir: probe.data_dir })
    return session.connection
  }

  async request(config, message) {
    await this.connect(config)
    return this.sessions.get(config.id).wire.request(message)
  }

  send(id, message) {
    const wire = this.sessions.get(id)?.wire
    if (!wire || wire.closed) return Promise.reject(Error('SSH 数据通道未连接。'))
    return wire.send(message)
  }

  closeChannel(id, channel) {
    const wire = this.sessions.get(id)?.wire
    // Closing a viewer must never reconnect a disconnected machine.
    if (!wire || wire.closed) return Promise.resolve()
    return wire.request({ op: 'close', channel })
  }

  recover(config, credentials, retry = 0, generation = this.recoveryGeneration.get(config.id) || 0) {
    if (this.closed || (this.recoveryGeneration.get(config.id) || 0) !== generation || retry >= 5) return
    const timer = setTimeout(() => {
      this.recoveries.delete(config.id)
      if ((this.recoveryGeneration.get(config.id) || 0) !== generation) return
      void this.connect(config, credentials).catch(() => this.recover(config, credentials, retry + 1, generation))
    }, Math.min(8000, 1000 * 2 ** retry))
    this.recoveries.set(config.id, timer)
  }

  async disconnect(id, notify = true) {
    if (notify) {
      clearTimeout(this.recoveries.get(id))
      this.recoveries.delete(id)
      this.recoveryGeneration.set(id, (this.recoveryGeneration.get(id) || 0) + 1)
      this.state(id, 'offline', '已断开；远端服务继续运行。')
    }
    const session = this.sessions.get(id)
    const opening = this.pendingConnections.get(id)
    if (!session) {
      if (notify && opening) await opening.catch(() => {})
      return
    }
    session.cancelled = true
    session.wire?.fail(new Error('SSH 连接已断开。'))
    await Promise.all([...session.children].map(child => new Promise(resolve => {
      if (child.exitCode !== null) { resolve(); return }
      const timer = setTimeout(() => { child.kill('SIGKILL'); resolve() }, 2000)
      child.once('close', () => { clearTimeout(timer); resolve() })
      child.kill()
    })))
    if (session.temporaryKey) await fs.rm(session.temporaryKey, { force: true })
    if (this.sessions.get(id) === session) this.sessions.delete(id)
    if (notify && opening) await opening.catch(() => {})
  }

  async close() {
    this.closed = true
    if (this.initializing) await this.initializing.catch(() => {})
    for (const timer of this.recoveries.values()) clearTimeout(timer)
    this.recoveries.clear()
    await Promise.all([...this.sessions.keys()].map(id => this.disconnect(id)))
    this.server?.close()
    if (this.brokerDirectory) await fs.rm(this.brokerDirectory, { recursive: true, force: true })
    if (this.privateDirectory) await fs.rm(this.privateDirectory, { recursive: true, force: true })
  }
}
module.exports = { SshManager, quote }
