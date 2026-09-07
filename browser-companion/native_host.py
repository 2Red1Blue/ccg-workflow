#!/usr/bin/env python3
"""Chrome Native Messaging host: sends only validated live loopback task URLs."""
import json
import os
from pathlib import Path
import re
import select
import struct
import sys
import time
from urllib.parse import urlsplit


def task_directory():
    if sys.platform == 'darwin':
        root = Path.home()/'Library/Application Support'
    else:
        root = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home()/'.config')))
    return root/'codeagent-wrapper/webui-v1'


def snapshot(directory, now):
    tasks = []
    try:
        entries = os.scandir(directory)
    except FileNotFoundError:
        return tasks
    with entries:
        for index, entry in enumerate(entries):
            if index >= 512: break
            if not entry.is_file(follow_symlinks=False) or not re.fullmatch(r'[a-f0-9]{32}\.json', entry.name): continue
            try:
                fd = os.open(entry.path, os.O_RDONLY | os.O_NOFOLLOW)
                with os.fdopen(fd, 'rb') as stream:
                    data = json.loads(stream.read(16385))
                identity = entry.name[:-5]
                url = urlsplit(data['url'])
                if data.get('id') != identity or not 0 <= now - data['updatedAt'] <= 10: continue
                if url.scheme != 'http' or url.hostname != '127.0.0.1' or not url.port: continue
                if url.username or url.password or url.path != '/' or url.query or url.fragment != 'ccg-task='+identity: continue
                tasks.append({'id': identity, 'url': data['url']})
            except (OSError, ValueError, KeyError, TypeError):
                continue
    return tasks


def main():
    while True:
        try:
            message = {'type': 'snapshot', 'tasks': snapshot(task_directory(), time.time())}
        except OSError:
            message = {'type':'unavailable'} # A transient read error is not task completion.
        encoded = json.dumps(message).encode()
        try:
            sys.stdout.buffer.write(struct.pack('<I', len(encoded)) + encoded)
            sys.stdout.buffer.flush()
        except (BrokenPipeError, OSError):
            return
        readable, _, _ = select.select([sys.stdin], [], [], 1)
        if readable and not os.read(sys.stdin.fileno(), 4096): return


if __name__ == '__main__':
    main()
