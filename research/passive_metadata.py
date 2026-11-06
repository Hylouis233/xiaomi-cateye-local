import ipaddress
import json
import os
from pathlib import Path
import re
import select
import struct
import sys
import time

os.umask(0o077)
duration = int(sys.argv[1])
assert 1 <= duration <= 55
deadline = time.monotonic() + duration
started = time.time()
out_path = Path('/config/cateye_local/research') / sys.argv[2]
seed_path = Path('/config/.storage/cateye_local/research/observed_cs2_seeds.json')
seeds = []

def publish_live():
    report = {'at': started, 'duration_s': duration, 'camera_packets': packets,
              'probe_sent': False, 'rows': list(rows.values())}
    out_path.write_text(json.dumps(report, indent=2))

def inspect_cs2(data, source_port, server_ip, server_port):
    if len(data) < 24 or data[0] != 0xf1 or server_port != 32100:
        return
    for offset in range(4, min(len(data) - 19, 49)):
        candidate = data[offset:offset + 20]
        prefix, suffix = candidate[:8].rstrip(b'\0'), candidate[12:20].rstrip(b'\0')
        serial = int.from_bytes(candidate[8:12], 'big')
        if not re.fullmatch(b'[A-Z0-9_]{3,8}', prefix) or not re.fullmatch(b'[A-Z0-9]{3,8}', suffix) or not serial:
            continue
        uid = prefix.decode() + '-' + str(serial) + '-' + suffix.decode()
        if any(x['p2p_id'] == uid and x['server_ip'] == server_ip and x['local_port'] == source_port for x in seeds):
            continue
        if len(seeds) < 20:
            seeds.append({'p2p_id': uid, 'local_port': source_port, 'server_ip': server_ip,
                          'at': time.time(), 'kind': hex(data[1]), 'payload_offset': offset,
                          'validation': 'wire_format_candidate_pending_handshake'})
            seed_path.parent.mkdir(mode=0o700, exist_ok=True)
            seed_path.write_text(json.dumps(seeds, indent=2))

def read_exact(n):
    result = b''
    while len(result) < n:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([0], [], [], remaining)[0]:
            return None
        part = os.read(0, n - len(result))
        if not part:
            return None
        result += part
    return result

header = read_exact(24)
rows = {}
packets = 0
if header:
    endian = '<' if header[:4] in (b'\xd4\xc3\xb2\xa1', b'\x4d\x3c\xb2\xa1') else '>'
    while time.monotonic() < deadline:
        h = read_exact(16)
        if not h:
            break
        sec, usec, captured, wire = struct.unpack(endian + 'IIII', h)
        if captured > 65536:
            break
        frame = read_exact(captured)
        if not frame or len(frame) < 34:
            continue
        offset = 18 if frame[12:14] == b'\x81\x00' else 14
        ip = frame[offset:]
        if len(ip) < 20 or ip[0] >> 4 != 4:
            continue
        src = str(ipaddress.ip_address(ip[12:16]))
        dst = str(ipaddress.ip_address(ip[16:20]))
        if src != '192.168.1.210' and dst != '192.168.1.210':
            continue
        ihl = (ip[0] & 15) * 4
        transport = ip[ihl:]
        if len(transport) < 8 or ip[9] not in (6, 17):
            continue
        sport, dport = struct.unpack('!HH', transport[:4])
        proto = 'udp' if ip[9] == 17 else 'tcp'
        payload = transport[8:] if proto == 'udp' else b''
        kind = hex(payload[1]) if len(payload) >= 4 and payload[0] == 0xf1 else None
        packets += 1
        key = (src, dst, sport, dport, proto, kind)
        if key not in rows and len(rows) < 160:
            rows[key] = {'src': src, 'dst': dst, 'sport': sport, 'dport': dport,
                         'proto': proto, 'cs2_kind': kind, 'count': 0,
                         'first_at': sec + usec / 1000000}
            new_row = True
        else:
            new_row = False
        if key in rows:
            rows[key]['count'] += 1
            rows[key]['last_at'] = sec + usec / 1000000
        if src == '192.168.1.210' and proto == 'udp':
            inspect_cs2(payload, sport, dst, dport)
        if new_row:
            publish_live()

report = {'at': started, 'duration_s': duration, 'camera_packets': packets,
          'cloud_wake_sent': False, 'probe_sent': False, 'rows': list(rows.values())}
out_path.write_text(json.dumps(report, indent=2))
print(json.dumps({'saved': str(out_path), 'camera_packets': packets, 'uid_candidates': len(seeds), 'rows': list(rows.values())[:16]}), flush=True)
