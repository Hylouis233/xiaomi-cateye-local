"""One explicitly authorized wake for the dynamic-port comparison."""
import asyncio
import json
import os
import re
from pathlib import Path
import sys
import time

os.umask(0o077)
sys.path.insert(0, '/config/cateye_local')
import video_probe

async def main():
    label = sys.argv[1] if len(sys.argv) > 1 else 'cloud_wake_once'
    assert re.fullmatch(r'[a-z0-9_]{1,60}', label)
    path = Path('/config/cateye_local/research') / (label + '_receipt.json')
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        old = json.loads(path.read_text())
        print(json.dumps({'repeated_wake_refused': True, 'previous_receipt': old}), flush=True)
        return
    report = {'at': time.time(), 'phase': 'claimed_before_send',
              'purpose': label, 'cloud_wake_calls': 1,
              'cloud_key_refresh_calls': 0, 'stream_started': False}
    with os.fdopen(fd, 'w') as f:
        json.dump(report, f)
    start = time.monotonic()
    try:
        report['accepted'] = bool(await asyncio.wait_for(video_probe.cloud_wake(), 12))
        report['phase'] = 'response_received'
    except Exception as error:
        report['phase'] = 'error'
        report['error'] = type(error).__name__
    finally:
        report['elapsed_ms'] = round(1000 * (time.monotonic() - start))
        path.write_text(json.dumps(report, indent=2))
    print(json.dumps(report), flush=True)

asyncio.run(main())
