const fs = require('node:fs/promises')
const os = require('node:os')
const path = require('node:path')
const crypto = require('node:crypto')
const net = require('node:net')

async function prepareSshStorage(manager, quote) {
  const temporaryRoot = manager.directory ? path.join(manager.directory, 'ssh-temporary') : os.tmpdir()
  if (manager.directory) {
    await fs.mkdir(temporaryRoot, { recursive: true, mode: 0o700 })
    await fs.chmod(temporaryRoot, 0o700)
    for (const name of await fs.readdir(temporaryRoot)) {
      if (!name.startsWith('lumi-ssh-')) continue
      const folder = path.join(temporaryRoot, name)
      if (!(await fs.lstat(folder)).isDirectory()) continue
      try {
        const pid = Number(await fs.readFile(path.join(folder, 'owner.pid'), 'utf8'))
        if (!Number.isSafeInteger(pid) || pid < 1) continue
        try { process.kill(pid, 0) } catch (error) {
          if (error.code === 'ESRCH') await fs.rm(folder, { recursive: true, force: true })
        }
      } catch { }
    }
  }
  manager.privateDirectory = await fs.mkdtemp(path.join(temporaryRoot, 'lumi-ssh-'))
  await fs.chmod(manager.privateDirectory, 0o700)
  await fs.writeFile(path.join(manager.privateDirectory, 'owner.pid'), String(process.pid), { mode: 0o600 })
  manager.askpass = path.join(manager.privateDirectory, 'askpass.sh')
  const helper = path.join(__dirname, 'ssh-askpass.cjs').replace('app.asar' + path.sep, 'app.asar.unpacked' + path.sep)
  const launcher = '#!/bin/sh\nELECTRON_RUN_AS_NODE=1 exec ' + quote(process.execPath) + ' ' + quote(helper) + ' "$@"\n'
  if (process.platform !== 'win32') await fs.writeFile(manager.askpass, launcher, { mode: 0o700 })
}

async function startAuthenticationBroker(manager, configHost) {
  manager.brokerDirectory = process.platform === 'win32' ? null : await fs.mkdtemp('/tmp/lumi-askpass-')
  if (manager.brokerDirectory) await fs.chmod(manager.brokerDirectory, 0o700)
  manager.brokerAddress = process.platform === 'win32' ? '\\\\.\\pipe\\lumi-askpass-' + crypto.randomUUID() : path.join(manager.brokerDirectory, 'ipc.sock')
  manager.server = net.createServer(socket => {
    let body = ''
    socket.on('data', chunk => {
      body += chunk
      if (body.length > 16384) { socket.destroy(); return }
      const end = body.indexOf('\n')
      if (end < 0) return
      socket.removeAllListeners('data')
      void (async () => {
        try {
          const { token, machine: id, prompt } = JSON.parse(body.slice(0, end))
          if (token !== manager.brokerToken) throw Error('Invalid authentication broker token')
          const session = manager.sessions.get(id)
          if (!session || session.cancelled) throw Error('SSH connection was cancelled')
          const trust = /Are you sure you want to continue connecting/i.test(prompt)
          if (/REMOTE HOST IDENTIFICATION HAS CHANGED/i.test(prompt)) throw new Error('主机身份发生变化，请核对 SSH known_hosts。')
          let answer
          if (!trust && session.passphrase && /passphrase/i.test(prompt)) answer = session.passphrase
          else if (!trust && session.password && /password/i.test(prompt)) answer = session.password
          else {
            let displayPrompt = prompt
            if (trust) {
              const diagnostics = (session.authenticationDetails || '') + '\n' + prompt
              const fingerprints = diagnostics.match(/SHA256:[A-Za-z0-9+/=]+/g)
              if (!fingerprints?.length) throw new Error('无法取得 SSH 主机指纹，请先在终端核验此主机。')
              displayPrompt = configHost(session.config) + '\n' + fingerprints.at(-1) + '\n\n' + prompt
            }
            answer = await manager.ask({ machine: id, kind: trust ? 'trust' : 'secret', prompt: displayPrompt, ...(trust ? { host: configHost(session.config), fingerprint: displayPrompt.split('\n')[1] } : {}) })
          }
          if (answer == null || session.cancelled) throw new Error('已取消 SSH 认证。')
          if (!trust && /passphrase/i.test(prompt)) session.passphrase = answer
          if (!trust && /password/i.test(prompt)) session.password = answer
          socket.end(JSON.stringify({ answer }) + '\n')
        } catch { socket.end(JSON.stringify({ error: 'Authentication cancelled' }) + '\n') }
      })()
    })
    socket.on('error', () => { })
  })
  await new Promise((resolve, reject) => { manager.server.once('error', reject); manager.server.listen(manager.brokerAddress, resolve) })
  if (manager.brokerDirectory) await fs.chmod(manager.brokerAddress, 0o600)
  manager.server.unref()
}

module.exports = { prepareSshStorage, startAuthenticationBroker }
