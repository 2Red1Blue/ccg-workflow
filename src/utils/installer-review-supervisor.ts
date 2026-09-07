import { createHash, randomUUID } from 'node:crypto'
import { constants } from 'node:fs'
import { open, rename } from 'node:fs/promises'
import { join } from 'pathe'
import fs from 'fs-extra'
import { PACKAGE_ROOT } from './installer-template'

const BACKUP_LIMIT = 3
const FILES = [
  { source: 'ccg-agent-supervisor.py', target: 'ccg-agent-supervisor', mode: 0o700 },
  { source: 'ccg_review_runtime.py', target: 'ccg_review_runtime.py', mode: 0o600 },
  { source: 'ccg_review_web.py', target: 'ccg_review_web.py', mode: 0o600 },
  { source: 'ccg_review_web.html', target: 'ccg_review_web.html', mode: 0o600 },
  { source: 'ccg_review_web.js', target: 'ccg_review_web.js', mode: 0o600 },
  { source: 'ccg_review_web.css', target: 'ccg_review_web.css', mode: 0o600 },
  { source: 'assets/ccg-review-center-poster.webp', target: 'ccg_review_center_poster.webp', mode: 0o600 },
] as const

interface Manifest {
  version: 1
  componentVersion: string
  files: Record<string, string>
}

function sha256(content: Buffer): string {
  return createHash('sha256').update(content).digest('hex')
}

function manifestPath(installDir: string): string {
  return join(installDir, '.ccg', 'review-supervisor.json')
}

async function ensureOwnedDirectory(path: string): Promise<void> {
  await fs.ensureDir(path)
  const status = await fs.lstat(path)
  if (!status.isDirectory() || status.isSymbolicLink()) throw new Error(`refusing to use non-directory or symlink path: ${path}`)
}

async function readManifest(path: string): Promise<Manifest | undefined> {
  try {
    const parsed: unknown = JSON.parse(await fs.readFile(path, 'utf8'))
    if (!parsed || typeof parsed !== 'object') return undefined
    const value = parsed as Partial<Manifest>
    if (value.version !== 1 || typeof value.componentVersion !== 'string' || !value.files || typeof value.files !== 'object') return undefined
    if (FILES.some(file => typeof value.files?.[file.target] !== 'string')) return undefined
    if (Object.values(value.files).some(hash => typeof hash !== 'string' || !/^[a-f0-9]{64}$/.test(hash))) return undefined
    return value as Manifest
  }
  catch {
    return undefined
  }
}

async function componentVersion(sourceDir: string): Promise<string> {
  const version = (await fs.readFile(join(sourceDir, 'VERSION'), 'utf8')).trim()
  if (!/^\d+\.\d+\.\d+$/.test(version)) throw new Error(`invalid review supervisor VERSION: ${version}`)
  return version
}

async function readRegularFileNoFollow(path: string): Promise<Buffer | undefined> {
  let handle
  try {
    handle = await open(path, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0))
  }
  catch (error: any) {
    if (error?.code === 'ENOENT') return undefined
    if (error?.code === 'ELOOP') throw new Error(`refusing to replace non-regular supervisor file: ${path}`)
    throw error
  }
  try {
    if (!(await handle.stat()).isFile()) throw new Error(`refusing to replace non-regular supervisor file: ${path}`)
    return await handle.readFile()
  }
  finally {
    await handle.close()
  }
}

async function backupExisting(content: Buffer, target: string, installDir: string): Promise<void> {
  const backupDir = join(installDir, '.ccg', 'backups', 'review-supervisor', `${Date.now()}-${randomUUID()}`)
  await ensureOwnedDirectory(join(installDir, '.ccg', 'backups'))
  await ensureOwnedDirectory(join(installDir, '.ccg', 'backups', 'review-supervisor'))
  await ensureOwnedDirectory(backupDir)
  await fs.writeFile(join(backupDir, target), content, { mode: 0o600 })
  const parent = join(installDir, '.ccg', 'backups', 'review-supervisor')
  const entries = (await fs.readdir(parent)).sort()
  await Promise.all(entries.slice(0, Math.max(0, entries.length - BACKUP_LIMIT)).map(entry => fs.remove(join(parent, entry))))
}

/** Atomically deploy the package-owned supervisor without loading user code. */
export async function installReviewSupervisor(installDir: string, sourceDir = join(PACKAGE_ROOT, 'review-supervisor')): Promise<void> {
  const binDir = join(installDir, 'bin')
  const manifest: Manifest = { version: 1, componentVersion: await componentVersion(sourceDir), files: {} }
  await ensureOwnedDirectory(installDir)
  await ensureOwnedDirectory(binDir)
  await ensureOwnedDirectory(join(installDir, '.ccg'))

  for (const file of FILES) {
    const source = join(sourceDir, file.source)
    const content = await fs.readFile(source)
    const destination = join(binDir, file.target)
    const nextHash = sha256(content)
    const existing = await readRegularFileNoFollow(destination)
    const currentHash = existing === undefined ? undefined : sha256(existing)
    if (currentHash !== nextHash) {
      if (existing !== undefined) await backupExisting(existing, file.target, installDir)
      const temporary = join(binDir, `.${file.target}.${randomUUID()}.tmp`)
      try {
        await fs.writeFile(temporary, content, { mode: file.mode })
        await fs.chmod(temporary, file.mode)
        await rename(temporary, destination)
      }
      finally {
        await fs.remove(temporary)
      }
    }
    manifest.files[file.target] = nextHash
  }

  const destination = manifestPath(installDir)
  const temporary = `${destination}.${randomUUID()}.tmp`
  try {
    await fs.writeFile(temporary, `${JSON.stringify(manifest, null, 2)}\n`, { mode: 0o600 })
    await fs.chmod(temporary, 0o600)
    await rename(temporary, destination)
  }
  finally {
    await fs.remove(temporary)
  }
}

/** Remove only files still identical to the package-owned install receipt. */
export async function uninstallReviewSupervisor(installDir: string): Promise<string[]> {
  const receipt = manifestPath(installDir)
  const manifest = await readManifest(receipt)
  if (!manifest) return []
  const removed: string[] = []
  for (const file of FILES) {
    const destination = join(installDir, 'bin', file.target)
    try {
      const content = await readRegularFileNoFollow(destination)
      if (content !== undefined && sha256(content) === manifest.files[file.target]) {
        await fs.remove(destination)
        removed.push(file.target)
      }
    }
    catch {
      // Missing, modified, or non-regular paths belong to the operator.
    }
  }
  await fs.remove(receipt)
  return removed
}
