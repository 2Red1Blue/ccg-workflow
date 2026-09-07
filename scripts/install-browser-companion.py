#!/usr/bin/env python3
"""Install native host + persistent unpacked extension files from committed Git source."""
import base64
import hashlib
import json
from pathlib import Path
import os
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    if subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT).strip():
        raise RuntimeError('commit source before installing the browser companion')
    source = ROOT/'browser-companion'
    manifest = json.loads((source/'manifest.json').read_text())
    digest = hashlib.sha256(base64.b64decode(manifest['key'])).hexdigest()[:32]
    extension_id = ''.join(chr(ord('a')+int(c,16)) for c in digest)
    install = Path.home()/'.local/share/ccg/browser-companion'
    install.mkdir(parents=True, exist_ok=True)
    for name in ['manifest.json','background.js','tabs.js']:
        shutil.copyfile(source/name, install/name)
    host = install/'native_host.py'
    body = (source/'native_host.py').read_text().split('\n',1)[1]
    host.write_text('#!'+sys.executable+'\n'+body)
    host.chmod(0o700)
    if sys.platform == 'darwin':
        native_dir = Path.home()/'Library/Application Support/Google/Chrome/NativeMessagingHosts'
    elif sys.platform.startswith('linux'):
        native_dir = Path.home()/'.config/google-chrome/NativeMessagingHosts'
    else:
        raise RuntimeError('native-host installer currently supports macOS and Linux')
    native_dir.mkdir(parents=True, exist_ok=True)
    native_manifest = {'name':'com.ccg.webui','description':'CCG task-owned background tabs',
                       'path':str(host),'type':'stdio','allowed_origins':['chrome-extension://'+extension_id+'/']}
    target = native_dir/'com.ccg.webui.json'
    target.write_text(json.dumps(native_manifest, indent=2)+'\n')
    target.chmod(0o600)
    print(json.dumps({'extensionPath':str(install),'extensionId':extension_id,'nativeHostManifest':str(target)}, indent=2))


if __name__ == '__main__': main()
