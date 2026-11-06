"""Prearmed, one-wake comparison: act on observed ports immediately."""
import asyncio
import ipaddress
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
PRIVATE = Path('/config/.storage/cateye_local')
deadline = time.monotonic() + 28

def read_exact(n):
    result = b''
    while len(result) < n:
        remain = deadline - time.monotonic()
        if remain <= 0 or not select.select([0], [], [], remain)[0]:
            return None
        part = os.read(0, n - len(result))
        if not part:
            return None
        result += part
    return result

def message(kind, payload=b''):
    return struct.pack('!BBH', 0xf1, kind, len(payload)) + payload

def uid_bytes(uid):
    parts = uid.split('-')
    assert len(parts) == 3 and len(parts[0]) <= 8 and len(parts[2]) <= 8
    return parts[0].encode().ljust(8, b'\0') + struct.pack('!I', int(parts[1])) + parts[2].encode().ljust(8, b'\0')

header = read_exact(24)
assert header, 'capture_not_ready'
endian = '<' if header[:4] in (b'\xd4\xc3\xb2\xa1', b'\x4d\x3c\xb2\xa1') else '>'
cached = json.loads((PRIVATE / 'video_session.json').read_text())
did = uid_bytes(cached['response']['vendor']['vendor_params']['p2p_id'])
receipt = ROOT / 'armed_dynamic_probe.json'
try:
    fd = os.open(receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except FileExistsError:
    print(json.dumps({'repeat_refused': True, 'previous_receipt': str(receipt)}), flush=True)
    sys.exit(0)
report = {'at': time.time(), 'phase': 'armed_before_wake', 'cloud_wake_calls': 1,
          'cloud_key_refresh_calls': 0, 'media_requested': False,
          'local_observed_ports': [], 'local_probe_sends': [], 'local_replies': [],
          'external_coordination': [], 'observed_server_count': 0}
with os.fdopen(fd, 'w') as f:
    json.dump(report, f)
sys.path.insert(0, '/config/cateye_local')
import video_probe
mark = time.monotonic()
report['wake_at'] = time.time()
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.bind(('192.168.1.1', 0))
s.setblocking(False)
servers = set()
ports = set()
raw_controls = []
next_send = 0
next_external = 0

try:
    report['wake_accepted'] = bool(asyncio.run(asyncio.wait_for(video_probe.cloud_wake(), 12)))
    report['cloud_wake_ms'] = round(1000 * (time.monotonic() - mark))
    if not report['wake_accepted']:
        raise RuntimeError('cloud_wake_rejected')
    deadline = min(deadline, mark + 16)
    while time.monotonic() < deadline:
        now = time.monotonic()
        elapsed = round(1000 * (now - mark))
        if ports and now >= next_send:
            for port in ports | {32108}:
                s.sendto(message(0x30), ('192.168.1.210', port))
                s.sendto(message(0x41, did), ('192.168.1.210', port))
                if len(report['local_probe_sends']) < 48:
                    report['local_probe_sends'].append({'ms': elapsed, 'port': port})
            next_send = now + .7
        if elapsed >= 8500 and servers and now >= next_external and not report.get('ready_ms'):
            addr = b'\0\2' + struct.pack('<H', s.getsockname()[1]) + socket.inet_aton('192.168.1.1')[::-1] + bytes(8)
            for host in servers:
                s.sendto(message(0x20, did + addr), (host, 32100))
            next_external = now + 2
            report['external_coordination_started_ms'] = report.get('external_coordination_started_ms', elapsed)
        readable = select.select([0, s], [], [], min(.15, max(0, deadline - now)))[0]
        if s in readable:
            data, peer = s.recvfrom(1500)
            if len(data) >= 4 and data[0] == 0xf1:
                row = {'ms': round(1000 * (time.monotonic() - mark)), 'kind': hex(data[1]), 'port': peer[1], 'length': len(data)}
                if peer[0] == '192.168.1.210':
                    if len(report['local_replies']) < 100:
                        report['local_replies'].append(row)
                    if data[1] == 0x41:
                        raw_controls.append({'direction': 'camera_to_probe', 'port': peer[1], 'hex': data.hex()})
                        s.sendto(data, peer)
                    elif data[1] in (0x42, 0x43):
                        report['ready_ms'] = row['ms']
                        report['ready_port'] = peer[1]
                        s.sendto(message(0xf0), peer)
                        break
                elif peer[0] in servers:
                    if data[1] == 0x21:
                        row['status'] = data[4:8].hex()
                    elif data[1] == 0x40 and len(data) >= 20:
                        host = socket.inet_ntoa(data[8:12][::-1])
                        port = struct.unpack('<H', data[6:8])[0]
                        if host == '192.168.1.210' and port:
                            ports.add(port)
                        else:
                            row['wan_peer_rejected'] = True
                    if len(report['external_coordination']) < 60:
                        report['external_coordination'].append(row)
        if 0 in readable:
            h = read_exact(16)
            if not h:
                break
            sec, usec, length, wire = struct.unpack(endian + 'IIII', h)
            if length > 65536:
                break
            frame = read_exact(length)
            if not frame or len(frame) < 42:
                continue
            off = 18 if frame[12:14] == b'\x81\x00' else 14
            ip = frame[off:]
            if len(ip) < 28 or ip[9] != 17 or ip[0] >> 4 != 4:
                continue
            ihl = (ip[0] & 15) * 4
            udp = ip[ihl:]
            if len(udp) < 8:
                continue
            src = socket.inet_ntoa(ip[12:16])
            dst = socket.inet_ntoa(ip[16:20])
            sport, dport = struct.unpack('!HH', udp[:4])
            payload = udp[8:]
            if src == '192.168.1.210' and dport == 32100 and ipaddress.ip_address(dst).is_global:
                servers.add(dst)
                report['observed_server_count'] = len(servers)
                if sport not in ports:
                    ports.add(sport)
                    report['local_observed_ports'].append({'port': sport, 'ms': round((sec + usec / 1000000 - report['wake_at']) * 1000)})
                    next_send = 0
                if len(payload) >= 4 and payload[0] == 0xf1 and payload[1] in (0x0, 0x10, 0x12):
                    if len(raw_controls) < 30:
                        raw_controls.append({'direction': 'camera_to_server', 'port': sport, 'kind': hex(payload[1]), 'hex': payload.hex(), 'wire_udp_length': int.from_bytes(udp[4:6], 'big')})
    report['phase'] = 'finished'
except Exception as error:
    report['phase'] = 'error'
    report['error'] = type(error).__name__
finally:
    for port in ports:
        try:
            s.sendto(message(0xf0), ('192.168.1.210', port))
        except OSError:
            pass
    s.close()
    report['elapsed_ms'] = round(1000 * (time.monotonic() - mark))
    report['raw_control_count'] = len(raw_controls)
    directory = PRIVATE / 'research'
    directory.mkdir(mode=0o700, exist_ok=True)
    (directory / 'armed_dynamic_controls.json').write_text(json.dumps(raw_controls, indent=2))
    receipt.write_text(json.dumps(report, indent=2))
print(json.dumps(report), flush=True)
