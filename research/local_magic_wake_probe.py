"""Exploratory standard WoWLAN packet test; no cloud or setting changes."""
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import urllib.request

os.umask(0o077)
ROOT = Path('/config/cateye_local/research')
receipt = ROOT / 'local_magic_wake_probe.json'
assert not receipt.exists(), 'previous_receipt_exists_read_back_first'
with urllib.request.urlopen('http://127.0.0.1:11984/api/streams', timeout=2) as response:
    streams = json.load(response)
if any(p.get('medias') for v in streams.values() for p in v.get('producers') or []):
    raise RuntimeError('another_stream_active_no_wake_sent')
report = {'at': time.time(), 'hypothesis': 'standard_WoWLAN_magic_packet_not_model_documented',
          'cloud_called': False, 'persistent_settings_changed': False,
          'target_camera_mac': 'XX:XX:XX:XX:XX:XX', 'packets_sent': []}
receipt.write_text(json.dumps(report, indent=2))
mac = bytes.fromhex('78be81584ace')
magic = b'\xff' * 6 + mac * 16
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
try:
    for destination in ('192.168.1.255', '192.168.1.210'):
        s.sendto(magic, (destination, 9))
        report['packets_sent'].append({'kind': 'udp_magic', 'destination': destination, 'port': 9})
finally:
    s.close()
try:
    source = bytes.fromhex(Path('/sys/class/net/br-lan/address').read_text().strip().replace(':', ''))
    raw = socket.socket(socket.AF_PACKET, socket.SOCK_RAW)
    try:
        raw.bind(('br-lan', 0))
        raw.send(mac + source + b'\x08\x42' + magic)
        report['packets_sent'].append({'kind': 'ethernet_magic', 'ether_type': '0x0842'})
    finally:
        raw.close()
except OSError as error:
    report['ethernet_error'] = type(error).__name__
time.sleep(6)
trial = subprocess.run(['python3', '/config/cateye_local/pure_local_probe.py', 'awake_no_wake'],
                       capture_output=True, text=True, timeout=24)
report['local_media_exit_code'] = trial.returncode
try:
    report['local_media'] = json.loads(trial.stdout.strip().splitlines()[-1])
except (ValueError, IndexError):
    report['local_media_result_missing'] = True
receipt.write_text(json.dumps(report, indent=2))
print(json.dumps(report), flush=True)
