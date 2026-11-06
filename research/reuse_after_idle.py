"""After 90 quiet seconds, attempt local-only wake/handshake and media."""
import asyncio
import io
import json
import logging
import os
from pathlib import Path
import socket
import struct
import time
from urllib.parse import urlencode

import aiohttp
import av
from miio import Device

os.umask(0o077)
logging.disable(logging.CRITICAL)
ROOT = Path('/config/cateye_local/research')
PRIVATE = Path('/config/.storage/cateye_local')

def write_report(path, report):
    path.write_text(json.dumps(report, indent=2))

async def counts(client):
    async with client.get('http://127.0.0.1:11984/api/streams') as response:
        values = await response.json()
    return {k: {'active_producers': sum(bool(p.get('medias')) for p in v.get('producers') or []),
                'consumers': len(v.get('consumers') or [])} for k, v in values.items()}

def read_business(local):
    spec = json.loads(Path('/config/cateye_local/ph300-spec.json').read_text())
    prop = next({'did': local['did'], 'siid': s['iid'], 'piid': p['iid']}
                for s in spec['services'] for p in s.get('properties', [])
                if ':battery-level:' in p.get('type', ''))
    device = Device(local['ip'], local['token'], timeout=2.5)
    result = {}
    mark = time.monotonic()
    try:
        device._protocol.send_handshake(retry_count=0)
        answer = device.send('get_properties', [prop], retry_count=0)
        result['codes'] = [x.get('code') for x in answer if isinstance(x, dict)] if isinstance(answer, list) else []
    except Exception as error:
        code = getattr(error, 'code', None)
        result.update(error=type(error).__name__, code=getattr(code, 'value', code), busy='busy' in str(error).lower())
    result['elapsed_ms'] = round(1000 * (time.monotonic() - mark))
    return result

async def media(client, local, port):
    cached = json.loads((PRIVATE / 'video_session.json').read_text())
    old = cached['response']
    uri = 'xiaomi://' + local['ip'] + ':' + str(port) + '?' + urlencode({
        'model': local['model'], 'did': local['did'], 'vendor': 'cs2',
        'client_public': cached['client_public'], 'client_private': cached['client_private'],
        'device_public': old['public_key'], 'sign': old['sign'], 'subtype': 'sd'})
    alias = 'cateye_reuse_local_test'
    result = {'cloud_called': False, 'cached_credentials': True, 'video_path': 'direct_lan_cs2',
              'browser_measurement': False, 'port': port}
    mark = time.monotonic()
    blob = []
    try:
        async with asyncio.timeout(16):
            async with client.put('http://127.0.0.1:11984/api/streams', params={'name': alias, 'src': uri}) as response:
                assert response.status == 200
            async with client.ws_connect('http://127.0.0.1:11984/api/ws', params={'src': alias}) as ws:
                await ws.send_json({'type': 'mse', 'value': 'hvc1.1.6.L153.B0'})
                while len(blob) < 8:
                    message = await asyncio.wait_for(ws.receive(), 12)
                    if message.type == aiohttp.WSMsgType.BINARY:
                        blob.append(message.data)
                        if len(blob) >= 2:
                            try:
                                with av.open(io.BytesIO(b''.join(blob))) as c:
                                    frames = sum(1 for _ in c.decode(video=0))
                                result['decoded_frames'] = frames
                                if frames and 'first_decodable_ms' not in result:
                                    result['first_decodable_ms'] = round(1000 * (time.monotonic() - mark))
                                if frames >= 15:
                                    result['success'] = True
                                    break
                            except av.error.FFmpegError:
                                pass
                    elif message.type == aiohttp.WSMsgType.TEXT:
                        answer = json.loads(message.data)
                        if answer.get('type') == 'error':
                            raise RuntimeError('media_error')
                    else:
                        raise RuntimeError('media_closed')
    except Exception as error:
        result['error'] = type(error).__name__
    finally:
        async with client.delete('http://127.0.0.1:11984/api/streams', params={'src': alias}) as response:
            result['delete_status'] = response.status
        async with client.post('http://192.168.1.1:18766/api/idle', data=b'') as response:
            result['idle_status'] = response.status
    result['elapsed_ms'] = round(1000 * (time.monotonic() - mark))
    return result

async def main():
    path = ROOT / 'reuse_after_idle.json'
    previous = json.loads((ROOT / 'armed_dynamic_probe.json').read_text())
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        print(json.dumps({'repeat_refused': True, 'receipt': str(path)}), flush=True)
        return
    report = {'at': time.time(), 'phase': 'waiting_without_device_traffic', 'cloud_called': False,
              'cloud_wake_sent': False, 'cloud_key_refresh_sent': False, 'external_coordination': False}
    with os.fdopen(fd, 'w') as f:
        json.dump(report, f)
    quiet_since = previous['wake_at'] + previous['elapsed_ms'] / 1000
    remain = max(0, quiet_since + 90 - time.time())
    print(json.dumps({'waiting_quiet_s': round(remain), 'device_requests_while_waiting': 0}), flush=True)
    while remain > 0:
        await asyncio.sleep(min(30, remain))
        remain = max(0, quiet_since + 90 - time.time())
    report['quiet_s_before_probe'] = round(time.time() - quiet_since, 1)
    local = json.loads((PRIVATE / 'miio.json').read_text())
    cached = json.loads((PRIVATE / 'video_session.json').read_text())
    parts = cached['response']['vendor']['vendor_params']['p2p_id'].split('-')
    did = parts[0].encode().ljust(8, b'\0') + struct.pack('!I', int(parts[1])) + parts[2].encode().ljust(8, b'\0')
    ports = {previous['ready_port'], 32108}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as client:
        report['before'] = await counts(client)
        if any(v['active_producers'] or v['consumers'] for v in report['before'].values()):
            report['phase'] = 'aborted_other_viewer_active'
            write_report(path, report)
            print(json.dumps(report), flush=True)
            return
        report['business_state'] = await asyncio.to_thread(read_business, local)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(('192.168.1.1', 0))
        sock.setblocking(False)
        loop = asyncio.get_running_loop()
        mark = time.monotonic()
        next_send = 0
        report['local_replies'] = []
        try:
            while time.monotonic() - mark < 6:
                now = time.monotonic()
                if now >= next_send:
                    for port in ports:
                        await loop.sock_sendto(sock, b'\xf1\x30\0\0', (local['ip'], port))
                        await loop.sock_sendto(sock, b'\xf1\x41\x00\x14' + did, (local['ip'], port))
                    next_send = now + 1
                try:
                    data, peer = await asyncio.wait_for(loop.sock_recvfrom(sock, 1500), .2)
                except asyncio.TimeoutError:
                    continue
                if peer[0] != local['ip'] or len(data) < 4 or data[0] != 0xf1:
                    continue
                ms = round(1000 * (time.monotonic() - mark))
                if len(report['local_replies']) < 30:
                    report['local_replies'].append({'ms': ms, 'kind': hex(data[1]), 'port': peer[1]})
                if data[1] == 0x41:
                    await loop.sock_sendto(sock, data, peer)
                elif data[1] in (0x42, 0x43):
                    report['ready_ms'] = ms
                    report['ready_port'] = peer[1]
                    await loop.sock_sendto(sock, b'\xf1\xf0\0\0', peer)
                    break
        finally:
            sock.close()
        report['handshake_elapsed_ms'] = round(1000 * (time.monotonic() - mark))
        if report.get('ready_ms') is not None:
            report['media'] = await media(client, local, report['ready_port'])
        for attempt in range(20):
            try:
                report['after'] = await counts(client)
                break
            except aiohttp.ClientError:
                await asyncio.sleep(.1)
        else:
            report['after_readback_error'] = 'engine_api_unavailable'
    report['phase'] = 'finished'
    write_report(path, report)
    print(json.dumps(report), flush=True)

asyncio.run(main())
