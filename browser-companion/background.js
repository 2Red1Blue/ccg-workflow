import {reconcile, recordNavigation, recoverOwnership} from './tabs.js'

let port
let queue = Promise.resolve()
let recovered = false
function connect() {
  if (port) return
  try { port = chrome.runtime.connectNative('com.ccg.webui') }
  catch { void chrome.action.setBadgeText({text: '!'}); return }
  port.onMessage.addListener(message => {
    if (message.type !== 'snapshot' || !Array.isArray(message.tasks)) return
    queue = queue.then(async () => {
      const {owned = {}} = await chrome.storage.local.get('owned')
      if (!recovered) { await recoverOwnership(chrome, owned); recovered = true }
      await reconcile(chrome, owned, message.tasks, Date.now(), owned => chrome.storage.local.set({owned}))
      await chrome.storage.local.set({owned})
      await chrome.action.setBadgeText({text: ''})
    }).catch(() => chrome.action.setBadgeText({text: 'E'}))
  })
  port.onDisconnect.addListener(() => {
    void chrome.runtime.lastError
    port = undefined
    void chrome.action.setBadgeText({text: '!'})
  })
}
chrome.tabs.onUpdated.addListener((tabId, change, tab) => {
  queue = queue.then(async () => {
    const {owned = {}} = await chrome.storage.local.get('owned')
    recordNavigation(owned, tabId, change, tab)
    await chrome.storage.local.set({owned})
  }).catch(() => chrome.action.setBadgeText({text: 'E'}))
})
chrome.alarms.create('reconnect', {periodInMinutes: 0.5})
chrome.alarms.onAlarm.addListener(connect)
chrome.runtime.onStartup.addListener(connect)
chrome.runtime.onInstalled.addListener(connect)
chrome.action.onClicked.addListener(connect)
connect()
