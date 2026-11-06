"""Passive classification only; retain no TCP application payload."""
import json
import os
from pathlib import Path
import select
import socket
import struct
import time
import sys

os.umask(0o077)
duration = int(sys.argv[1]) if len(sys.argv) > 1 else 50
assert 1 <= duration <= 55
name = sys.argv[2] if len(sys.argv) > 2 else 'cloud_transport_classification.json'
assert '/' not in name and name.endswith('.json')
deadline = time.monotonic() + duration
report = {'at': time.time(), 'duration_s': duration, 'device_requests_sent': 0,
          'application_payload_retained': False, 'data_packets': 0, 'tls_headers': 0,
          'miio_headers': 0, 'samples': []}
def read_exact(n):
    value = b''
    while len(value) < n:
        left = deadline - time.monotonic()
        if left <= 0 or not select.select([0], [], [], left)[0]:
            return None
        part = os.read(0, n - len(value))
        if not part:
            return None
        value += part
    return value
header = read_exact(24)
if header:
    endian = '<' if header[:4] == b'\xd4\xc3\xb2\xa1' else '>'
    while time.monotonic() < deadline:
        record = read_exact(16)
        if not record:
            break
        sec, usec, length, wire = struct.unpack(endian + 'IIII', record)
        if length > 65536:
            break
        frame = read_exact(length)
        if not frame or len(frame) < 54:
            continue
        off = 18 if frame[12:14] == b'\x81\x00' else 14
        ip = frame[off:]
        if len(ip) < 40 or ip[0] >> 4 != 4 or ip[9] != 6:
            continue
        ihl = (ip[0] & 15) * 4
        tcp = ip[ihl:]
        thl = (tcp[12] >> 4) * 4
        payload_size = int.from_bytes(ip[2:4], 'big') - ihl - thl
        payload = tcp[thl:]
        if payload_size <= 0:
            continue
        report['data_packets'] += 1
        kind = 'unknown_fragment'
        if len(payload) >= 5 and payload[0] in (20, 21, 22, 23) and payload[1] == 3 and payload[2] in (1, 2, 3, 4):
            kind = 'TLS_record'
            report['tls_headers'] += 1
        elif payload.startswith(b'\x21\x31'):
            kind = 'miIO_envelope'
            report['miio_headers'] += 1
            try:
                from miio.miioprotocol import Message
                private = Path('/config/.storage/cateye_local')
                token = json.loads((private / 'miio.json').read_text())['token']
                packet_length = int.from_bytes(payload[2:4], 'big')
                if len(payload) >= packet_length:
                    decoded = Message.parse(payload[:packet_length], token=bytes.fromhex(token)).data.value
                    method = decoded.get('method') if isinstance(decoded, dict) else None
                    report.setdefault('decoded_method_names', []).append(method)
                    p = decoded.get('params') if isinstance(decoded, dict) else None
                    allowed = method in ('wakeup', 'wakeup_host') or (method == 'action' and isinstance(p, dict) and p.get('siid') == 2 and p.get('aiid') in (2, 3))
                    if allowed:
                        directory = private / 'research'
                        directory.mkdir(mode=0o700, exist_ok=True)
                        (directory / 'cloud_wake_local_replay_candidate.json').write_text(json.dumps(decoded))
                        report['local_replay_candidate_saved'] = True
            except Exception as error:
                report['miio_decode_error'] = type(error).__name__
        if len(report['samples']) < 20:
            report['samples'].append({'at': sec + usec / 1000000, 'kind': kind,
                                      'payload_length': payload_size,
                                      'direction': 'camera_to_cloud' if socket.inet_ntoa(ip[12:16]) == '192.168.1.210' else 'cloud_to_camera'})
Path('/config/cateye_local/research', name).write_text(json.dumps(report, indent=2))
print(json.dumps(report), flush=True)
