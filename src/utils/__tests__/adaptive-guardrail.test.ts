import { afterEach, describe, expect, it } from 'vitest'
import { execFile, spawn } from 'node:child_process'
import { copyFile, mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { promisify } from 'node:util'

const execFileAsync = promisify(execFile)
const roots: string[] = []
const hookSource = process.env.CCG_GUARDRAIL_PATH || join(process.cwd(), 'templates', 'hooks', 'adaptive-guardrail.js')
const taskUtilsSource = join(process.cwd(), 'templates', 'hooks', 'task-utils.js')
const skillRouterSource = join(process.cwd(), 'templates', 'hooks', 'skill-router.js')
const codexHook = process.env.CCG_CODEX_GUARDRAIL_PATH || join(process.cwd(), 'templates', 'codex', 'hooks', 'ccg-workflow.py')
const python = process.platform === 'win32' ? 'python' : 'python3'

async function fixture(complexity: 'S' | 'M' | 'L', phase = 'implementation') {
  const root = await mkdtemp(join(tmpdir(), 'ccg-adaptive-hook-'))
  roots.push(root)
  await mkdir(join(root, '.ccg', 'tasks', 'current'), { recursive: true })
  await mkdir(join(root, 'src'), { recursive: true })
  await mkdir(join(root, 'hooks'), { recursive: true })
  await copyFile(hookSource, join(root, 'hooks', 'adaptive-guardrail.js'))
  await copyFile(taskUtilsSource, join(root, 'hooks', 'task-utils.js'))
  await copyFile(skillRouterSource, join(root, 'hooks', 'skill-router.js'))
  await writeFile(join(root, '.ccg', 'tasks', 'current', 'task.json'), JSON.stringify({
    id: 'current', status: 'in_progress', complexity, currentPhase: phase, risk: 'low',
  }))
  await writeFile(join(root, 'src', 'base.ts'), 'export const base = true\n')
  await execFileAsync('git', ['init', '-q'], { cwd: root })
  await execFileAsync('git', ['add', '.'], { cwd: root })
  await execFileAsync('git', ['-c', 'user.name=CCG Test', '-c', 'user.email=ccg@example.test', 'commit', '-qm', 'initial'], { cwd: root })
  return root
}

async function runHook(root: string, message: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const child = spawn('node', [join(root, 'hooks', 'adaptive-guardrail.js')], {
      cwd: root,
      env: { ...process.env, CLAUDE_PROJECT_DIR: root },
      stdio: ['pipe', 'pipe', 'pipe'],
    })
    let stdout = ''
    let stderr = ''
    child.stdout.on('data', chunk => { stdout += chunk })
    child.stderr.on('data', chunk => { stderr += chunk })
    child.on('error', reject)
    child.on('close', (code) => code === 0 ? resolve(stdout) : reject(new Error(stderr)))
    child.stdin.end(JSON.stringify({ message }))
  })
}

async function runCodexHook(root: string, message: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const child = spawn(python, [codexHook], {
      cwd: root,
      env: { ...process.env, CODEX_PROJECT_DIR: root },
      stdio: ['pipe', 'pipe', 'pipe'],
    })
    let stdout = ''
    let stderr = ''
    child.stdout.on('data', chunk => { stdout += chunk })
    child.stderr.on('data', chunk => { stderr += chunk })
    child.on('error', reject)
    child.on('close', (code) => code === 0 ? resolve(stdout) : reject(new Error(stderr)))
    child.stdin.end(JSON.stringify({ message }))
  })
}

async function runSkillRouter(root: string, message: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const child = spawn('node', [join(root, 'hooks', 'skill-router.js')], {
      cwd: root,
      env: { ...process.env, CLAUDE_PROJECT_DIR: root },
      stdio: ['pipe', 'pipe', 'pipe'],
    })
    let stdout = ''
    let stderr = ''
    child.stdout.on('data', chunk => { stdout += chunk })
    child.stderr.on('data', chunk => { stderr += chunk })
    child.on('error', reject)
    child.on('close', (code) => code === 0 ? resolve(stdout) : reject(new Error(stderr)))
    child.stdin.end(JSON.stringify({ message }))
  })
}

afterEach(async () => {
  await Promise.all(roots.splice(0).map(root => rm(root, { recursive: true, force: true })))
})

describe('adaptive review guardrail', () => {
  it('keeps a small low-risk S delivery quiet', async () => {
    const root = await fixture('S')
    await writeFile(join(root, 'src', 'base.ts'), 'export const base = false\n')
    expect(await runHook(root, '这个小修完成了')).toBe('')
  })

  it('does not trigger M work below the source-file and line thresholds', async () => {
    const root = await fixture('M')
    await writeFile(join(root, 'src', 'base.ts'), 'export const base = false\n')
    await writeFile(join(root, 'src', 'helper.ts'), 'export const helper = true\n')
    expect(await runHook(root, '准备交付')).toBe('')
  })

  it('requires a persisted review for M work spanning three source files at delivery', async () => {
    const root = await fixture('M')
    await writeFile(join(root, 'src', 'base.ts'), 'export const base = false\n')
    await writeFile(join(root, 'src', 'helper.ts'), 'export const helper = true\n')
    await writeFile(join(root, 'src', 'adapter.ts'), 'export const adapter = true\n')
    const output = await runHook(root, '准备交付')
    expect(output).toContain('<ccg-review-gate>')
    expect(output).toContain('ccg-agent-supervisor')
    expect(output).toContain('review --workdir')
    expect(output).toContain('跨 3 个以上源码文件')
  })

  it('requires review for a small high-risk change at delivery', async () => {
    const root = await fixture('S')
    await writeFile(join(root, 'src', 'auth-token.ts'), 'export const verify = () => true\n')
    const output = await runHook(root, '完成，准备提交')
    expect(output).toContain('高风险认证、安全、迁移或权限变更')
  })

  it('counts renamed and non-ASCII source paths toward the M delivery threshold', async () => {
    const root = await fixture('M')
    await execFileAsync('git', ['mv', 'src/base.ts', 'src/重命名.ts'], { cwd: root })
    await writeFile(join(root, 'src', 'helper.ts'), 'export const helper = true\n')
    await writeFile(join(root, 'src', 'adapter.ts'), 'export const adapter = true\n')
    const output = await runHook(root, '准备交付')
    expect(output).toContain('跨 3 个以上源码文件')
  })

  it('ignores document-only changes and respects an explicit review request', async () => {
    const root = await fixture('M')
    await writeFile(join(root, 'README.md'), '# Notes\n')
    expect(await runHook(root, '准备交付')).toBe('')
    expect(await runHook(root, '请审查当前改动')).toContain('用户明确请求审查')
  })

  it('routes an explicit dual review through the persisted supervisor', async () => {
    const root = await fixture('S')
    const output = await runSkillRouter(root, '请双模型审查当前代码')
    expect(output).toContain('ccg-agent-supervisor')
    expect(output).toContain('review --workdir')
    expect(output).not.toContain('codeagent-wrapper')
  })

  it('keeps the single Codex hook quiet for small work and opens the same gate for a qualifying delivery', async () => {
    const root = await fixture('M')
    await writeFile(join(root, 'src', 'base.ts'), 'export const base = false\n')
    expect(await runCodexHook(root, '准备交付')).toBe('')
    await writeFile(join(root, 'src', 'helper.ts'), 'export const helper = true\n')
    await writeFile(join(root, 'src', 'adapter.ts'), 'export const adapter = true\n')
    const output = await runCodexHook(root, '准备交付')
    const context = JSON.parse(output).hookSpecificOutput.additionalContext
    expect(context).toContain('ccg-agent-supervisor review')
    expect(context).toContain('跨 3 个以上源码文件')
  })
})
