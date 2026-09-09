"""Durable analysis uses the same isolated runtime without review verdicts."""
import json
import os
import re
import subprocess
import sys
import unittest

import test_ccg_agent_supervisor as harness
import test_ccg_review_runtime as stream_harness


ANALYSIS_REPORT = '## Options\nReuse the shared runtime.\n## Recommendation\nShare execution, separate contracts.\n## Risks\nContract drift.\n## Validation\nRun both modes through regression tests.'


def analysis_fake(script):
    script = script.replace('CHANGES.patch', 'CONTEXT.md')
    return re.sub(r'## Critical\\nNone\\n## Warning\\nNone\\n## Info\\nFake [^"\']+?\\n## Verdict\\nAPPROVE', lambda _: ANALYSIS_REPORT.replace('\n', r'\n'), script)


class SupervisorAnalysisTest(unittest.TestCase):
    def setUp(self):
        self.h = harness.SupervisorReviewTest()
        self.h.setUp()
        self.h.codex.write_text(analysis_fake(harness.FAKE_CODEX))
        self.h.wrapper.write_text(analysis_fake(harness.FAKE_WRAPPER))
        self.context = self.h.root / 'requirements.md'
        self.context.write_text('Choose a durable analysis architecture.')
        self.task = self.h.workdir / '.ccg/tasks/test-analysis'
        self.task.mkdir(parents=True)
        self.task_file = self.task / 'task.json'
        self.task_file.write_text(json.dumps({'id': self.task.name, 'title': 'Analysis task', 'createdAt': '2026-09-09T00:00:00Z', 'currentPhase': 'analysis'}))

    def tearDown(self):
        self.h.tearDown()

    def analyze(self, environment=None, extra=None, request=b'Analyze this request.', task=True, context=True, timeout=10):
        env = os.environ.copy()
        env['TEST_SYNC_DIR'] = str(self.h.sync)
        env.update(environment or {})
        return subprocess.run([
            sys.executable, str(harness.SUPERVISOR), '--root', str(self.h.run_root), 'analyze',
            '--workdir', str(self.h.workdir), '--codex-cli', str(self.h.codex), '--wrapper', str(self.h.wrapper),
            '--timeout-seconds', str(timeout),
            *(['--context-file', str(self.context)] if context else []),
            *(['--task-dir', str(self.task)] if task else []), *(extra or []),
        ], input=request, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout + 10)

    def latest(self):
        return max((json.loads(p.read_text()) for p in self.h.run_root.glob('*/status.json')), key=lambda s: s['started_at_epoch'])

    def test_analysis_is_concurrent_isolated_and_has_no_verdict(self):
        before = self.task_file.read_bytes()
        second = self.h.root / 'architecture.md'
        second.write_text('Preserve independent leaf execution.')
        result = self.analyze(extra=['--context-file', str(second)])
        self.assertEqual(0, result.returncode, result.stderr.decode())
        status = self.latest()
        self.assertEqual('dual_leaf_analysis', status['mode'])
        self.assertEqual('succeeded', status['state'])
        self.assertEqual(self.task.name, status['task']['taskId'])
        self.assertEqual([str(self.context), str(second)], status['source']['paths'])
        self.assertEqual(before, self.task_file.read_bytes())
        self.assertFalse(status['raw_input_retained'])
        self.assertFalse((self.h.run_root / status['run_id'] / 'bundle').exists())
        for name in ('codex', 'claude'):
            self.assertNotIn('verdict', status['backends'][name])
            self.assertTrue((self.h.run_root / status['run_id'] / status['backends'][name]['report']['path']).is_file())
        self.assertIn(b'CODEX LEAF ANALYSIS', result.stdout)
        self.assertIn(b'## Recommendation', result.stdout)
        response = subprocess.run([sys.executable, str(harness.SUPERVISOR), '--root', str(self.h.run_root), 'status', status['run_id']], capture_output=True)
        self.assertEqual(status, json.loads(response.stdout))

    def test_analysis_task_is_optional(self):
        result = self.analyze(task=False)
        self.assertEqual(0, result.returncode, result.stderr.decode())
        self.assertIsNone(self.latest()['task'])

    def test_context_is_explicit_and_diff_contract_is_separate(self):
        for extra in ([], ['--diff-file', str(self.h.patch)]):
            result = self.analyze(context=False, extra=extra)
            self.assertEqual(2, result.returncode)
        result = self.analyze(extra=['--snapshot-base', 'HEAD'])
        self.assertEqual(2, result.returncode)

    def test_empty_context_and_empty_request_fail_before_launch(self):
        self.context.write_text('  ')
        result = self.analyze()
        self.assertEqual(1, result.returncode)
        self.assertIn('analysis context is empty', self.latest()['supervisor_error'])
        self.context.write_text('Context')
        result = self.analyze(request=b'')
        self.assertEqual(1, result.returncode)
        self.assertIn('analysis request must not be empty', self.latest()['supervisor_error'])
        self.assertFalse((self.h.sync / 'codex.started').exists())

    def test_invalid_or_wrong_owner_task_fails_before_launch(self):
        for value in ('[]', '{}', '{broken', json.dumps({'id': 'different-id'})):
            self.task_file.write_text(value)
            result = self.analyze()
            self.assertEqual(1, result.returncode)
            self.assertIn('task', self.latest()['supervisor_error'])
        other = self.h.root / 'other/.ccg/tasks/task'
        other.mkdir(parents=True)
        (other / 'task.json').write_text('{"id":"task","title":"Task"}')
        result = self.analyze(task=False, extra=['--task-dir', str(other)])
        self.assertEqual(1, result.returncode)
        self.assertIn('task association', self.latest()['supervisor_error'])
        self.assertFalse((self.h.sync / 'codex.started').exists())

    def test_malformed_analysis_fails_while_preserving_success(self):
        for environment in ({'FAKE_INVALID_REPORT': '1'}, {'FAKE_CLAUDE_FAIL': '1'}):
            result = self.analyze(environment)
            self.assertEqual(1, result.returncode)
            status = self.latest()
            self.assertEqual('failed', status['state'])
            self.assertEqual('succeeded', status['backends']['codex']['state'])
            self.assertEqual('failed', status['backends']['claude']['state'])
            self.assertNotIn('verdict', status['backends']['codex'])

    def test_report_sections_must_be_nonempty_and_ordered(self):
        for text in (ANALYSIS_REPORT.replace('## Recommendation', '## Extra'), ANALYSIS_REPORT.replace('Contract drift.', ''), '```\n' + ANALYSIS_REPORT + '\n```'):
            _, error = harness.supervisor.validate_analysis_text(text)
            self.assertIsNotNone(error)
        value, error = harness.supervisor.validate_analysis_text(ANALYSIS_REPORT)
        self.assertIsNone(value)
        self.assertIsNone(error)

    def test_retry_reuses_success_only_and_ignores_mutable_task_phase(self):
        self.assertEqual(1, self.analyze({'FAKE_CLAUDE_FAIL': '1'}).returncode)
        first = self.latest()
        stamp = (self.h.sync / 'codex.started').stat().st_mtime_ns
        task = json.loads(self.task_file.read_text())
        task.update(currentPhase='planning', title='Updated title')
        self.task_file.write_text(json.dumps(task))
        before = self.task_file.read_bytes()
        result = self.analyze(extra=['--retry-run', first['run_id']])
        self.assertEqual(0, result.returncode, result.stderr.decode())
        self.assertEqual(stamp, (self.h.sync / 'codex.started').stat().st_mtime_ns)
        status = self.latest()
        self.assertEqual(first['run_id'], status['backends']['codex']['reused_from_run'])
        self.assertNotIn('verdict', status['backends']['codex'])
        self.assertEqual(before, self.task_file.read_bytes())

    def test_retry_rejects_task_request_and_context_changes(self):
        self.analyze({'FAKE_CLAUDE_FAIL': '1'})
        first = self.latest()
        other = self.task.with_name('another-task')
        other.mkdir()
        (other / 'task.json').write_text('{"id":"another-task","title":"Another task"}')
        cases = [({'request': b'Different request.'}, 'request_sha256'), ({'task': False, 'extra': ['--task-dir', str(other)]}, 'task'), ({'task': False}, 'task')]
        for kwargs, key in cases:
            kwargs['extra'] = kwargs.get('extra', []) + ['--retry-run', first['run_id']]
            result = self.analyze(**kwargs)
            self.assertEqual(1, result.returncode)
            self.assertIn(key, self.latest()['supervisor_error'])
        self.context.write_text('changed context')
        result = self.analyze(extra=['--retry-run', first['run_id']])
        self.assertEqual(1, result.returncode)
        self.assertIn('context_sha256', self.latest()['supervisor_error'])
        self.context.write_text('Choose a durable analysis architecture.')
        task = json.loads(self.task_file.read_text())
        task['createdAt'] = '2026-09-10T00:00:00Z'
        self.task_file.write_text(json.dumps(task))
        result = self.analyze(extra=['--retry-run', first['run_id']])
        self.assertEqual(1, result.returncode)
        self.assertIn('task', self.latest()['supervisor_error'])

    def test_retry_rejects_tampered_report(self):
        self.analyze({'FAKE_CLAUDE_FAIL': '1'})
        first = self.latest()
        report = self.h.run_root / first['run_id'] / 'codex.report.md'
        report.write_text(report.read_text().replace('Contract drift.', 'Tampered.'))
        result = self.analyze(extra=['--retry-run', first['run_id']])
        self.assertEqual(1, result.returncode)
        self.assertIn('integrity', self.latest()['supervisor_error'])

    def test_retry_cannot_cross_modes_in_either_direction(self):
        self.analyze()
        analysis = self.latest()
        result = self.h._review(extra_args=['--retry-run', analysis['run_id']])
        self.assertEqual(1, result.returncode)
        self.assertIn('mode', self.latest()['supervisor_error'])
        self.h.codex.write_text(harness.FAKE_CODEX)
        self.h.wrapper.write_text(harness.FAKE_WRAPPER)
        self.assertEqual(0, self.h._review().returncode)
        review = self.latest()
        result = self.analyze(extra=['--retry-run', review['run_id']])
        self.assertEqual(1, result.returncode)
        self.assertIn('mode', self.latest()['supervisor_error'])

    def test_analysis_input_bounds_and_fifo_do_not_launch_leaves(self):
        self.context.write_bytes(b'x' * (harness.supervisor.PATCH_LIMIT + 1))
        result = self.analyze(task=False)
        self.assertEqual(1, result.returncode)
        self.assertIn('exceeds', self.latest()['supervisor_error'])
        self.context.unlink()
        os.mkfifo(self.context)
        result = self.analyze(task=False, timeout=2)
        self.assertEqual(1, result.returncode)
        self.assertIn('regular file', self.latest()['supervisor_error'])
        self.context.unlink()
        self.context.write_text('Context')
        result = self.analyze(task=False, request=b'x' * (harness.supervisor.REQUEST_LIMIT + 1))
        self.assertEqual(1, result.returncode)
        self.assertIn('request exceeds', self.latest()['supervisor_error'])
        self.assertFalse((self.h.sync / 'codex.started').exists())

    def test_analysis_hard_deadline_and_model_mismatch_fail(self):
        claude = self.h._executable('fake-claude-stream', analysis_fake(stream_harness.FAKE_CLAUDE))
        extra = ['--claude-transport', 'stream', '--claude-cli', str(claude), '--claude-settings', str(self.h.root / 'settings.json')]
        result = self.analyze({'FAKE_STREAM_MODE': 'active'}, extra, task=False, timeout=4)
        self.assertEqual(124, result.returncode)
        self.assertEqual('hard_deadline', self.latest()['backends']['claude']['termination_reason'])
        self.assertEqual('succeeded', self.latest()['backends']['codex']['state'])
        result = self.analyze(extra=extra + ['--expect-claude-model', 'wrong-model'], task=False)
        self.assertEqual(1, result.returncode)
        self.assertEqual('failed', self.latest()['backends']['claude']['state'])

    def test_analysis_leaf_recursion_guard_runs_before_task_or_input_io(self):
        result = self.analyze({'CCG_LEAF_REVIEW': '1'})
        self.assertEqual(126, result.returncode)
        self.assertFalse(self.h.run_root.exists())

    def test_stream_analysis_success_and_partial_failure_are_distinct(self):
        claude = self.h._executable('fake-claude-stream', analysis_fake(stream_harness.FAKE_CLAUDE))
        extra = ['--claude-transport', 'stream', '--claude-cli', str(claude), '--claude-settings', str(self.h.root / 'settings.json')]
        result = self.analyze(extra=extra)
        self.assertEqual(0, result.returncode, result.stderr.decode())
        self.assertTrue(self.latest()['backends']['claude']['activity']['completion_received'])
        result = self.analyze({'FAKE_STREAM_MODE': 'missing-result'}, extra)
        self.assertEqual(1, result.returncode)
        backend = self.latest()['backends']['claude']
        self.assertFalse(backend['activity']['completion_received'])
        self.assertGreater(backend['partial_report']['saved_bytes'], 0)
        self.assertNotIn('verdict', backend)


if __name__ == '__main__':
    unittest.main()
