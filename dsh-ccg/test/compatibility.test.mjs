import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

const manifest = JSON.parse(await readFile(new URL('../package.json', import.meta.url), 'utf8'))
const lock = JSON.parse(await readFile(new URL('../package-lock.json', import.meta.url), 'utf8'))

test('the package release and lockfile versions agree', () => {
  assert.equal(manifest.version, '0.4.8')
  assert.equal(lock.version, manifest.version)
  assert.equal(lock.packages[''].version, manifest.version)
})

test('DSH peer ranges include the tested 0.2.1 alpha without widening prerelease support', () => {
  const compatiblePeers = [
    '@deepseek-ai/dsh-settings',
    '@deepseek-ai/dsh-skill-filesystem',
    '@deepseek-ai/dsh-subagent',
    '@deepseek-ai/dsh-tool-subagent',
    '@deepseek-ai/dsh-tools',
    '@deepseek-ai/dsh-storage-domain',
  ]
  for (const name of compatiblePeers) {
    assert.equal(manifest.peerDependencies[name], '^0.1.0-rc.6 || 0.2.1-alpha.1')
    assert.equal(lock.packages[''].peerDependencies[name], manifest.peerDependencies[name])
  }
  assert.ok(!Object.values(manifest.peerDependencies).includes('*'))
})
