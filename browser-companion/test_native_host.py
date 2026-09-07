import importlib.util
import json
from pathlib import Path
import tempfile
import time
import unittest

spec=importlib.util.spec_from_file_location('host',Path(__file__).with_name('native_host.py'))
host=importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)

class HostTest(unittest.TestCase):
    def test_accepts_only_live_loopback_lease(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); identity='a'*32; now=time.time()
            record={'id':identity,'url':f'http://127.0.0.1:1234/#ccg-task={identity}','updatedAt':now}
            path=root/(identity+'.json');path.write_text(json.dumps(record))
            self.assertEqual(len(host.snapshot(root,now)),1)
            self.assertEqual(host.snapshot(root,now+11),[])
            record['url']='http://example.com:1234/'
            path.write_text(json.dumps(record))
            self.assertEqual(host.snapshot(root,now),[])
    def test_ignores_symlinks_and_invalid_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'bad').write_text('not json')
            (root/('a'*32+'.json')).symlink_to(root/'bad')
            self.assertEqual(host.snapshot(root,time.time()),[])

if __name__=='__main__':unittest.main()
