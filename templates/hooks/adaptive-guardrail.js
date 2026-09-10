#!/usr/bin/env node
// CCG Adaptive Review Guardrail — UserPromptSubmit
//
// This is intentionally the only CCG hook registered for Claude Code.  It is
// quiet while work is in progress and asks for a persisted dual-model review
// only at a meaningful review/delivery boundary.  It never starts a reviewer
// process itself: a hook must not turn every edit into an expensive background
// job or race the agent that is still writing the change.

'use strict';

const childProcess = require('child_process');
const fs = require('fs');
const path = require('path');
const { findProjectRoot, getActiveTask, outputHook } = require('./task-utils.js');

const SOURCE_EXTENSIONS = new Set([
  '.c', '.cc', '.cpp', '.cs', '.go', '.java', '.js', '.jsx', '.kt', '.kts',
  '.mjs', '.php', '.py', '.rb', '.rs', '.scala', '.sh', '.sql', '.swift',
  '.ts', '.tsx', '.vue',
]);
const REVIEWABLE_CONFIG_NAMES = new Set([
  'dockerfile', 'docker-compose.yml', 'docker-compose.yaml', 'openapi.yaml',
  'openapi.yml', 'openapi.json', 'swagger.yaml', 'swagger.yml', 'swagger.json',
]);
const TEST_PATH = /(^|[/\\])(?:test|tests|__tests__|spec|fixtures?)(?:[/\\]|$)|[._-](?:test|spec)\.[^.]+$/i;
const HIGH_RISK_PATH = /(?:^|[/\\_.-])(?:auth|login|password|token|secret|credential|crypto|encrypt|permission|admin|migration|schema|openid|oauth|sso)(?=$|[/\\_.-])/i;
const CONTRACT_PATH = /(^|[/\\])(api|apis|contracts?|migrations?|schema|openapi|swagger|proto)([/\\]|$)|\.(?:proto|graphql|gql)$/i;
const CLOSING_INTENT = /\b(done|finish(?:ed)?|complete(?:d)?|deliver(?:ed|y)?|submit|ready\s+to\s+(?:ship|merge)|review\s+it)\b|完成|完毕|交付|提交|验收|收尾|可以合并|准备合并|准备发布/iu;
const REVIEW_INTENT = /\breview\b|\baudit\b|dual[ -]?review|审查|审核|代码检查|交叉验证/iu;

function runGit(root, args) {
  try {
    return childProcess.execFileSync('git', args, {
      cwd: root,
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'ignore'],
      timeout: 1500,
    });
  }
  catch {
    return '';
  }
}

function isReviewableSource(file) {
  const normalized = file.replace(/\\/g, '/');
  const name = path.basename(normalized).toLowerCase();
  if (TEST_PATH.test(normalized)) return false;
  return SOURCE_EXTENSIONS.has(path.extname(name)) || REVIEWABLE_CONFIG_NAMES.has(name) || CONTRACT_PATH.test(normalized);
}

function lineCount(file) {
  let descriptor;
  try {
    descriptor = fs.openSync(file, 'r');
    const buffer = Buffer.alloc(64 * 1024);
    const bytes = fs.readSync(descriptor, buffer, 0, buffer.length, 0);
    if (bytes === 0) return 0;
    let lines = 0;
    for (let index = 0; index < bytes; index++) {
      if (buffer[index] === 10) lines++;
      if (lines >= 80) return 80;
    }
    // A large source file with very long lines still deserves the conservative
    // delivery gate without reading it all synchronously from a prompt hook.
    if (bytes === buffer.length) return 80;
    return lines + (buffer[bytes - 1] === 10 ? 0 : 1);
  }
  catch {
    return 0;
  }
  finally {
    if (descriptor !== undefined) fs.closeSync(descriptor);
  }
}

function shellQuote(value) {
  return `'${value.replace(/'/g, "'\\''")}'`;
}

function changedFiles(root) {
  const status = runGit(root, ['-c', 'core.quotepath=off', 'status', '--porcelain=v1', '-z', '--untracked-files=all']);
  const paths = new Map();
  const records = status.split('\0');
  for (let index = 0; index < records.length; index++) {
    const record = records[index];
    if (!record) continue;
    const statusCode = record.slice(0, 2);
    const filename = record.slice(3);
    if (!filename) continue;
    paths.set(filename, { untracked: statusCode === '??' });
    // In porcelain -z rename/copy entries include a second NUL-delimited old
    // path. The first path is the current destination and is the one to gate.
    if (statusCode.includes('R') || statusCode.includes('C')) index++;
  }

  const numstat = runGit(root, ['-c', 'core.quotepath=off', 'diff', 'HEAD', '--numstat', '-z', '--find-renames']);
  const lineTotals = new Map();
  const diffRecords = numstat.split('\0');
  for (let index = 0; index < diffRecords.length; index++) {
    const record = diffRecords[index];
    if (!record) continue;
    const [additions, deletions, filename] = record.split('\t', 3);
    if (additions === '-' || deletions === '-') continue;
    // A rename/copy numstat entry uses an empty filename followed by old and
    // new paths. Count it against the current path, not the removed one.
    const currentPath = filename || diffRecords[index + 2];
    if (filename === '') index += 2;
    if (!currentPath) continue;
    lineTotals.set(currentPath, Number(additions) + Number(deletions));
  }

  return [...paths.entries()].map(([file, metadata]) => ({
    file,
    lines: lineTotals.get(file) ?? (metadata.untracked ? lineCount(path.join(root, file)) : 0),
  }));
}

function taskProfile(task) {
  const strategy = String(task?.strategy || '').toLowerCase();
  const configured = String(task?.complexity || '').toUpperCase();
  const complexity = ['S', 'M', 'L', 'XL'].includes(configured)
    ? configured
    : strategy.includes('full-collaborate') ? 'L'
      : strategy.includes('guided') || strategy.includes('debug') || strategy.includes('refactor') ? 'M'
        : strategy.includes('direct') || strategy.includes('quick') ? 'S'
          : 'M';
  const phase = String(task?.currentPhase || '').toLowerCase();
  const risk = String(task?.risk || 'low').toLowerCase();
  return { complexity, phase, risk };
}

function reviewDecision(task, files, message) {
  const { complexity, phase, risk } = taskProfile(task);
  const reviewable = files.filter(change => isReviewableSource(change.file));
  const sourceFiles = reviewable.length;
  const changedLines = reviewable.reduce((total, change) => total + change.lines, 0);
  const highRisk = risk === 'high' || reviewable.some(change => HIGH_RISK_PATH.test(change.file));
  const contractChange = reviewable.some(change => CONTRACT_PATH.test(change.file));
  const boundary = phase === 'review' || CLOSING_INTENT.test(message);
  const explicitlyRequested = REVIEW_INTENT.test(message);

  if (sourceFiles === 0) {
    return { due: explicitlyRequested, reasons: explicitlyRequested ? ['用户明确请求审查'] : [], sourceFiles, changedLines, complexity };
  }
  if (explicitlyRequested) {
    return { due: true, reasons: ['用户明确请求审查'], sourceFiles, changedLines, complexity };
  }
  if (!boundary) {
    return { due: false, reasons: [], sourceFiles, changedLines, complexity };
  }
  if (highRisk) {
    return { due: true, reasons: ['高风险认证、安全、迁移或权限变更'], sourceFiles, changedLines, complexity };
  }
  if (complexity === 'L' || complexity === 'XL') {
    return { due: true, reasons: [`${complexity} 复杂度任务到达交付/审查节点`], sourceFiles, changedLines, complexity };
  }
  if (complexity === 'M' && (sourceFiles >= 3 || changedLines >= 80 || contractChange)) {
    const reason = contractChange ? '接口契约或数据结构变更' : sourceFiles >= 3 ? '跨 3 个以上源码文件' : `源码变更达到 ${changedLines} 行`;
    return { due: true, reasons: [`M 复杂度任务在交付节点且${reason}`], sourceFiles, changedLines, complexity };
  }
  return { due: false, reasons: [], sourceFiles, changedLines, complexity };
}

function readUserMessage() {
  let input = '';
  try {
    if (!process.stdin.isTTY) input = fs.readFileSync(0, 'utf8');
    const parsed = JSON.parse(input);
    const message = parsed.message || parsed.content || parsed.prompt || '';
    return typeof message === 'string' ? message : JSON.stringify(message);
  }
  catch {
    return input;
  }
}

function main() {
  const cwd = process.env.CLAUDE_PROJECT_DIR || process.cwd();
  const root = findProjectRoot(cwd);
  if (!root) return;

  const task = getActiveTask(root);
  if (!task) return;

  const decision = reviewDecision(task, changedFiles(root), readUserMessage());
  if (!decision.due) return;

  const reasons = decision.reasons.map(reason => `- ${reason}`).join('\n');
  const homeDir = process.env.HOME || process.env.USERPROFILE || '';
  const supervisor = shellQuote(path.join(homeDir, '.claude', 'bin', 'ccg-agent-supervisor'));
  const context = [
    '<ccg-review-gate>',
    '双模型审查已到达有效触发点；不要把这个要求提前到每次编辑。',
    reasons,
    `审查范围：${decision.sourceFiles} 个源码/契约文件，约 ${decision.changedLines} 行。`,
    '先完成相关测试，再在当前工作目录执行一次持久化审查：',
    `  printf '%s\\n' 'Review the current change for correctness, security, regression risk, and maintainability. Return Critical/Warning/Info findings with file:line evidence.' | ${supervisor} review --workdir "$(pwd)" --snapshot-base HEAD --include-untracked --claude-effort "\${CCG_CLAUDE_REVIEW_EFFORT:-low}"`,
    '等待两个 leaf 都返回；任一 transport/model 失败或超时都不是审查通过。结果会写入 CCG Review Center。',
    '在审查结果明确前，不要把任务标记为 completed。',
    '</ccg-review-gate>',
  ].join('\n');
  outputHook('UserPromptSubmit', context);
}

try {
  main();
}
catch {
  // Hooks must never make Claude Code unavailable.
}

module.exports = { changedFiles, isReviewableSource, reviewDecision, taskProfile };
