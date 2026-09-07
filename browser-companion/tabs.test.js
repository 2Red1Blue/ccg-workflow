import {test} from 'node:test'
import assert from 'node:assert/strict'
import {reconcile, validTask, recordNavigation, recoverOwnership} from './tabs.js'

const id = 'a'.repeat(32)
const task = {id,url:`http://127.0.0.1:12345/#ccg-task=${id}`}
function fixture() {
  const opened=[], closed=[], tabs=new Map()
  const chrome={
    windows:{getAll:async()=>[{id:7,focused:true}]},
    tabs:{create:async options=>{opened.push(options);tabs.set(1,{id:1,url:options.url});return{id:1}},
      query:async()=>[...tabs.values()],
      get:async id=>{if(!tabs.has(id))throw Error('closed');return tabs.get(id)},
      remove:async id=>{closed.push(id);tabs.delete(id)}}
  }
  return {chrome,opened,closed,tabs}
}
test('opens inactive in an existing window and closes only after completion grace',async()=>{
  const f=fixture(), state={}
  await reconcile(f.chrome,state,[task],0)
  assert.equal(f.opened[0].active,false)
  assert.equal(f.opened[0].windowId,7)
  await reconcile(f.chrome,state,[task],5000)
  assert.equal(f.opened.length,1)
  await reconcile(f.chrome,state,[],6000)
  assert.equal(f.closed.length,0)
  await reconcile(f.chrome,state,[],9000)
  assert.deepEqual(f.closed,[1])
})
test('navigation away and a pending navigation both preserve user tabs',async()=>{
  for(const update of [{url:'https://example.org'},{pendingUrl:'https://example.org'}]) {
    const f=fixture(), state={}
    await reconcile(f.chrome,state,[task],0)
    Object.assign(f.tabs.get(1),update)
    await reconcile(f.chrome,state,[],1)
    await reconcile(f.chrome,state,[],4000)
    assert.deepEqual(f.closed,[])
  }
})
test('no normal window means no new window or tab',async()=>{
  const f=fixture();f.chrome.windows.getAll=async()=>[]
  await reconcile(f.chrome,{},[task],0)
  assert.equal(f.opened.length,0)
})
test('rejects nonloopback credentials and mismatched task identity',()=>{
  assert.ok(validTask(task))
  for(const url of ['https://example.org','http://127.0.0.1:12345/?secret=x','http://user:pw@127.0.0.1:12345/',task.url+'x'])assert.equal(validTask({id,url}),false)
})
test('navigating away then back permanently releases auto-close ownership',async()=>{
  const f=fixture(),state={}
  await reconcile(f.chrome,state,[task],0)
  recordNavigation(state,1,{url:'https://example.org'},{})
  recordNavigation(state,1,{url:task.url},{})
  await reconcile(f.chrome,state,[],1)
  await reconcile(f.chrome,state,[],4000)
  assert.deepEqual(f.closed,[])
})
test('restart recovers one known task tab and does not duplicate it',async()=>{
  const f=fixture(),state={[id]:{tabId:99,url:task.url}}
  f.chrome.tabs.query=async()=>[{id:1,url:task.url}]
  await recoverOwnership(f.chrome,state)
  assert.equal(state[id].tabId,1)
  await reconcile(f.chrome,state,[task],0)
  assert.equal(f.opened.length,0)
})
test('failed ownership save after create recovers the reserved tab without duplication',async()=>{
  const f=fixture();let persisted={},writes=0
  const save=async state=>{if(++writes===2)throw Error('disk');persisted=structuredClone(state)}
  await assert.rejects(reconcile(f.chrome,{},[task],0,save))
  assert.equal(persisted[id].creating,true)
  await reconcile(f.chrome,persisted,[task],1000)
  assert.equal(f.opened.length,1)
  assert.equal(persisted[id].tabId,1)
})
test('recovery retains a known owned ID if the user duplicated its URL',async()=>{
  const f=fixture(),state={[id]:{tabId:1,url:task.url}}
  f.chrome.tabs.query=async()=>[{id:1,url:task.url},{id:2,url:task.url}]
  await recoverOwnership(f.chrome,state)
  assert.equal(state[id].tabId,1)
  assert.equal(state[id].detached,undefined)
})
