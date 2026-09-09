from __future__ import annotations

import fcntl
import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ccg_review_web import History, ReviewServer

ID = '11111111-1111-1111-1111-111111111111'
OTHER = '22222222-2222-2222-2222-222222222222'


class HistoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.run = self.root / ID
        self.run.mkdir()
        self.meta = {'mode': 'dual_leaf_review', 'state': 'succeeded', 'workdir': '/work/demo',
                     'started_at_epoch': 100, 'finished_at_epoch': 110,
                     'backends': {n: {'state': 'succeeded', 'verdict': 'APPROVE'} for n in ('codex', 'claude')}}
        self.history = History(self.root)
        self.save()

    def save(self):
        (self.run / 'status.json').write_text(json.dumps(self.meta))

    def test_execution_success_does_not_imply_approval(self):
        self.assertEqual(self.history.query(ID)['verdict'], 'approved')
        self.meta['backends']['codex']['verdict'] = 'REQUEST_CHANGES'
        self.save()
        row = self.history.query(ID)
        self.assertEqual(row['state'], 'succeeded')
        self.assertEqual(row['verdict'], 'changes')
        self.meta['backends']['codex'] = {'state': 'failed', 'verdict': None}
        self.save()
        self.assertEqual(self.history.query(ID)['verdict'], 'incomplete')

    def test_analysis_is_visible_without_claiming_review_approval(self):
        self.meta['mode'] = 'dual_leaf_analysis'
        self.meta['context_bytes'] = 2048
        for row in self.meta['backends'].values():
            row['verdict'] = None
        self.save()
        result = self.history.query(ID)
        self.assertEqual(result['mode'], 'dual_leaf_analysis')
        self.assertEqual(result['verdict'], 'analyzed')
        self.assertEqual(result['patch_bytes'], 2048)
        self.meta['backends']['claude']['state'] = 'failed'
        self.save()
        self.assertEqual(self.history.query(ID)['verdict'], 'incomplete')

    def test_actual_model_partial_report_and_no_arbitrary_files(self):
        self.meta['backends']['claude']['activity'] = {'actual_models': ['glm-5-3-flash']}
        self.meta['preflight'] = {'codex_review_model': 'gpt-example', 'api_key': 'PRIVATE'}
        self.meta['secret'] = 'PRIVATE'
        self.save()
        (self.run / 'claude.partial.md').write_text('Bearer authentication. Partial <script>bad()</script> Bearer topsecret123topsecret1234567')
        (self.run / 'codex.stderr.log').write_text('PRIVATE')
        row = self.history.query(ID)
        self.assertTrue(row['backends']['claude']['partial'])
        self.assertEqual(row['backends']['claude']['models'], ['glm-5-3-flash'])
        self.assertEqual(row['backends']['codex']['requested_model'], 'gpt-example')
        self.assertIn('[redacted]', row['backends']['claude']['report'])
        self.assertIn('Bearer authentication.', row['backends']['claude']['report'])
        self.assertNotIn('PRIVATE', json.dumps(row))
        self.assertNotIn('report', self.history.query()['runs'][0]['backends']['claude'])

    def test_traversal_symlinks_fifo_and_oversized_reports_are_not_read(self):
        (self.root / OTHER).symlink_to(self.run, target_is_directory=True)
        self.assertIsNone(self.history.query(OTHER))
        self.assertIsNone(self.history.query('../' + ID))
        (self.run / 'codex.report.md').symlink_to(self.run / 'status.json')
        os.mkfifo(self.run / 'claude.report.md')
        row = self.history.query(ID)
        self.assertEqual(row['backends']['codex']['report'], '')
        self.assertEqual(row['backends']['claude']['report'], '')
        (self.run / 'codex.report.md').unlink()
        (self.run / 'codex.report.md').write_bytes(b'x' * (2*1024*1024+1))
        self.assertEqual(self.history.query(ID)['backends']['codex']['report'], '')

    def test_running_lock_then_orphan_and_atomic_terminal_transition(self):
        (self.run / 'status.json').unlink()
        self.meta['state'] = 'running'
        (self.run / 'running.json').write_text(json.dumps(self.meta))
        with (self.run / 'run.lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.assertEqual(self.history.query(ID)['state'], 'running')
        self.assertEqual(self.history.query(ID)['state'], 'interrupted')
        self.meta['state'] = 'succeeded'
        self.save()
        self.assertEqual(self.history.query(ID)['state'], 'succeeded')

    def test_malformed_and_unrelated_runs_and_scan_cap(self):
        (self.run / 'status.json').write_text('{')
        self.assertEqual(self.history.query()['runs'], [])
        self.meta['mode'] = 'single'
        self.save()
        self.assertEqual(self.history.query()['runs'], [])
        self.assertTrue(History(self.root, scan_limit=0).query()['capped'])

    def test_extreme_numeric_and_invalid_state_do_not_break_history(self):
        self.meta['started_at_epoch'] = 10**400
        self.meta['state'] = []
        self.save()
        row = self.history.query()['runs'][0]
        self.assertEqual(row['started'], 0)
        self.assertEqual(row['state'], 'unknown')

    def test_http_fixed_address_host_origin_and_route_boundary(self):
        server = ReviewServer(self.root, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        def get(path, headers=None):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=2)
            try:
                connection.request('GET', path, headers=headers or {})
                response = connection.getresponse()
                return response.status, dict(response.getheaders()), response.read()
            finally:
                connection.close()
        code, headers, data = get('/api/runs')
        self.assertEqual(code, 200)
        self.assertEqual(len(json.loads(data)['runs']), 1)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertNotIn('Access-Control-Allow-Origin', headers)
        for extra in ({'Host':'attacker.example'}, {'Origin':'https://attacker.example'}, {'Sec-Fetch-Site':'cross-site'}, {'Sec-Fetch-Site':'same-site'}, {'Origin':'null'}):
            self.assertEqual(get('/api/runs', extra)[0], 403)
            self.assertEqual(get('/api/runs/' + ID, extra)[0], 403)
        self.assertEqual(get('/api/runs/../status.json')[0], 404)
        self.assertEqual(get('/api/runs/' + ID)[0], 200)
        for path in ('/', '/app.js', '/style.css'):
            code, headers, body = get(path)
            self.assertEqual(code, 200)
            self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
            self.assertIn("img-src 'self'", headers['Content-Security-Policy'])
        code, headers, body = get('/poster.webp')
        self.assertEqual(code, 200)
        self.assertEqual(headers['Content-Type'], 'image/webp')
        self.assertEqual(body[:4], b'RIFF')
        self.assertEqual(get('/ccg-agent-supervisor.py')[0], 404)


if __name__ == '__main__':
    unittest.main()
