import { tmpdir } from 'node:os'
import fs from 'fs-extra'
import { join } from 'pathe'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { mergeCcgHooks, registerHooksInSettingsFile, shellQuote } from '../installer'

describe('mergeCcgHooks', () => {
  it('replaces every old CCG entry with one UserPromptSubmit guardrail and preserves user hooks', () => {
    const settings = {
      env: { KEEP: 'yes' },
      hooks: {
        UserPromptSubmit: [
          { hooks: [
            { type: 'command', command: 'node /Users/test/.claude/hooks/ccg/workflow-state.js' },
            { type: 'command', command: 'node /opt/combined-user-hook.js' },
          ] },
          { hooks: [{ type: 'command', command: 'node /opt/user-hook.js' }] },
        ],
        SessionStart: [
          { hooks: [{ type: 'command', command: 'node /Users/test/.claude/hooks/ccg/session-start.js' }] },
          { hooks: [{ type: 'command', command: 'node /Users/test/.claude/hooks/ccg/custom.js' }] },
        ],
        PreToolUse: [{ hooks: [{ type: 'command', command: 'node /Users/test/.claude/hooks/ccg/subagent-context.js' }] }],
      },
    }

    const merged = mergeCcgHooks(settings, '/Users/test/.claude/hooks/ccg')
    expect(merged.env).toEqual({ KEEP: 'yes' })
    expect(merged.hooks.SessionStart).toHaveLength(1)
    expect(merged.hooks.SessionStart[0].hooks[0].command).toContain('custom.js')
    expect(merged.hooks.PreToolUse).toBeUndefined()
    expect(merged.hooks.UserPromptSubmit).toHaveLength(3)
    expect(merged.hooks.UserPromptSubmit[0].hooks).toEqual([{ type: 'command', command: 'node /opt/combined-user-hook.js' }])
    expect(merged.hooks.UserPromptSubmit[1].hooks[0].command).toBe('node /opt/user-hook.js')
    expect(merged.hooks.UserPromptSubmit[2].hooks).toEqual([
      { type: 'command', command: "node '/Users/test/.claude/hooks/ccg/adaptive-guardrail.js'", timeout: 10000 },
      { type: 'command', command: "node '/Users/test/.claude/hooks/ccg/workflow-state.js'", timeout: 10000 },
      { type: 'command', command: "node '/Users/test/.claude/hooks/ccg/skill-router.js'", timeout: 5000 },
    ])
    expect(mergeCcgHooks(JSON.parse(JSON.stringify(merged)), '/Users/test/.claude/hooks/ccg')).toEqual(merged)
  })
})

describe('shellQuote', () => {
  it('uses the native command-shell quoting rules', () => {
    expect(shellQuote("/Users/O'Connor/.claude/hook.js", 'darwin')).toBe("'/Users/O'\\''Connor/.claude/hook.js'")
    expect(shellQuote('C:\\Users\\A B\\hook.js', 'win32')).toBe('"C:\\Users\\A B\\hook.js"')
  })
})

describe('registerHooksInSettingsFile', () => {
  let root: string
  let settingsPath: string
  const hooksDir = '/Users/test/.claude/hooks/ccg'
  beforeEach(async () => {
    root = await fs.mkdtemp(join(tmpdir(), 'ccg-settings-test-'))
    settingsPath = join(root, 'settings.json')
  })
  afterEach(async () => { await fs.remove(root) })

  it('never rewrites a settings file it could not parse', async () => {
    // A stray trailing comma is a common hand-edit; rewriting it would discard
    // every unrelated setting the user has.
    const broken = '{\n  "env": { "KEEP": "yes" },\n}\n'
    await fs.writeFile(settingsPath, broken)

    const failure = await registerHooksInSettingsFile(settingsPath, hooksDir)

    expect(failure).toContain('is not valid JSON')
    expect(await fs.readFile(settingsPath, 'utf8')).toBe(broken)
  })

  it('merges into a valid file and preserves unrelated settings', async () => {
    await fs.writeFile(settingsPath, JSON.stringify({
      env: { KEEP: 'yes' },
      hooks: { PreToolUse: [{ hooks: [{ type: 'command', command: 'node /opt/user-hook.js' }] }] },
    }, null, 2))

    const failure = await registerHooksInSettingsFile(settingsPath, hooksDir)

    expect(failure).toBeUndefined()
    const written = await fs.readJson(settingsPath)
    expect(written.env).toEqual({ KEEP: 'yes' })
    expect(written.hooks.PreToolUse).toHaveLength(1)
    expect(written.hooks.PreToolUse[0].hooks[0].command).toBe('node /opt/user-hook.js')
    expect(JSON.stringify(written.hooks.UserPromptSubmit)).toContain('/hooks/ccg/adaptive-guardrail.js')
  })

  it('creates the file when none exists', async () => {
    const failure = await registerHooksInSettingsFile(settingsPath, hooksDir)

    expect(failure).toBeUndefined()
    const written = await fs.readJson(settingsPath)
    expect(JSON.stringify(written.hooks.UserPromptSubmit)).toContain('/hooks/ccg/workflow-state.js')
  })
})
