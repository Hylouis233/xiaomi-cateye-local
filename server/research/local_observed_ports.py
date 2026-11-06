import json
import os
from pathlib import Path
import select
import socket
import struct
import time

os.umask(0o077)
ROOT = Path('/config/cateye_local/research')
PRIVATE = Path('/config/.storage/cateye_local/research')
observed = json.loads((ROOT / 'passive_after_wake.json').read_text())
ports = sorted({r['sport'] for r in observed['rows'] if r['src'] == '192.168.1.210' and r['proto'] == 'udp' and r['dport'] == 32100})
assert ports and len(ports) <= 12
ports.append(32108)
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.bind(('192.168.1.1', 0))
s.setblocking(False)
mark = time.monotonic()
report = {'at': time.time(), 'cloud_called': False, 'media_requested': False, 'ports': ports, 'replies': []}
seeds = []
next_send = mark
try:
    while time.monotonic() < mark + 4:
        now = time.monotonic()
        if now >= next_send:
            for port in ports:
                s.sendto(b'\xf1\x30\0\0', ('192.168.1.210', port))
            next_send = now + 1
        if not select.select([s], [], [], .15)[0]:
            continue
        data, peer = s.recvfrom(1500)
        if peer[0] != '192.168.1.210' or len(data) < 4 or data[0] != 0xf1:
            continue
        kind = data[1]
        report['replies'].append({'ms': round(1000 * (time.monotonic() - mark)), 'port': peer[1], 'kind': hex(kind), 'length': len(data)})
        if kind == 0x41 and len(data) >= 24:
            raw = data[4:24]
            prefix, suffix = raw[:8].rstrip(b'\0'), raw[12:20].rstrip(b'\0')
            if prefix.isascii() and suffix.isascii() and prefix.isalnum() and suffix.isalnum():
                uid = prefix.decode() + '-' + str(int.from_bytes(raw[8:12], 'big')) + '-' + suffix.decode()
                seeds.append({'p2p_id': uid, 'local_port': peer[1], 'at': time.time(), 'validation': 'observed_lan_punch'})
                PRIVATE.mkdir(mode=0o700, exist_ok=True)
                (PRIVATE / 'observed_lan_punch_seeds.json').write_text(json.dumps(seeds, indent=2))
            s.sendto(data, peer)
        elif kind in (0x42, 0x43):
            report['ready_ms'] = round(1000 * (time.monotonic() - mark))
            report['ready_port'] = peer[1]
            s.sendto(b'\xf1\xf0\0\0', peer)
            break
finally:
    s.close()
report['elapsed_ms'] = round(1000 * (time.monotonic() - mark))
report['uid_observed'] = bool(seeds)
(ROOT / 'local_observed_ports.json').write_text(json.dumps(report, indent=2))
print(json.dumps(report), flush=True)
