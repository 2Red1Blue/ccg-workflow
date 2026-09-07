import { afterEach, describe, expect, it } from 'vitest'
import { mkdtemp, readFile, rm, symlink, writeFile } from 'node:fs/promises'
import { stat } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { installReviewSupervisor, uninstallReviewSupervisor } from '../installer-review-supervisor'

const roots: string[] = []

async function fixture() {
  const root = await mkdtemp(join(tmpdir(), 'ccg-review-supervisor-'))
  roots.push(root)
  const source = join(root, 'source')
  await writeFile(join(root, 'marker'), '')
  await (await import('fs-extra')).default.ensureDir(source)
  await writeFile(join(source, 'ccg-agent-supervisor.py'), '#!/usr/bin/env python3\nprint("supervisor")\n')
  await writeFile(join(source, 'ccg_review_runtime.py'), 'RUNTIME = 1\n')
  for (const extension of ['py', 'html', 'js', 'css']) {
    await writeFile(join(source, `ccg_review_web.${extension}`), `web asset ${extension}\n`)
  }
  await writeFile(join(source, 'VERSION'), '9.8.7\n')
  return { root, source, install: join(root, 'install') }
}

afterEach(async () => {
  await Promise.all(roots.splice(0).map(root => rm(root, { recursive: true, force: true })))
})

describe('installReviewSupervisor', () => {
  it('installs private, executable scripts and a managed receipt', async () => {
    const { install, source } = await fixture()
    await installReviewSupervisor(install, source)
    const main = join(install, 'bin', 'ccg-agent-supervisor')
    const runtime = join(install, 'bin', 'ccg_review_runtime.py')
    expect(await readFile(main, 'utf8')).toContain('supervisor')
    expect(await readFile(runtime, 'utf8')).toContain('RUNTIME')
    expect((await stat(main)).mode & 0o777).toBe(0o700)
    expect((await stat(runtime)).mode & 0o777).toBe(0o600)
    const receipt = JSON.parse(await readFile(join(install, '.ccg', 'review-supervisor.json'), 'utf8'))
    expect(receipt.files).toHaveProperty('ccg-agent-supervisor')
    expect(receipt.componentVersion).toBe('9.8.7')
    for (const extension of ['py', 'html', 'js', 'css']) {
      expect(await readFile(join(install, 'bin', `ccg_review_web.${extension}`), 'utf8')).toContain('web asset')
      expect(receipt.files).toHaveProperty(`ccg_review_web.${extension}`)
    }
  })

  it('backs up changed existing files before atomically replacing them', async () => {
    const { install, source } = await fixture()
    const destination = join(install, 'bin', 'ccg-agent-supervisor')
    await (await import('fs-extra')).default.ensureDir(join(install, 'bin'))
    await writeFile(destination, 'manual prior version\n')
    await installReviewSupervisor(install, source)
    expect(await readFile(destination, 'utf8')).toContain('supervisor')
    const backups = await (await import('fs-extra')).default.readdir(join(install, '.ccg', 'backups', 'review-supervisor'))
    expect(backups).toHaveLength(1)
  })

  it('refuses to replace a symlink', async () => {
    const { install, source, root } = await fixture()
    const bin = join(install, 'bin')
    await (await import('fs-extra')).default.ensureDir(bin)
    const target = join(root, 'outside')
    await writeFile(target, 'keep')
    await symlink(target, join(bin, 'ccg-agent-supervisor'))
    await expect(installReviewSupervisor(install, source)).rejects.toThrow('non-regular')
    expect(await readFile(target, 'utf8')).toBe('keep')
  })

  it('refuses a symlinked installation directory before creating files', async () => {
    const { install, source, root } = await fixture()
    const target = join(root, 'target')
    await (await import('fs-extra')).default.ensureDir(target)
    await symlink(target, install)
    await expect(installReviewSupervisor(install, source)).rejects.toThrow('symlink')
    expect(await (await import('fs-extra')).default.readdir(target)).toEqual([])
  })

  it('uninstalls only files still matching its receipt', async () => {
    const { install, source } = await fixture()
    await installReviewSupervisor(install, source)
    const main = join(install, 'bin', 'ccg-agent-supervisor')
    const runtime = join(install, 'bin', 'ccg_review_runtime.py')
    await writeFile(runtime, 'operator change\n')
    expect(await uninstallReviewSupervisor(install)).toEqual(['ccg-agent-supervisor', 'ccg_review_web.py', 'ccg_review_web.html', 'ccg_review_web.js', 'ccg_review_web.css'])
    await expect(readFile(main)).rejects.toThrow()
    expect(await readFile(runtime, 'utf8')).toBe('operator change\n')
  })
})
