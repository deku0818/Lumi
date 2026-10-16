const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const { sshHosts, sshArgs } = require('../electron/ssh-config.cjs')

test('SSH host discovery follows Include, quotes, cycles and explicit aliases', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'lumi-config-test-'))
  try {
    fs.mkdirSync(path.join(root, 'hosts'))
    fs.writeFileSync(path.join(root, 'config'), 'Include "hosts/*.conf"\nHost aws-node-01 alias-two *.internal !excluded\n  HostName example.com\n')
    fs.writeFileSync(path.join(root, 'hosts/first.conf'), 'Include "' + path.join(root, 'config') + '"\nHost "gpu-lab" # comment\n')
    assert.deepEqual(sshHosts(path.join(root, 'config')), [{ alias:'alias-two' }, {alias:'aws-node-01'}, {alias:'gpu-lab'}])
  } finally { fs.rmSync(root, {recursive:true,force:true}) }
})
test('remote SSH arguments preserve alias and reject option injection', () => {
  assert.throws(()=>sshArgs({host:'-oProxyCommand=bad'}))
  assert.throws(()=>sshArgs({host:'host\nbad'}))
  assert.throws(()=>sshArgs({host:'safe',port:70000}))
  const args=sshArgs({host:'safe',user:'demo',port:2222,identityFile:'/tmp/key with spaces'})
  assert.ok(args.includes('/tmp/key with spaces'))
  assert.ok(args.includes('2222'))
})

test('IPC registry ignores unsupported records and keeps SSH key bytes out of JSON', async () => {
  const {setupSsh}=require('../electron/ssh-ipc.cjs')
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),'lumi-ipc-test-'))
  const handlers=new Map()
  const registry=path.join(directory,'backends.json')
  fs.writeFileSync(registry,JSON.stringify({remotes:[{id:'incomplete',name:'Incomplete',unsupported:'untouched'}]}))
  const api=setupSsh({
    app:{isPackaged:false,getPath:()=>directory},
    ipcMain:{handle:(name,handler)=>handlers.set(name,handler)},
    BrowserWindow:{getAllWindows:()=>[]},dialog:{},
    projectRoot:path.resolve(__dirname,'../..'),environmentPath:async()=>process.env.PATH,
  })
  try {
    const listed=await handlers.get('lumi:backends:list')()
    assert.deepEqual(listed.remotes,[])
    assert.equal(fs.readFileSync(registry,'utf8').includes('untouched'),true,'Reading must not rewrite stored data')
    const saved=await handlers.get('lumi:backends:save')({},{
      id:'aws-host',name:'AWS',host:'aws-node-01',port:2222,enabled:false,authMethod:'paste',
    },{privateKey:'PRIVATE-TEST-FIXTURE',keepKey:true})
    const key=saved.remotes[0].identityFile
    assert.equal(fs.statSync(key).mode&0o777,0o600)
    assert.equal(fs.readFileSync(registry,'utf8').includes('PRIVATE-TEST-FIXTURE'),false)
    const restored=await handlers.get('lumi:backends:save')({},{id:saved.remotes[0].id,name:'AWS',host:'aws-node-01',enabled:false})
    assert.equal(restored.remotes[0].port,null)
    assert.equal(restored.remotes[0].identityFile,'')
    await handlers.get('lumi:ssh:clean-keys')({},saved.remotes[0].id)
    assert.equal(fs.existsSync(key),false)
    await assert.rejects(handlers.get('lumi:backends:remove')({},'../escape'))
    assert.equal(fs.statSync(registry).mode&0o777,0o600)
  } finally { await api.close();fs.rmSync(directory,{recursive:true,force:true}) }
})

test('connection attempts share one opening and cancellation during initialization prevents SSH', async () => {
  const {SshManager}=require('../electron/ssh-manager.cjs')
  let initResolve, opens=0
  const initialized=new Promise(resolve=>{initResolve=resolve})
  const manager=new SshManager({version:'test',emit:()=>{},ask:async()=>null})
  manager.init=()=>initialized
  manager.open=async()=>{opens++;return {kind:'ssh',backend:'test'}}
  const first=manager.connect({id:'test',host:'safe'})
  assert.equal(first,manager.connect({id:'test',host:'safe'}))
  const cancel=manager.disconnect('test')
  initResolve()
  await assert.rejects(first,/取消/)
  await cancel
  assert.equal(opens,0)
  await manager.close()
})

test('private SSH temporary storage reclaims only dead owned sessions', async () => {
  const {SshManager}=require('../electron/ssh-manager.cjs')
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'lumi-private-test-'))
  const temporary=path.join(root,'ssh-temporary')
  fs.mkdirSync(temporary)
  const dead=path.join(temporary,'lumi-ssh-dead')
  const live=path.join(temporary,'lumi-ssh-live')
  fs.mkdirSync(dead);fs.mkdirSync(live)
  fs.writeFileSync(path.join(dead,'owner.pid'),'99999999')
  fs.writeFileSync(path.join(dead,'private-key'),'fixture')
  fs.writeFileSync(path.join(live,'owner.pid'),String(process.pid))
  const manager=new SshManager({directory:root,version:'test',emit:()=>{},ask:async()=>null})
  try {
    await manager.init()
    assert.equal(fs.existsSync(dead),false)
    assert.equal(fs.existsSync(live),true)
    assert.equal(fs.statSync(manager.privateDirectory).mode&0o777,0o700)
  } finally {await manager.close();fs.rmSync(root,{recursive:true,force:true})}
})

test('authentication broker validates its token and reuses a supplied passphrase', { timeout: 5000 }, async () => {
  const net = require('node:net')
  const { SshManager } = require('../electron/ssh-manager.cjs')
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'lumi-auth-test-'))
  const questions = []
  const manager = new SshManager({ directory: root, emit: () => {}, ask: async question => {
    questions.push(question)
    return 'fixture-passphrase'
  } })
  manager.sessions.set('remote', { config: { id: 'remote', host: 'fixture' }, children: new Set(), cancelled: false })
  function ask(token) {
    return new Promise((resolve, reject) => {
      const socket = net.createConnection(manager.brokerAddress)
      let response = ''
      socket.on('connect', () => socket.write(JSON.stringify({ token, machine: 'remote', prompt: 'Enter passphrase for key:' }) + '\n'))
      socket.on('data', chunk => { response += chunk })
      socket.on('end', () => resolve(JSON.parse(response)))
      socket.on('error', reject)
    })
  }
  try {
    await manager.init()
    assert.deepEqual(await ask('incorrect-token'), { error: 'Authentication cancelled' })
    assert.deepEqual(await ask(manager.brokerToken), { answer: 'fixture-passphrase' })
    assert.deepEqual(await ask(manager.brokerToken), { answer: 'fixture-passphrase' })
    assert.equal(questions.length, 1)
    assert.equal(questions[0].kind, 'secret')
  } finally {
    await manager.close()
    fs.rmSync(root, { recursive: true, force: true })
  }
})

test('askpass executable preload returns a broker answer without executing the prompt', async () => {
  const net=require('node:net')
  const directory=fs.mkdtempSync('/tmp/lumi-askpass-test-')
  const address=path.join(directory,'ipc.sock')
  const execFile=require('node:util').promisify(require('node:child_process').execFile)
  const electron=require('electron')
  let received
  const server=net.createServer(socket=>{
    let text=''
    socket.on('data',chunk=>{
      text+=chunk
      if(!text.includes('\n'))return
      received=JSON.parse(text).prompt
      socket.end(JSON.stringify({answer:'fixture-answer'})+'\n')
    })
  })
  await new Promise(resolve=>server.listen(address,resolve))
  try {
    const result=await execFile(electron,['SSH fixture prompt & literal'],{
      env:{...process.env,ELECTRON_RUN_AS_NODE:'1',SSH_ASKPASS:electron,
        NODE_OPTIONS:'--require '+JSON.stringify(path.resolve(__dirname,'../electron/ssh-askpass-preload.cjs')),
        LUMI_ASKPASS_SOCKET:address,LUMI_ASKPASS_TOKEN:'fixture-token',LUMI_ASKPASS_MACHINE:'fixture'},
      timeout:15000,
    })
    assert.equal(result.stdout.trim(),'fixture-answer')
    assert.ok(received.includes('SSH fixture prompt & literal'))
  } finally {await new Promise(resolve=>server.close(resolve));fs.rmSync(directory,{recursive:true,force:true})}
})

test('remote preparation asks only for installation and rejects incompatible native runtimes', async () => {
  const {SshManager}=require('../electron/ssh-manager.cjs')
  const states=[],questions=[]
  const manager=new SshManager({version:'0.3.0',emit:state=>states.push(state),ask:async question=>{questions.push(question);return null}})
  const session={config:{id:'test',host:'safe'},children:new Set()}
  manager.bootstrap=async()=>({runtime:null,installed:false,data_dir:'/fixture/lumi'})
  await assert.rejects(manager.open(session),/取消准备/)
  assert.deepEqual(states.map(state=>state.phase),['connecting','checking'])
  assert.equal(questions[0].installed,false)
  assert.equal(questions[0].dataDir,'/fixture/lumi')
  manager.bootstrap=async()=>({runtime:{transport:'ssh-stdio',protocol:1,version:'0.2.138'},installed:true,data_dir:'/fixture/lumi'})
  await assert.rejects(manager.open(session),/版本不一致/)
  assert.equal(questions.length,1)
  assert.equal(states.at(-1).preparation,'reuse')
  await manager.close()
})

test('disconnect resolves a pending preparation question before awaiting the opening', async () => {
  const {SshManager}=require('../electron/ssh-manager.cjs')
  let answer,asked
  const waiting=new Promise(resolve=>{asked=resolve})
  const states=[]
  const manager=new SshManager({version:'test',
    emit:state=>{states.push(state);if(state.phase==='offline')answer?.(null)},
    ask:()=>new Promise(resolve=>{answer=resolve;asked()}),
  })
  manager.init=async()=>{}
  manager.bootstrap=async()=>({runtime:null,installed:false,data_dir:'/fixture/lumi'})
  const opening=manager.connect({id:'cancel-test',host:'safe'})
  const failed=assert.rejects(opening,/取消准备/)
  await waiting
  await manager.disconnect('cancel-test')
  await failed
  assert.equal(states.at(-1).phase,'offline','Cancelled openings must not emit a stale failure')
  await manager.close()
})

test('renderer navigation cancels an opening SSH channel without starting a late remote session', async () => {
  const {EventEmitter}=require('node:events')
  const {SshManager}=require('../electron/ssh-manager.cjs')
  const {setupSsh}=require('../electron/ssh-ipc.cjs')
  const directory=fs.mkdtempSync('/tmp/lumi-native-ipc-'),handlers=new Map(),sent=[]
  const originals={connect:SshManager.prototype.connect,send:SshManager.prototype.send,request:SshManager.prototype.request}
  let connected
  const waiting=new Promise(resolve=>connected=resolve)
  SshManager.prototype.connect=()=>waiting
  SshManager.prototype.send=async (machine,message)=>sent.push({machine,message})
  SshManager.prototype.request=async ()=>{throw Error('Cancelled opening must not create a session')}
  fs.writeFileSync(path.join(directory,'backends.json'),JSON.stringify({remotes:[{id:'aws',host:'aws-node-01'}]}))
  const api=setupSsh({app:{isPackaged:false,getPath:()=>directory},ipcMain:{handle:(name,handler)=>handlers.set(name,handler)},BrowserWindow:{getAllWindows:()=>[]},dialog:{},projectRoot:path.resolve(__dirname,'../..'),environmentPath:async()=>process.env.PATH})
  const owner=new EventEmitter()
  owner.mainFrame={frameTreeNodeId:7};owner.isDestroyed=()=>false
  const event={sender:owner,senderFrame:owner.mainFrame}
  try {
    const opening=handlers.get('lumi:remote:open')(event,'aws','test-channel',{})
    const cancelled=assert.rejects(opening,/cancelled/)
    owner.emit('did-start-navigation',{isMainFrame:true,isSameDocument:false})
    connected({kind:'ssh',backend:'aws'})
    await cancelled
    assert.deepEqual(sent,[{machine:'aws',message:{op:'close',channel:'test-channel'}}])
    assert.equal(owner.listenerCount('did-start-navigation'),1)
    await assert.rejects(handlers.get('lumi:remote:open')({sender:owner,senderFrame:{frameTreeNodeId:8}},'aws','child-channel',{}),/application frame/)
  } finally {
    await api.close();Object.assign(SshManager.prototype,originals);fs.rmSync(directory,{recursive:true,force:true})
  }
})
