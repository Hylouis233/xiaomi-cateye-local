"""One production-path acceptance, matching the viewer's one-rekey limit."""
import asyncio
import io
import json
import os
from pathlib import Path
import time
import uuid

import aiohttp
import av

os.umask(0o077)
ROOT = Path('/config/cateye_local/research')
PRIVATE = Path('/config/.storage/cateye_local')
BASE = 'http://192.168.1.1:18766'

async def main():
    path = ROOT / 'ha_rekey_recovery.json'
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        print(json.dumps({'repeat_refused': True}), flush=True)
        return
    report = {'at': time.time(), 'path': 'existing_HA_prepare_media_rekey_stop',
              'browser_measurement': False, 'rekey_calls': 0, 'stages': []}
    with os.fdopen(fd, 'w') as f:
        json.dump(report, f)
    session = uuid.uuid4().hex
    started = time.monotonic()
    task = None
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as client:
        async def beat():
            while True:
                await asyncio.sleep(2)
                try:
                    async with client.post(BASE + '/api/heartbeat', json={'session': session, 'client': 'bounded_protocol_acceptance', 'metrics': {'frames': 0}}) as response:
                        await response.read()
                except aiohttp.ClientError:
                    pass
        try:
            async with asyncio.timeout(40):
                async with client.post(BASE + '/api/prepare', json={'session': session}) as response:
                    prepared = await response.json()
                    report['prepare_status'] = response.status
                    report['prepare'] = prepared
                    assert response.status == 200
                task = asyncio.create_task(beat())
                for attempt in range(2):
                    blob = []
                    stage = {'attempt': attempt + 1, 'started_ms': round(1000 * (time.monotonic() - started))}
                    report['stages'].append(stage)
                    failed = False
                    async with client.ws_connect(BASE + '/api/media', params={'session': session}) as ws:
                        await ws.send_json({'type': 'mse', 'value': 'hvc1.1.6.L153.B0'})
                        while len(blob) < 30:
                            msg = await asyncio.wait_for(ws.receive(), 16)
                            if msg.type == aiohttp.WSMsgType.BINARY:
                                blob.append(msg.data)
                                if len(blob) >= 2:
                                    try:
                                        with av.open(io.BytesIO(b''.join(blob))) as container:
                                            frames = list(container.decode(video=0))
                                        stage['decoded_frames'] = len(frames)
                                        if frames:
                                            stage.setdefault('first_decoded_ms', round(1000 * (time.monotonic() - started)))
                                            stage['size'] = [frames[0].width, frames[0].height]
                                        if len(frames) >= 15:
                                            report['success'] = True
                                            break
                                    except av.error.FFmpegError:
                                        pass
                            elif msg.type == aiohttp.WSMsgType.TEXT:
                                data = json.loads(msg.data)
                                if data.get('type') == 'error':
                                    value = str(data.get('value', '')).lower()
                                    stage['error_flags'] = {x: x in value for x in ('auth', 'timeout', 'no answer from camera')}
                                    failed = True
                                    break
                            else:
                                failed = True
                                stage['closed_before_frames'] = True
                                break
                    if report.get('success'):
                        break
                    if not failed or attempt or not prepared.get('used_cached_session'):
                        break
                    stage['failed_ms'] = round(1000 * (time.monotonic() - started))
                    report['rekey_calls'] += 1
                    mark = time.monotonic()
                    async with client.post(BASE + '/api/rekey', json={'session': session}) as response:
                        report['rekey_status'] = response.status
                        await response.read()
                        assert response.status == 200
                    report['rekey_ms'] = round(1000 * (time.monotonic() - mark))
        except Exception as error:
            report['error'] = type(error).__name__
        finally:
            if task:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            mark = time.monotonic()
            async with client.post(BASE + '/api/stop', json={'session': session, 'reason': 'research_final_acceptance'}) as response:
                report['stop_status'] = response.status
                await response.read()
            report['stop_ms'] = round(1000 * (time.monotonic() - mark))
            report['elapsed_ms'] = round(1000 * (time.monotonic() - started))
    report['viewer_active_after_stop'] = json.loads((PRIVATE / 'viewer_activity.json').read_text()).get('active')
    path.write_text(json.dumps(report, indent=2))
    print(json.dumps(report), flush=True)

asyncio.run(main())
