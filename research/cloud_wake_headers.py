"""Prearmed header-only capture; one wake and one key provision, no video."""
import asyncio
import json
import os
from pathlib import Path
import select
import socket
import struct
import sys
import time

os.umask(0o077)
ROOT = Path('/config/cateye_local/research')
PRIVATE = Path('/config/.storage/cateye_local/research')
label = sys.argv[1] if len(sys.argv) > 1 else 'cloud_wake_headers'
assert label.replace('_', '').isalnum()
receipt = ROOT / (label + '.json')
if receipt.exists():
    print(json.dumps({'repeat_refused': True}), flush=True)
    raise SystemExit(0)
deadline = time.monotonic() + 30
def read_exact(n):
    out = b''
    while len(out) < n:
        left = deadline - time.monotonic()
        if left <= 0 or not select.select([0], [], [], left)[0]:
            return None
        data = os.read(0, n - len(out))
        if not data:
            return None
        out += data
    return out
header = read_exact(24)
assert header
endian = '<' if header[:4] == b'\xd4\xc3\xb2\xa1' else '>'
report = {'at': time.time(), 'phase': 'claimed_before_wake', 'cloud_wake_calls': 1,
          'video_requested': False, 'packets': [], 'private_candidate_count': 0, 'control_payloads': []}
receipt.write_text(json.dumps(report, indent=2))
sys.path.insert(0, '/config/cateye_local')
import video_probe
started = time.monotonic()
report['wake_started_at'] = time.time()
async def trigger():
    report['wake_accepted'] = bool(await video_probe.cloud_wake())
    report['wake_ms'] = round(1000 * (time.monotonic() - started))
    if report['wake_accepted'] and '--no-provision' not in sys.argv:
        await asyncio.sleep(3)
        mark = time.monotonic()
        result = await video_probe.main(force_fresh=True)
        report['key_provision_ready'] = bool(result and result.get('ready'))
        report['key_provision_ms'] = round(1000 * (time.monotonic() - mark))
asyncio.run(trigger())
candidates = []
payloads = []
while time.monotonic() < deadline:
    h = read_exact(16)
    if not h:
        break
    sec, usec, size, wire = struct.unpack(endian + 'IIII', h)
    if size > 65536:
        break
    frame = read_exact(size)
    if not frame or len(frame) < 54:
        continue
    off = 18 if frame[12:14] == b'\x81\x00' else 14
    ip = frame[off:]
    if len(ip) < 40 or ip[0] >> 4 != 4 or ip[9] != 6:
        continue
    ihl = (ip[0] & 15) * 4
    tcp = ip[ihl:]
    thl = (tcp[12] >> 4) * 4
    src, dst = socket.inet_ntoa(ip[12:16]), socket.inet_ntoa(ip[16:20])
    sport, dport = struct.unpack('!HH', tcp[:4])
    length = int.from_bytes(ip[2:4], 'big') - ihl - thl
    flags = tcp[13]
    row = {'at': sec + usec / 1000000, 'from_camera': src == '192.168.1.210',
           'peer_ip': dst if src == '192.168.1.210' else src,
           'sport': sport, 'dport': dport, 'flags': hex(flags), 'payload_length': length,
           'ip_total_length': int.from_bytes(ip[2:4], 'big'), 'tcp_header_length': thl,
           'capture_length': len(frame), 'window': int.from_bytes(tcp[14:16], 'big'),
           'urgent_pointer': int.from_bytes(tcp[18:20], 'big')}
    if len(report['packets']) < 80:
        report['packets'].append(row)
    if dst == '192.168.1.210' and sport == 443 and length > 0 and sec + usec / 1000000 <= report['wake_started_at'] + 4:
        payload = tcp[thl:thl + length]
        kind = 'unknown'
        if len(payload) >= 5 and payload[0] in (20, 21, 22, 23) and payload[1] == 3:
            kind = 'TLS_record'
        elif payload.startswith(b'\x21\x31'):
            kind = 'miIO_envelope'
        elif payload.lstrip().startswith(b'{'):
            kind = 'plain_JSON'
        summary = {'at': row['at'], 'kind': kind, 'bytes': len(payload), 'complete': len(payload) == length}
        if len(payloads) < 6:
            payloads.append({'at': row['at'], 'src': src, 'sport': sport, 'dport': dport, 'payload_hex': payload.hex()})
            report['control_payloads'].append(summary)
        if kind in ('miIO_envelope', 'plain_JSON') and len(payload) == length:
            try:
                if kind == 'miIO_envelope':
                    from miio.miioprotocol import Message
                    device = json.loads(Path('/config/.storage/cateye_local/miio.json').read_text())
                    decoded = Message.parse(payload, token=bytes.fromhex(device['token'])).data.value
                else:
                    decoded = json.loads(payload)
                method = decoded.get('method') if isinstance(decoded, dict) else None
                summary['decoded_method'] = method
                params = decoded.get('params') if isinstance(decoded, dict) else None
                allowed = method in ('wakeup', 'wakeup_host') or (method == 'action' and isinstance(params, dict) and params.get('siid') == 2 and params.get('aiid') in (2, 3))
                if allowed:
                    PRIVATE.mkdir(mode=0o700, exist_ok=True)
                    (PRIVATE / 'cloud_wake_local_replay_candidate.json').write_text(json.dumps(decoded))
                    summary['known_wake_candidate_saved'] = True
            except Exception as error:
                summary['decode_error'] = type(error).__name__
    if dst == '192.168.1.210' and sport == 443 and length == 0 and frame[:6] == bytes.fromhex('78be81584ace'):
        candidates.append({'at': row['at'], 'flags': flags, 'frame_hex': frame[:off + int.from_bytes(ip[2:4], 'big')].hex(),
                           'src': src, 'dst': dst, 'sport': sport, 'dport': dport,
                           'seq': int.from_bytes(tcp[4:8], 'big'), 'ack': int.from_bytes(tcp[8:12], 'big')})
PRIVATE.mkdir(mode=0o700, exist_ok=True)
(PRIVATE / 'cloud_wake_header_candidates.json').write_text(json.dumps(candidates, indent=2))
(PRIVATE / (label + '_payloads.json')).write_text(json.dumps(payloads, indent=2))
report['private_candidate_count'] = len(candidates)
report['phase'] = 'finished'
receipt.write_text(json.dumps(report, indent=2))
print(json.dumps(report), flush=True)
