// Windows OpenSSH needs an executable, not a shell/batch wrapper.
// Run this before Node interprets the SSH prompt as an entry point.
const { spawnSync } = require('node:child_process')
const path = require('node:path')
if (path.resolve(process.execPath).toLowerCase() === path.resolve(process.env.SSH_ASKPASS || '.').toLowerCase()) {
  const env = { ...process.env, ELECTRON_RUN_AS_NODE: '1' }
  delete env.NODE_OPTIONS
  const result = spawnSync(process.execPath, [path.join(__dirname, 'ssh-askpass.cjs'), process.argv[1] || ''], {
    env, encoding: 'utf8', windowsHide: true, timeout: 310000,
  })
  if (result.status !== 0) process.exit(1)
  process.stdout.write(result.stdout)
  process.exit(0)
}
