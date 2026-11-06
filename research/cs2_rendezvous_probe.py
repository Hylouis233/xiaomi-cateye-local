"""Bounded handshake only. Public coordination allowed; media is never sent."""
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
PRIVATE = Path('/config/.storage/cateye_local')
ROOT = Path('/config/cateye_local/research')
TABLE = bytes.fromhex('49 59 43 3d b5 bf 6d a3 47 53 4f 61 65 e3 71 e9 67 7f 02 03 0b ad b3 89 2b 2f 35 c1 6b 8b 95 97 11 e5 a7 0d ef f1 05 07 83 fb 9d 3b c5 c7 13 17 1d 1f 25 29 d3 df')

def decode_init(text):
    output = bytearray()
    z = 0x39
    assert len(text) % 2 == 0
    for n in range(0, len(text), 2):
        hi, lo = ord(text[n]) - 65, ord(text[n + 1]) - 65
        assert 0 <= hi <= 15 and 0 <= lo <= 15
        c = ((hi << 4) | lo) ^ TABLE[len(output) % len(TABLE)] ^ z
        output.append(c)
        z ^= c
    hosts = output.decode().split(',')
    return [str(ipaddress.IPv4Address(x)) for x in hosts if x]

def msg(kind, data):
    return struct.pack('!BBH', 0xf1, kind, len(data)) + data

def main():
    mode = sys.argv[1]
    assert mode in ('servers_no_wake', 'servers_observed_no_wake', 'local_cached_port')
    cached = json.loads((PRIVATE / 'video_session.json').read_text())
    params = cached['response']['vendor']['vendor_params']
    parts = params['p2p_id'].split('-')
    assert len(parts) == 3 and len(parts[0]) <= 8 and len(parts[2]) <= 8
    did = parts[0].encode().ljust(8, b'\0') + struct.pack('!I', int(parts[1])) + parts[2].encode().ljust(8, b'\0')
    if mode == 'servers_observed_no_wake':
        observed = json.loads((ROOT / 'passive_after_wake.json').read_text())
        servers = sorted({r['dst'] for r in observed['rows'] if r['src'] == '192.168.1.210' and r['proto'] == 'udp' and r['dport'] == 32100})
    else:
        servers = decode_init(params['init_string'])
    assert 1 <= len(servers) <= 12 and all(ipaddress.ip_address(x).is_global for x in servers)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(('192.168.1.1', 0))
    s.setblocking(False)
    port = s.getsockname()[1]
    addr = b'\0\2' + struct.pack('<H', port) + socket.inet_aton('192.168.1.1')[::-1] + bytes(8)
    req = msg(0x20, did + addr)
    punch = msg(0x41, did)
    report = {'at': time.time(), 'mode': mode, 'cloud_wake_sent': False,
              'cloud_key_refresh_sent': False, 'external_coordination': mode.startswith('servers_'),
              'server_count': len(servers), 'media_requested': False, 'replies': [], 'peers': []}
    report['server_source'] = 'observed_device_registration' if mode == 'servers_observed_no_wake' else 'cached_vendor_parameters'
    local_peers = set()
    if mode == 'local_cached_port':
        old = json.loads((ROOT / 'servers_no_wake.json').read_text())
        local_peers = {(x['ip'], x['port']) for x in old['peers'] if x['ip'] == '192.168.1.210'}
        if not local_peers:
            report['aborted'] = 'no_verified_cached_local_port'
            s.close()
            (ROOT / (mode + '.json')).write_text(json.dumps(report, indent=2))
            print(json.dumps(report), flush=True)
            return
    mark = time.monotonic()
    deadline = mark + 9
    next_ask, next_punch = mark, mark
    try:
        while time.monotonic() < deadline:
            now = time.monotonic()
            if now >= next_ask and mode.startswith('servers_'):
                for host in servers:
                    s.sendto(req, (host, 32100))
                next_ask = now + 2
            if now >= next_punch:
                for peer in local_peers:
                    s.sendto(punch, peer)
                next_punch = now + .5
            if not select.select([s], [], [], min(.2, max(0, deadline - now)))[0]:
                continue
            data, peer = s.recvfrom(1500)
            if len(data) < 4 or data[0] != 0xf1:
                continue
            elapsed = round(1000 * (time.monotonic() - mark))
            kind = data[1]
            if peer[0] in servers:
                row = {'from': 'server', 'ms': elapsed, 'kind': hex(kind), 'length': len(data)}
                if kind == 0x21:
                    row['status'] = data[4:8].hex()
                elif kind == 0x40 and len(data) >= 20:
                    ip = socket.inet_ntoa(data[8:12][::-1])
                    peer_port = struct.unpack('<H', data[6:8])[0]
                    if ip == '192.168.1.210' and peer_port:
                        local_peers.add((ip, peer_port))
                        if not any(x['ip'] == ip and x['port'] == peer_port for x in report['peers']):
                            report['peers'].append({'ip': ip, 'port': peer_port, 'learned_ms': elapsed})
                    else:
                        row['non_camera_lan_address_rejected'] = True
                if len(report['replies']) < 60:
                    report['replies'].append(row)
            elif peer[0] == '192.168.1.210':
                local_peers.add(peer)
                if len(report['replies']) < 60:
                    report['replies'].append({'from': 'camera_lan', 'ms': elapsed, 'kind': hex(kind), 'port': peer[1], 'length': len(data)})
                if kind == 0x41:
                    s.sendto(punch, peer)
                    s.sendto(msg(0x42, did), peer)
                elif kind in (0x42, 0x43):
                    report['ready_ms'] = elapsed
                    report['ready_kind'] = hex(kind)
                    report['ready_port'] = peer[1]
                    break
        report['elapsed_ms'] = round(1000 * (time.monotonic() - mark))
    finally:
        for peer in local_peers:
            s.sendto(msg(0xf0, b''), peer)
        s.close()
    (ROOT / (mode + '.json')).write_text(json.dumps(report, indent=2))
    print(json.dumps(report), flush=True)

if __name__ == '__main__':
    main()
