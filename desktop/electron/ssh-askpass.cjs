// OpenSSH helper: credentials travel over a private Unix socket / Windows named pipe.
const net = require('node:net')
const socket = net.createConnection(process.env.LUMI_ASKPASS_SOCKET)
let response = ''
socket.on('connect', () => socket.write(JSON.stringify({
  token: process.env.LUMI_ASKPASS_TOKEN, machine: process.env.LUMI_ASKPASS_MACHINE,
  prompt: process.argv[2] || '',
}) + '\n'))
socket.on('data', chunk => response += chunk)
socket.on('end', () => {
  try {
    const value = JSON.parse(response)
    if (typeof value.answer !== 'string') process.exit(1)
    process.stdout.write(value.answer + '\n')
  } catch { process.exit(1) }
})
socket.on('error', () => process.exit(1))
