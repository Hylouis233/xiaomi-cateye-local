"""Bounded local-only media test. No OAuth, cloud SDK or key refresh."""
import asyncio
import io
import json
import logging
import os
from pathlib import Path
import sys
import time
from urllib.parse import urlencode, urlparse

import aiohttp
import av
from miio import Device

PRIVATE = Path('/config/.storage/cateye_local')
ROOT = Path('/config/cateye_local')
API = 'http://127.0.0.1:11984'
logging.disable(logging.CRITICAL)

def persist(name, value):
    path = ROOT / name
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(value, f, indent=2)

async def counts(client):
    async with client.get(API + '/api/streams') as response:
        d = await response.json()
    return {k: {'active_producers': sum(bool(p.get('medias')) for p in v.get('producers') or []),
                'consumers': len(v.get('consumers') or [])} for k, v in d.items()}

def cached_uri():
    local = json.loads((PRIVATE / 'miio.json').read_text())
    cached = json.loads((PRIVATE / 'video_session.json').read_text())
    old = cached['response']
    assert local['ip'] == '192.168.1.210' and old['vendor']['vendor'] == 4
    uri = 'xiaomi://' + local['ip'] + '?' + urlencode({
        'model': local['model'], 'did': local['did'], 'vendor': 'cs2',
        'client_public': cached['client_public'], 'client_private': cached['client_private'],
        'device_public': old['public_key'], 'sign': old['sign'], 'subtype': 'sd'})
    assert urlparse(uri).hostname == local['ip'] and urlparse(uri).username is None
    return local, uri

async def main():
    mode = sys.argv[1]
    assert mode in ('idle_local_wake', 'awake_no_wake')
    result = {'at': time.time(), 'mode': mode, 'cloud_wake_sent': False,
              'cloud_key_request_sent': False, 'cached_credentials': True,
              'video_path': 'direct_lan_cs2', 'browser_measurement': False}
    alias = 'cateye_test_local_only'
    local, uri = cached_uri()
    start = time.monotonic()
    blob = []
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as client:
        before = await counts(client)
        result['before'] = before
        if any(v['active_producers'] or v['consumers'] for v in before.values()):
            result['aborted'] = 'another_viewer_active'
            persist('pure_local_' + mode + '.json', result)
            print(json.dumps(result), flush=True)
            return
        start = time.monotonic()
        try:
            async with asyncio.timeout(16):
                if mode == 'idle_local_wake':
                    mark = time.monotonic()
                    device = Device(local['ip'], local['token'], timeout=.6)
                    try:
                        answer = await asyncio.to_thread(device.send, 'action',
                            {'did': local['did'], 'siid': 2, 'aiid': 3, 'in': []}, 0)
                        result['local_wake_code'] = answer.get('code') if isinstance(answer, dict) else None
                    except Exception as error:
                        result['local_wake_error'] = type(error).__name__
                    result['local_wake_ms'] = round(1000 * (time.monotonic() - mark))
                async with client.put(API + '/api/streams', params={'name': alias, 'src': uri}) as response:
                    assert response.status == 200
                media_start = time.monotonic()
                result['before_media_ms'] = round(1000 * (media_start - start))
                async with client.ws_connect(API + '/api/ws', params={'src': alias}) as ws:
                    await ws.send_json({'type': 'mse', 'value': 'hvc1.1.6.L153.B0'})
                    while len(blob) < 8:
                        message = await asyncio.wait_for(ws.receive(), 12)
                        elapsed = round(1000 * (time.monotonic() - start))
                        if message.type == aiohttp.WSMsgType.BINARY:
                            blob.append(message.data)
                            if len(blob) == 1:
                                result['init_ms'] = elapsed
                            elif len(blob) == 2:
                                result['first_fragment_ms'] = elapsed
                            if len(blob) >= 2:
                                try:
                                    with av.open(io.BytesIO(b''.join(blob))) as c:
                                        frames = sum(1 for _ in c.decode(video=0))
                                    result['decoded_frames'] = frames
                                    if frames and 'first_decodable_ms' not in result:
                                        result['first_decodable_ms'] = round(1000 * (time.monotonic() - start))
                                        result['local_media_first_decodable_ms'] = round(1000 * (time.monotonic() - media_start))
                                    if frames >= 15:
                                        result['success'] = True
                                        break
                                except av.error.FFmpegError:
                                    pass
                        elif message.type == aiohttp.WSMsgType.TEXT:
                            answer = json.loads(message.data)
                            if answer.get('type') == 'mse':
                                result['mime'] = answer.get('value')
                            elif answer.get('type') == 'error':
                                result['media_error'] = True
                                raise RuntimeError('local_media_error')
                        else:
                            raise RuntimeError('closed_before_frames')
        except Exception as error:
            result['error'] = type(error).__name__
        finally:
            result['test_elapsed_ms'] = round(1000 * (time.monotonic() - start))
            mark = time.monotonic()
            async with client.delete(API + '/api/streams', params={'src': alias}) as response:
                result['delete_status'] = response.status
            async with client.post('http://192.168.1.1:18766/api/idle', data=b'') as response:
                result['idle_status'] = response.status
            result['stop_ms'] = round(1000 * (time.monotonic() - mark))
        for _ in range(15):
            try:
                result['after'] = await counts(client)
                break
            except (aiohttp.ClientError, ConnectionError):
                await asyncio.sleep(.1)
    phases = []
    for line in (PRIVATE / 'go2rtc.log').read_text().splitlines():
        if line.startswith('CATEYE_PHASE '):
            fields = dict(x.split('=', 1) for x in line.split()[1:] if '=' in x)
            if int(fields['at_ms']) >= result['at'] * 1000:
                phases.append(fields)
    result['source_phases'] = phases
    persist('pure_local_' + mode + '.json', result)
    print(json.dumps(result), flush=True)

asyncio.run(main())
