export function validTask(task) {
  if (!task || !/^[a-f0-9]{32}$/.test(task.id)) return false
  try {
    const url = new URL(task.url)
    return url.protocol === 'http:' && url.hostname === '127.0.0.1' && url.port !== ''
      && !url.username && !url.password && url.pathname === '/' && !url.search
      && url.hash === `#ccg-task=${task.id}`
  } catch { return false }
}

export async function reconcile(chrome, state, tasks, now, persist = async () => {}) {
  if (Object.values(state).some(record => record.creating)) await recoverOwnership(chrome, state)
  const active = new Map(tasks.filter(validTask).map(task => [task.id, task]))
  for (const [id, record] of Object.entries(state)) {
    if (active.has(id)) { delete record.finishedAt; continue }
    record.finishedAt ??= now
    if (now - record.finishedAt < 3000) continue
    if (record.tabId !== undefined && !record.detached) {
      try {
        const tab = await chrome.tabs.get(record.tabId)
        // Navigated-away, reused or user-created tabs are never closed.
        if (tab.url === record.url && (!tab.pendingUrl || tab.pendingUrl === record.url)) await chrome.tabs.remove(record.tabId)
      } catch { /* User may already have closed this tab. */ }
    }
    delete state[id]
    await persist(state)
  }
  const windows = await chrome.windows.getAll({windowTypes: ['normal']})
  const window = windows.find(w => w.focused) ?? windows[0]
  if (!window) return state // Never create/focus a window just for a task.
  for (const task of active.values()) {
    if (state[task.id]) continue
    // Persist a reservation BEFORE the browser side effect. If a worker dies
    // after create but before storing its ID, recovery finds the unique URL.
    state[task.id] = {url: task.url, creating: true}
    await persist(state)
    const tab = await chrome.tabs.create({windowId: window.id, url: task.url, active: false})
    state[task.id] = {tabId: tab.id, url: task.url}
    await persist(state)
  }
  return state
}

export function recordNavigation(state, tabId, change, tab) {
  for (const record of Object.values(state)) {
    if (record.tabId !== tabId) continue
    if ((change.url && change.url !== record.url) || (tab.pendingUrl && tab.pendingUrl !== record.url)) record.detached = true
  }
  return state
}

export async function recoverOwnership(chrome, state) {
  // Chrome can assign new tab IDs after restart. Reattach only an unambiguous
  // task URL whose ownership was previously persisted; never arbitrary tabs.
  const tabs = await chrome.tabs.query({url: 'http://127.0.0.1/*'})
  for (const [id, record] of Object.entries(state)) {
    if (record.detached) continue
    const matches = tabs.filter(tab => tab.url === record.url && (!tab.pendingUrl || tab.pendingUrl === record.url))
    if (matches.some(tab => tab.id === record.tabId)) { delete record.creating }
    else if (matches.length === 1) { record.tabId = matches[0].id; delete record.creating }
    else if (matches.length === 0 && record.creating) delete state[id]
    else { record.detached = true; delete record.tabId }
  }
  return state
}
