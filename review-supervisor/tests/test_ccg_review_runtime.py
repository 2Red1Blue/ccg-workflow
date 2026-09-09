"""Subprocess regressions for stream collection, timeout classification and retries."""
import importlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

import test_ccg_agent_supervisor as harness

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ccg_review_runtime import ReviewActivity, collect_claude_events, observe_pipe


FAKE_CLAUDE = r'''#!/usr/bin/env python3
import json, os, sys, time
from pathlib import Path
args = sys.argv[1:]
if args == ['--help']:
 print('--output-format --include-partial-messages --strict-mcp-config --mcp-config --setting-sources --system-prompt --verbose --tools --effort --model --disable-slash-commands')
 raise SystemExit(0)
if args == ['--version']:
 print('Claude fake 1.0')
 raise SystemExit(0)
sync = Path(os.environ['TEST_SYNC_DIR'])
(sync / 'claude.started').write_text('started')
(sync / 'claude.args').write_text(json.dumps(args))
sys.stdin.read()
def emit(e):
 print(json.dumps(e), flush=True)
emit({'type':'system','subtype':'init','session_id':'fake-session','model':'alias'})
if os.environ.get('FAKE_STREAM_MODE') == 'init-heartbeat':
 for i in range(60):
  emit({'type':'system','subtype':'heartbeat','session_id':'fake-session'})
  time.sleep(.1)
 raise SystemExit(0)
emit({'type':'assistant','message':{'model':os.environ.get('FAKE_MODEL','actual-model'),'content':[]}})
emit({'type':'stream_event','event':{'type':'content_block_delta','delta':{'type':'text_delta','text':'Partial finding'}}})
mode = os.environ.get('FAKE_STREAM_MODE')
if mode == 'idle':
 time.sleep(20)
if mode == 'active':
 for i in range(60):
  emit({'type':'stream_event','event':{'type':'content_block_delta','delta':{'type':'thinking_delta','thinking':'PRIVATE REASONING'}}})
  time.sleep(.1)
if mode == 'heartbeat-success':
 for i in range(15):
  emit({'type':'stream_event','event':{'type':'content_block_delta','delta':{'type':'thinking_delta','thinking':'PRIVATE REASONING'}}})
  time.sleep(.1)
if mode == 'missing-result':
 raise SystemExit(0)
emit({'type':'result','subtype':'success','is_error':False,'result':'## Critical\nNone\n## Warning\nNone\n## Info\nFake stream review\n## Verdict\nAPPROVE'})
'''


class StreamReviewTest(unittest.TestCase):
    def setUp(self):
        self.h = harness.SupervisorReviewTest()
        self.h.setUp()
        self.claude = self.h._executable('fake-claude-stream', FAKE_CLAUDE)

    def tearDown(self):
        self.h.tearDown()

    def review(self, environment=None, extra=None, timeout=10):
        return self.h._review(environment=environment, timeout=timeout, extra_args=[
            '--claude-transport', 'stream', '--claude-cli', str(self.claude),
            '--claude-settings', str(self.h.root/'settings.json'), *(extra or [])])

    def test_stream_records_actual_model_and_final_separately(self):
        result = self.review(extra=['--claude-model','requested-alias'])
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        b = self.h._status()['backends']['claude']
        self.assertEqual(b['activity']['actual_models'], ['actual-model'])
        self.assertTrue(b['activity']['completion_received'])
        self.assertEqual(b['partial_report']['saved_bytes'], len('Partial finding'))
        self.assertFalse(b['model_verified'])
        args = json.loads((self.h.sync/'claude.args').read_text())
        self.assertEqual(args[args.index('--model')+1], 'requested-alias')
        self.assertEqual(args[args.index('--tools')+1], '')

    def test_model_mismatch_fails_even_if_result_arrives_before_poll(self):
        result = self.review(extra=['--expect-claude-model','wanted-model'])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.h._status()['backends']['claude']['state'], 'failed')

    def test_missing_result_is_not_success(self):
        result = self.review({'FAKE_STREAM_MODE':'missing-result'})
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.h._status()['backends']['claude']['activity']['completion_received'])

    def test_idle_timeout_preserves_codex_and_partial_answer(self):
        result = self.review({'FAKE_STREAM_MODE':'idle'}, ['--idle-timeout-seconds','1'])
        self.assertEqual(result.returncode, 124)
        b = self.h._status()['backends']
        self.assertEqual(b['claude']['termination_reason'], 'idle_timeout')
        self.assertEqual(b['codex']['state'], 'succeeded')
        self.assertGreater(b['claude']['partial_report']['saved_bytes'], 0)

    def test_thinking_only_stream_hits_progress_timeout_without_persisting_reasoning(self):
        result = self.review({'FAKE_STREAM_MODE':'heartbeat-success'}, ['--idle-timeout-seconds','0', '--thinking-timeout-seconds','1'])
        self.assertEqual(result.returncode, 124)
        self.assertEqual(self.h._status()['backends']['claude']['termination_reason'], 'progress_timeout')
        directory = next(self.h.run_root.glob('*/status.json')).parent
        for p in directory.iterdir():
            if p.is_file():
                self.assertNotIn(b'PRIVATE REASONING', p.read_bytes())

    def test_initialized_heartbeat_stream_hits_progress_timeout(self):
        result = self.review({'FAKE_STREAM_MODE':'init-heartbeat'}, ['--idle-timeout-seconds','0', '--thinking-timeout-seconds','1'])
        self.assertEqual(result.returncode, 124)
        backend = self.h._status()['backends']['claude']
        self.assertEqual(backend['termination_reason'], 'progress_timeout')
        self.assertFalse(backend['activity']['completion_received'])

    def test_active_generation_still_has_hard_deadline(self):
        result = self.review({'FAKE_STREAM_MODE':'active'}, ['--idle-timeout-seconds','1'], timeout=2)
        self.assertEqual(result.returncode, 124)
        self.assertEqual(self.h._status()['backends']['claude']['termination_reason'], 'hard_deadline')

    def test_retry_reuses_successful_backend_only(self):
        first = self.review({'FAKE_STREAM_MODE':'missing-result'})
        self.assertEqual(first.returncode, 1)
        status = self.h._status()
        stamp = (self.h.sync/'codex.started').stat().st_mtime_ns
        second = self.review(extra=['--retry-run',status['run_id']])
        self.assertEqual(second.returncode, 0, second.stderr.decode())
        self.assertEqual(stamp, (self.h.sync/'codex.started').stat().st_mtime_ns)
        statuses = [json.loads(p.read_text()) for p in self.h.run_root.glob('*/status.json')]
        current = next(s for s in statuses if s['run_id'] != status['run_id'])
        self.assertEqual(current['backends']['codex']['reused_from_run'], status['run_id'])

    def test_retry_rejects_changed_input(self):
        self.review({'FAKE_STREAM_MODE':'missing-result'})
        rid = self.h._status()['run_id']
        self.h.patch.write_text('different patch')
        result = self.review(extra=['--retry-run',rid])
        self.assertEqual(result.returncode, 1)
        statuses = [json.loads(p.read_text()) for p in self.h.run_root.glob('*/status.json')]
        current = next(s for s in statuses if s['run_id'] != rid)
        self.assertIn('patch_sha256', current['supervisor_error'])

    def test_settings_credentials_are_forwarded_without_hooks_or_leaking(self):
        settings = self.h.root/'settings.json'
        settings.write_text(json.dumps({'env':{'ANTHROPIC_AUTH_TOKEN':'fixture-only-secret', 'UNRELATED_VALUE':'not-forwarded'}, 'hooks':{'invalid':'must-not-load'}}))
        self.claude.write_text(FAKE_CLAUDE.replace("sys.stdin.read()", "assert os.environ.get('ANTHROPIC_AUTH_TOKEN') == 'fixture-only-secret'\nassert 'UNRELATED_VALUE' not in os.environ\nsys.stdin.read()"))
        result = self.review()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertNotIn(b'fixture-only-secret', result.stdout+result.stderr)
        self.assertNotIn('fixture-only-secret',json.dumps(self.h._status()))

    def test_retry_refuses_changed_routing(self):
        self.review({'FAKE_STREAM_MODE':'missing-result'})
        rid = self.h._status()['run_id']
        (self.h.root/'settings.json').write_text(json.dumps({'env':{'ANTHROPIC_BASE_URL':'https://different.invalid'}}))
        result = self.review(extra=['--retry-run',rid])
        self.assertEqual(result.returncode, 1)

    def test_retry_refuses_changed_api_timeout(self):
        self.review({'FAKE_STREAM_MODE':'missing-result'})
        rid = self.h._status()['run_id']
        result = self.review({'API_TIMEOUT_MS':'12345'}, extra=['--retry-run',rid])
        self.assertEqual(result.returncode, 1)
        statuses = [json.loads(p.read_text()) for p in self.h.run_root.glob('*/status.json')]
        current = next(s for s in statuses if s['run_id'] != rid)
        self.assertIn('review_policy', current['supervisor_error'])

    def test_retry_refuses_tampered_report(self):
        self.review({'FAKE_STREAM_MODE':'missing-result'})
        rid = self.h._status()['run_id']
        report = self.h.run_root/rid/'codex.report.md'
        report.write_text(report.read_text().replace('Fake Codex', 'Altered'))
        result = self.review(extra=['--retry-run',rid])
        self.assertEqual(result.returncode, 1)

    def test_preflight_timeout_is_classified_as_timeout(self):
        self.h.codex.write_text(harness.FAKE_CODEX.replace('if args == ["exec", "--help"]:', 'if args == ["exec", "--help"]:\n    time.sleep(5)'))
        result = self.review(timeout=1)
        self.assertEqual(result.returncode, 124)
        self.assertEqual(self.h._status()['state'], 'timed_out')

    def test_preflight_checks_every_used_isolation_flag(self):
        self.claude.write_text(FAKE_CLAUDE.replace('--setting-sources', '--absent-setting-sources'))
        # Avoid substring false positives in the fake help.
        self.claude.write_text(self.claude.read_text().replace('--absent-setting-sources', '--absent'))
        result = self.review()
        self.assertEqual(result.returncode, 1)
        self.assertIn('--setting-sources', self.h._status()['supervisor_error'])
        self.assertFalse((self.h.sync/'codex.started').exists())

    def test_symlinked_settings_are_supported(self):
        target = self.h.root/'dotfiles.json'
        target.write_text(json.dumps({'env':{'ANTHROPIC_MODEL':'requested'}}))
        (self.h.root/'settings.json').symlink_to(target)
        result = self.review()
        self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_snapshot_contains_new_file_and_preserves_index(self):
        repo = self.h._git_repository()
        (repo/'new file.txt').write_text('new\n')
        (repo/'tracked.txt').write_text('unstaged\n')
        before = (repo/'.git/index').read_bytes()
        # Capture the exact input inside the fake reviewer, before bundle cleanup.
        original = self.h.codex.read_text()
        self.h.codex.write_text(original.replace('print("## Critical',
            '(sync / "captured.patch").write_bytes(Path("CHANGES.patch").read_bytes())\nprint("## Critical'))
        result = self.h._git_review(repo, extra_args=['--snapshot-base','HEAD','--include-untracked'])
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        patch = (self.h.sync/'captured.patch').read_text()
        self.assertIn('+new', patch)
        self.assertIn('+unstaged', patch)
        self.assertEqual((repo/'.git/index').read_bytes(), before)

    def test_snapshot_refuses_untracked_without_opt_in(self):
        repo = self.h._git_repository()
        (repo/'new.txt').write_text('new')
        result = self.h._git_review(repo, extra_args=['--snapshot-base','HEAD'])
        self.assertEqual(result.returncode, 1)
        self.assertIn('--include-untracked', self.h._status()['supervisor_error'])


class EventParserTest(unittest.TestCase):
    def test_pipe_read_failure_is_recorded(self):
        class BrokenPipe:
            def read(self, size):
                raise OSError('fixture failure')
        activity = ReviewActivity()
        observe_pipe(BrokenPipe(), io.BytesIO(), activity)
        self.assertEqual(activity.snapshot()['protocol_error'], 'OSError')

    def test_oversized_line_is_bounded_and_fails_closed(self):
        activity = ReviewActivity()
        report, partial = io.BytesIO(), io.BytesIO()
        collect_claude_events(io.BytesIO(b'x'*10000+b'\n'), report, partial, activity, 256)
        self.assertEqual(activity.snapshot()['protocol_error'], 'stream_event_too_large')

    def test_assistant_text_is_not_duplicated_after_deltas(self):
        events = [
            {'type':'stream_event','event':{'type':'content_block_delta','delta':{'type':'text_delta','text':'answer'}}},
            {'type':'assistant','message':{'model':'model','content':[{'type':'text','text':'answer'}]}},
        ]
        activity = ReviewActivity()
        partial = io.BytesIO()
        collect_claude_events(io.BytesIO(b'\n'.join(json.dumps(e).encode() for e in events)), io.BytesIO(), partial, activity)
        self.assertEqual(partial.getvalue(), b'answer')

    def test_error_result_never_becomes_success_report(self):
        activity = ReviewActivity()
        report = io.BytesIO()
        raw = json.dumps({'type':'result','subtype':'error_during_execution','is_error':True,'result':'APPROVE'}).encode()
        collect_claude_events(io.BytesIO(raw), report, io.BytesIO(), activity)
        self.assertEqual(report.getvalue(), b'')
        self.assertEqual(activity.snapshot()['protocol_error'], 'upstream_result_error')

    def test_synthetic_model_is_not_reported_as_actual(self):
        activity = ReviewActivity()
        activity.observe_model('<synthetic>')
        self.assertEqual(activity.snapshot()['actual_models'], [])

    def test_model_set_is_bounded(self):
        activity = ReviewActivity()
        for i in range(100):
            activity.observe_model(str(i))
        self.assertEqual(len(activity.snapshot()['actual_models']), 16)
        self.assertEqual(activity.snapshot()['protocol_error'], 'too_many_response_models')

    def test_duplicate_result_does_not_deadlock_or_duplicate_report(self):
        event = json.dumps({'type':'result','subtype':'success','result':'report'}).encode()
        activity = ReviewActivity()
        report = io.BytesIO()
        collect_claude_events(io.BytesIO(event+b'\n'+event), report, io.BytesIO(), activity)
        self.assertEqual(report.getvalue(), b'report')
        self.assertEqual(activity.snapshot()['protocol_error'], 'invalid_stream_event:ValueError')

    def test_fallback_preserves_all_text_blocks(self):
        event = {'type':'assistant','message':{'content':[{'type':'text','text':'one'},{'type':'text','text':'two'}]}}
        activity = ReviewActivity()
        partial = io.BytesIO()
        collect_claude_events(io.BytesIO(json.dumps(event).encode()), io.BytesIO(), partial, activity)
        self.assertEqual(partial.getvalue(), b'onetwo')


if __name__ == '__main__':
    unittest.main()
