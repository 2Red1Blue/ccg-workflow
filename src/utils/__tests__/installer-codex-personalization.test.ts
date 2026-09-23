import { homedir, tmpdir } from 'node:os'
import fs from 'fs-extra'
import { join } from 'pathe'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { installCodexMode, removeCodexWorkflowHook, stripCcgManagedBlock, uninstallCodexMode, writeCodexUserFileIfMissing } from '../installer'

vi.mock('node:os', async (original) => ({
  ...await original<typeof import('node:os')>(), homedir: vi.fn(),
}))
vi.mock('../installer-codex-api', () => ({
  configureApiMartForCodex: vi.fn(async () => ({ success: true })),
  removeApiMartFromCodex: vi.fn(async () => ({ success: true })),
}))
vi.mock('../config', () => ({ readCcgConfig: vi.fn(async () => null) }))

let root: string
let home: string
beforeEach(async () => {
  root = await fs.mkdtemp(join(tmpdir(), 'ccg-personalization-test-'))
  home = join(root, '.codex')
  await fs.ensureDir(home)
  vi.mocked(homedir).mockReturnValue(root)
})
afterEach(async () => { await fs.remove(root) })

describe('Codex personalization ownership', () => {
  it('leaves null or unrelated settings untouched', () => {
    expect(removeCodexWorkflowHook(null as any, home)).toBe(false)
    expect(removeCodexWorkflowHook({}, home)).toBe(false)
    expect(removeCodexWorkflowHook({ hooks: null }, home)).toBe(false)
  })

  it('creates defaults only when missing, including empty existing files', async () => {
    const file = join(home, 'AGENTS.md')
    await writeCodexUserFileIfMissing(file, 'default')
    expect(await fs.readFile(file, 'utf8')).toBe('default')
    await fs.writeFile(file, '')
    await writeCodexUserFileIfMissing(file, 'replacement')
    expect(await fs.readFile(file, 'utf8')).toBe('')
  })

  it('preserves symlink targets', async () => {
    const target = join(root, 'personal.md')
    await fs.writeFile(target, 'personal')
    const link = join(home, 'AGENTS.md')
    await fs.symlink(target, link)
    await writeCodexUserFileIfMissing(link, 'replacement')
    expect(await fs.readFile(target, 'utf8')).toBe('personal')
  })

  it('install and reinstall preserve personal instructions and disabled hook registration', async () => {
    const personal = '<!-- CCG:START -->\npersonal text outside or inside old markers\n'
    const hooks = '{"hooks":{"UserPromptSubmit":[{"hooks":[{"command":"openspace-hook"}]}]}}'
    await fs.writeFile(join(home, 'AGENTS.md'), personal)
    await fs.writeFile(join(home, 'hooks.json'), hooks)
    for (let i = 0; i < 2; i++) {
      expect((await installCodexMode()).success).toBe(true)
      expect(await fs.readFile(join(home, 'AGENTS.md'), 'utf8')).toBe(personal)
      expect(await fs.readFile(join(home, 'hooks.json'), 'utf8')).toBe(hooks)
    }
  })

  it('uninstall reclaims the managed block and preserves marked user instructions', async () => {
    const personal = '<!-- CCG:START -->\nold template\n<!-- CCG:END -->\nmy preferences'
    await fs.writeFile(join(home, 'AGENTS.md'), personal)
    const own = { command: `python3 ${join(home, 'hooks', 'ccg-workflow.py')}` }
    const other = { command: 'openspace-hook' }
    await fs.writeJson(join(home, 'hooks.json'), {
      hooks: { UserPromptSubmit: [{ matcher: '*', hooks: [own, other] }], Stop: [{ hooks: [other] }] },
      custom: true,
    })
    expect((await uninstallCodexMode()).success).toBe(true)
    // The managed block is CCG's and goes away; text after the end marker is the user's.
    const remaining = await fs.readFile(join(home, 'AGENTS.md'), 'utf8')
    expect(remaining).toContain('my preferences')
    expect(remaining).not.toContain('old template')
    expect(remaining).not.toContain('CCG:START')
    expect(await fs.readJson(join(home, 'hooks.json'))).toEqual({
      hooks: { UserPromptSubmit: [{ matcher: '*', hooks: [other] }], Stop: [{ hooks: [other] }] }, custom: true,
    })
  })

  it('removal is idempotent and does not match arbitrary similarly named hooks', () => {
    const settings = { hooks: { UserPromptSubmit: [
      { hooks: [{ command: 'python3 ~/.codex/hooks/ccg-workflow.py' }] },
      { hooks: [{ command: 'python3 /other/ccg-workflow.py' }] },
    ] } }
    expect(removeCodexWorkflowHook(settings, home)).toBe(true)
    expect(removeCodexWorkflowHook(settings, home)).toBe(false)
    expect(settings.hooks.UserPromptSubmit).toHaveLength(1)
  })

  it('matches installer-emitted Windows paths through pathe normalization', () => {
    const settings = { hooks: { UserPromptSubmit: [{ hooks: [
      { command: 'python3 C:/Users/test/.codex/hooks/ccg-workflow.py' },
    ] }] } }
    expect(removeCodexWorkflowHook(settings, 'C:\\Users\\test\\.codex')).toBe(true)
    expect(settings.hooks.UserPromptSubmit).toEqual([])
  })

  it('invalid shared JSON stops uninstall before deleting runtime files', async () => {
    const script = join(home, 'hooks', 'ccg-workflow.py')
    await fs.outputFile(script, 'keep runtime')
    await fs.writeFile(join(home, 'hooks.json'), '{broken')
    expect((await uninstallCodexMode()).success).toBe(false)
    expect(await fs.readFile(script, 'utf8')).toBe('keep runtime')
    expect(await fs.readFile(join(home, 'hooks.json'), 'utf8')).toBe('{broken')
  })

  it('removes the managed block it created when the user added nothing', async () => {
    expect((await installCodexMode()).success).toBe(true)
    const agentsPath = join(home, 'AGENTS.md')
    expect(await fs.readFile(agentsPath, 'utf8')).toContain('<!-- CCG:START')

    const result = await uninstallCodexMode()

    // Regression: this file is 100% CCG content, so leaving it behind kept telling
    // Codex to act as the CCG lead orchestrator after uninstall.
    expect(await fs.pathExists(agentsPath)).toBe(false)
    expect(result.removed).toContain('~/.codex/AGENTS.md [CCG-managed block]')
    expect(result.skipped.some(entry => entry.includes('AGENTS.md'))).toBe(false)
  })

  it('drops only the managed block and keeps surrounding personal text', async () => {
    const before = '# my notes\n'
    const block = '<!-- CCG:START — Managed by CCG Workflow. -->\nccg body\n<!-- CCG:END -->\n'
    const after = '\nkeep this too\n'
    await fs.writeFile(join(home, 'AGENTS.md'), before + block + after)

    const result = await uninstallCodexMode()
    const remaining = await fs.readFile(join(home, 'AGENTS.md'), 'utf8')

    expect(remaining).toContain('my notes')
    expect(remaining).toContain('keep this too')
    expect(remaining).not.toContain('CCG:START')
    expect(remaining).not.toContain('ccg body')
    expect(result.removed.some(entry => entry.includes('personal text preserved'))).toBe(true)
    expect(result.skipped.some(entry => entry.includes('AGENTS.md'))).toBe(false)
  })

  it('treats a marker-free AGENTS.md as user-owned and leaves it byte-identical', async () => {
    const personal = '# totally mine\nno ccg markers here\n'
    await fs.writeFile(join(home, 'AGENTS.md'), personal)

    const result = await uninstallCodexMode()

    expect(await fs.readFile(join(home, 'AGENTS.md'), 'utf8')).toBe(personal)
    expect(result.skipped.some(entry => entry.includes('AGENTS.md'))).toBe(true)
  })

  it('never deletes on an unterminated start marker', async () => {
    const unterminated = '<!-- CCG:START but no end marker\nuser text follows\n'
    await fs.writeFile(join(home, 'AGENTS.md'), unterminated)

    await uninstallCodexMode()

    expect(await fs.readFile(join(home, 'AGENTS.md'), 'utf8')).toBe(unterminated)
  })

  it('leaves a CRLF file byte-identical when it has no managed block', async () => {
    const personal = '# mine\r\nsecond line\r\n'
    await fs.writeFile(join(home, 'AGENTS.md'), personal)

    await uninstallCodexMode()

    expect(await fs.readFile(join(home, 'AGENTS.md'), 'utf8')).toBe(personal)
  })

  it('strips every managed block and ignores reversed markers', () => {
    const two = 'x<!-- CCG:START -->1<!-- CCG:END -->y<!-- CCG:START -->2<!-- CCG:END -->z'
    expect(stripCcgManagedBlock(two)).toBe('xyz')

    const crlf = 'a\r\n<!-- CCG:START -->\r\nbody\r\n<!-- CCG:END -->\r\nb'
    expect(stripCcgManagedBlock(crlf)).toBe('a\r\n\r\nb')

    const reversed = 'a<!-- CCG:END -->b<!-- CCG:START -->c'
    expect(stripCcgManagedBlock(reversed)).toBe(reversed)
  })
})
