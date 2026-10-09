/**
 * Unit tests for the card's data seam. The payload builder and the section
 * folder are pure, so what the card shows and what a save stores are tested
 * without a browser, a web server, or a profile-settings write.
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'

import {
  buildConfigPayload,
  describeProfileSettings,
  isLoopback,
  mutateProfileSettings,
  nextUserSection,
  profileEntryId,
  settingsOperations,
} from '../src/api.js'
import { ROLE_NAMES } from '../src/roles.js'

test('the payload reports each tier, whether the user layer carries it, and the tools', () => {
  const value = { strong: { provider: 'gw', model: 'big' }, worker: { provider: 'gw', model: 'fast' } }
  const payload = buildConfigPayload(value, { strong: value.strong }, true, [], 7)

  assert.equal(payload.revision, 7)
  assert.deepEqual(payload.tiers.strong, { provider: 'gw', model: 'big', overridden: true })
  assert.deepEqual(payload.tiers.worker, { provider: 'gw', model: 'fast', overridden: false })
  assert.equal(payload.roles.length, ROLE_NAMES.length)
  assert.deepEqual(payload.roles.find((entry) => entry.role === 'reviewer'), {
    role: 'reviewer',
    tool: 'ccg_review',
    tier: 'strong',
    enabled: true,
    members: [{ provider: 'gw', model: 'big' }],
    pinned: false,
  })
  assert.deepEqual(payload.roles.find((entry) => entry.role === 'builder').members, [
    { provider: 'gw', model: 'fast' },
  ])
})

test('a role holding several models reports them all and reads as pinned', () => {
  const value = {
    strong: { provider: 'gw', model: 'big' },
    roles: {
      analyzer: {
        models: [
          { provider: 'gw', model: 'big' },
          { provider: 'other', model: 'rival' },
          { provider: 'gw', model: 'big' },
        ],
      },
    },
  }
  const analyzer = buildConfigPayload(value, value, true).roles
    .find((entry) => entry.role === 'analyzer')

  // The duplicate is dropped — asking one model the same question twice is
  // not a second opinion.
  assert.deepEqual(analyzer.members, [
    { provider: 'gw', model: 'big' },
    { provider: 'other', model: 'rival' },
  ])
  assert.equal(analyzer.pinned, true)
})

test('a role model list is stored, deduplicated, and cleared back to its tier', () => {
  const added = nextUserSection({}, {
    roles: {
      tester: {
        models: [
          { provider: 'gw', model: 'a' },
          { provider: 'gw', model: 'b' },
          { provider: 'gw', model: 'a' },
        ],
      },
    },
  })
  assert.deepEqual(added.roles.tester.models, [
    { provider: 'gw', model: 'a' },
    { provider: 'gw', model: 'b' },
  ])

  // Clearing the models must not discard what else that role carries.
  const cleared = nextUserSection(
    { roles: { tester: { models: [{ provider: 'gw', model: 'a' }], tier: 'strong' } } },
    { roles: { tester: null } },
  )
  assert.deepEqual(cleared, { roles: { tester: { tier: 'strong' } } })

  // A role left holding nothing at all leaves no empty husk behind.
  const emptied = nextUserSection(
    { roles: { tester: { models: [{ provider: 'gw', model: 'a' }] } } },
    { roles: { tester: null } },
  )
  assert.deepEqual(emptied, {})
})

test('the payload survives an absent or malformed section rather than throwing', () => {
  for (const value of [undefined, null, 'nonsense', { strong: 7 }]) {
    const payload = buildConfigPayload(value, undefined, false)
    assert.deepEqual(payload.tiers.strong, { provider: '', model: '', overridden: false })
    assert.equal(payload.writable, false)
  }
})

test('the payload reflects role overrides the document carries', () => {
  const payload = buildConfigPayload(
    { roles: { optimizer: { enabled: false }, analyzer: { toolName: 'deep_think', tier: 'worker' } } },
    undefined,
    true,
  )
  assert.equal(payload.roles.find((entry) => entry.role === 'optimizer').enabled, false)
  const analyzer = payload.roles.find((entry) => entry.role === 'analyzer')
  assert.equal(analyzer.tool, 'deep_think')
  assert.equal(analyzer.tier, 'worker')
})

test('a save stores a full pair, clears on null, and carries other keys through', () => {
  const user = { strong: { provider: 'old', model: 'old' }, roles: { builder: { tier: 'strong' } } }

  assert.deepEqual(
    nextUserSection(user, { worker: { provider: ' gw ', model: ' fast ' } }),
    { strong: { provider: 'old', model: 'old' }, roles: { builder: { tier: 'strong' } }, worker: { provider: 'gw', model: 'fast' } },
  )
  assert.deepEqual(
    nextUserSection(user, { strong: null }),
    { roles: { builder: { tier: 'strong' } } },
  )
})

test('profile writes touch only the CCG fields the card changed', () => {
  const user = {
    strong: { provider: 'old', model: 'old' },
    roles: { builder: { models: [{ provider: 'gw', model: 'old' }], enabled: false } },
    team: false,
  }
  const patch = { strong: null, roles: { builder: null }, team: true }
  const next = nextUserSection(user, patch)

  assert.deepEqual(next, {
    roles: { builder: { enabled: false } },
    team: true,
  })
  assert.deepEqual(settingsOperations(next, patch), [
    { op: 'unset', path: ['strong'] },
    { op: 'set', path: ['team'], value: true },
    { op: 'unset', path: ['roles', 'builder', 'models'] },
  ])
  assert.deepEqual(settingsOperations(next, {}), [])
})

test('role routing edits preserve inherited role settings under SettingsPathOps', () => {
  const base = {
    roles: {
      reviewer: { enabled: false, tier: 'strong' },
      builder: { toolName: 'build_special', tier: 'worker' },
      architect: { enabled: true, toolName: 'design_special' },
    },
  }
  const user = { roles: { builder: { models: [{ provider: 'old', model: 'old' }] } } }
  const patch = { roles: { builder: { models: [{ provider: 'gw', model: 'fast' }] } } }
  const section = nextUserSection(user, patch)
  const raw = applySettingsPathOps(user, base, settingsOperations(section, patch))

  assert.deepEqual(raw, {
    roles: { builder: { models: [{ provider: 'gw', model: 'fast' }] } },
  })
  assert.deepEqual(mergeSettingsLayers(base, raw).roles, {
    reviewer: { enabled: false, tier: 'strong' },
    builder: { toolName: 'build_special', tier: 'worker', models: [{ provider: 'gw', model: 'fast' }] },
    architect: { enabled: true, toolName: 'design_special' },
  })
})

test('role route writes clear only legacy single-pin fields that were present', () => {
  const previous = { roles: { builder: { provider: 'old', model: 'old', toolName: 'build_special' } } }
  const patch = { roles: { builder: { models: [{ provider: 'gw', model: 'fast' }] } } }
  const section = nextUserSection(previous, patch)

  assert.deepEqual(settingsOperations(section, patch, previous), [
    { op: 'set', path: ['roles', 'builder', 'models'], value: [{ provider: 'gw', model: 'fast' }] },
    { op: 'unset', path: ['roles', 'builder', 'provider'] },
    { op: 'unset', path: ['roles', 'builder', 'model'] },
  ])
})

test('clearing a role route removes only its routing fields', () => {
  const user = {
    roles: {
      builder: {
        models: [{ provider: 'gw', model: 'fast' }],
        enabled: false,
        tier: 'strong',
        toolName: 'build_special',
      },
    },
  }
  const patch = { roles: { builder: null } }
  const section = nextUserSection(user, patch)

  assert.deepEqual(applySettingsPathOps(user, {}, settingsOperations(section, patch, user)), {
    roles: { builder: { enabled: false, tier: 'strong', toolName: 'build_special' } },
  })
})

test('DSH 0.2 Settings reads and writes by the owner fiber entry id', async () => {
  const fiber = { entry: { options: { id: 'ccg-custom' } } }
  const calls = []
  const settings = {
    describe: () => [
      { ns: 'ccg', revision: 3 },
      { ns: 'ccg-custom', revision: 7, user: { team: false } },
    ],
    mutate: async (...args) => { calls.push(args) },
  }
  const ops = [{ op: 'set', path: ['team'], value: true }]

  assert.equal(profileEntryId(fiber), 'ccg-custom')
  assert.equal(describeProfileSettings(settings, fiber).revision, 7)
  await mutateProfileSettings(settings, fiber, ops, 7)
  assert.deepEqual(calls, [['ccg-custom', ops, 7]])
  assert.equal(describeProfileSettings(settings, {}), undefined)
  assert.throws(() => mutateProfileSettings(settings, {}, ops, 7), /no profile entry id/)
})

function applySettingsPathOps(current, base, operations) {
  let result = structuredClone(current)
  for (const operation of operations) {
    if (operation.op === 'set') {
      result = setSettingsPath(result, operation.path, operation.value)
      continue
    }
    const inherited = getSettingsPath(base, operation.path)
    result = inherited === undefined
      ? unsetSettingsPath(result, operation.path)
      : setSettingsPath(result, operation.path, inherited)
  }
  return result
}

function getSettingsPath(value, path) {
  return path.reduce((current, key) => current?.[key], value)
}

function setSettingsPath(value, path, item) {
  const [head, ...tail] = path
  if (head === undefined) return item
  const result = { ...value }
  result[head] = tail.length === 0 ? item : setSettingsPath(result[head] ?? {}, tail, item)
  return result
}

function unsetSettingsPath(value, path) {
  const [head, ...tail] = path
  if (head === undefined || value[head] === undefined) return value
  const result = { ...value }
  if (tail.length === 0) delete result[head]
  else result[head] = unsetSettingsPath(result[head], tail)
  return result
}

function mergeSettingsLayers(base, user) {
  const result = { ...base }
  for (const [key, value] of Object.entries(user)) {
    result[key] = value && typeof value === 'object' && !Array.isArray(value)
      ? mergeSettingsLayers(base[key] ?? {}, value)
      : value
  }
  return result
}

test('half a route clears the tier instead of storing something inert', () => {
  const user = { strong: { provider: 'old', model: 'old' } }
  assert.deepEqual(nextUserSection(user, { strong: { provider: 'gw', model: '  ' } }), {})
  assert.deepEqual(nextUserSection(user, { strong: { provider: '', model: 'fast' } }), {})
})

test('an unknown tier is refused rather than written into the document', () => {
  assert.throws(() => nextUserSection({}, { strongest: { provider: 'a', model: 'b' } }), /unknown tier/)
})

test('writes are recognised as local only from loopback peers', () => {
  for (const address of ['127.0.0.1', '::1', '::ffff:127.0.0.1']) {
    assert.equal(isLoopback({ socket: { remoteAddress: address } }), true)
  }
  for (const address of ['192.168.1.9', '10.0.0.4', '', undefined]) {
    assert.equal(isLoopback({ socket: { remoteAddress: address } }), false)
  }
  assert.equal(isLoopback({}), false)
})

test('the card reads whether hiring is on, and who decided it', () => {
  // Absent means on: the plugin registers `ccg_team` unless told not to.
  assert.deepEqual(
    buildConfigPayload({ strong: { provider: 'gw', model: 'a' } }, undefined, true).team,
    { enabled: true, overridden: false },
  )
  assert.deepEqual(
    buildConfigPayload({ team: false }, { team: false }, true).team,
    { enabled: false, overridden: true },
  )
  // On by the profile patch rather than by the user: the badge must not claim
  // an override the document does not hold.
  assert.deepEqual(
    buildConfigPayload({ team: true }, {}, true).team,
    { enabled: true, overridden: false },
  )
})

test('the hiring switch stores a boolean, and null returns it to the layer below', () => {
  assert.deepEqual(nextUserSection({}, { team: false }), { team: false })
  assert.deepEqual(nextUserSection({ team: false }, { team: true }), { team: true })
  // Clearing is not the same as writing `true` — it lets the profile decide.
  assert.deepEqual(nextUserSection({ team: false }, { team: null }), {})
  // And it leaves everything else in the document alone.
  assert.deepEqual(
    nextUserSection({ strong: { provider: 'gw', model: 'a' } }, { team: false }),
    { strong: { provider: 'gw', model: 'a' }, team: false },
  )
})
