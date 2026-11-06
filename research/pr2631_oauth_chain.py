"""Full PR transport trial using existing OAuth wake/key adapters, LAN peer pinned."""
import asyncio
import base64
import io
import json
import logging
import os
from pathlib import Path
import re
import runpy
import select
import socket
import subprocess
import sys
import time
from urllib.parse import urlencode

import aiohttp
import av
import yaml
from nacl.public import Box, PrivateKey, PublicKey
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, PublicFormat, NoEncryption

os.umask(0o077)
logging.disable(logging.CRITICAL)
sys.path.insert(0, '/config/cateye_local')
import video_probe

ROOT = Path('/config/cateye_local/research')
PRIVATE = Path('/config/.storage/cateye_local')
API = 'http://127.0.0.1:12984'

def safe_error(error):
    value = getattr(error, 'code', None)
    text = str(error)
    status = re.search(r'failed, (\d{3})', text)
    return {'type': type(error).__name__, 'code': getattr(value, 'value', value),
            'http_status': int(status.group(1)) if status else None}

async def ports_probe(ip):
    async def one(port):
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), .35)
            row = {'port': port, 'open': True}
            if port in (554, 8554):
                writer.write(('OPTIONS rtsp://' + ip + '/ RTSP/1.0\r\nCSeq: 1\r\n\r\n').encode())
                await writer.drain()
                try:
                    data = await asyncio.wait_for(reader.read(64), .3)
                    row['rtsp_status_prefix'] = data.startswith(b'RTSP/1.0')
                except asyncio.TimeoutError:
                    pass
            writer.close()
            await writer.wait_closed()
            return row
        except (OSError, asyncio.TimeoutError):
            return {'port': port, 'open': False}
    return await asyncio.gather(*(one(x) for x in (80, 443, 554, 8554, 8899, 2020, 8000, 8080)))

async def main():
    receipt = ROOT / 'pr2631_oauth_chain.json'
    try:
        fd = os.open(receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        print(json.dumps({'repeat_refused': True, 'receipt': str(receipt)}), flush=True)
        return
    report = {'at': time.time(), 'phase': 'claimed_before_device_calls',
              'implementation': 'PR2631 transport with OAuth wake/key adapters and LAN peer restriction',
              'not_unmodified_native_mihome_login': True, 'media_policy': 'camera_LAN_only',
              'cloud_wake_calls': 0, 'video_key_requests': 0, 'production_credentials_changed': False,
              'browser_measurement': False}
    with os.fdopen(fd, 'w') as f:
        json.dump(report, f)
    directory = PRIVATE / 'research/pr2631'
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    process = None
    key_client = None
    began = time.monotonic()
    local = json.loads((PRIVATE / 'miio.json').read_text())
    auth = json.loads((PRIVATE / 'mico_oauth.json').read_text())
    auth_home = json.JSONDecoder().raw_decode(Path('/config/.storage/xiaomi_home/miot_config/973758982_cn.dict').read_bytes().decode(errors='replace'))[0]['auth_info']
    home = video_probe._legacy_cloud.MIoTHttpClient('cn', video_probe._legacy_const.OAUTH2_CLIENT_ID, auth_home['access_token'])
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=40)) as client:
        try:
            test_socket = socket.socket()
            test_socket.settimeout(.2)
            occupied = test_socket.connect_ex(('127.0.0.1', 12984)) == 0
            test_socket.close()
            assert not occupied, 'test_api_port_in_use'
            async with client.get('http://127.0.0.1:11984/api/streams') as response:
                existing = await response.json()
            if any(p.get('medias') for v in existing.values() for p in v.get('producers') or []):
                report['phase'] = 'aborted_other_stream_active'
                return
            try:
                await home._MIoTHttpClient__mihome_api_post_async('/app/home/rpc/' + local['did'],
                    {'id': 1, 'method': 'wakeup_host', 'params': {}}, timeout=3)
                report['wakeup_host_rpc'] = {'accepted': True}
            except Exception as error:
                report['wakeup_host_rpc'] = safe_error(error)
            mark = time.monotonic()
            report['cloud_wake_calls'] = 1
            assert await video_probe.cloud_wake(), 'miot_wake_not_accepted'
            report['cloud_wake_ms'] = round(1000 * (time.monotonic() - mark))
            await asyncio.sleep(3)
            report['post_wake_pause_s'] = 3
            key = X25519PrivateKey.generate()
            public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
            private = key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()).hex()
            key_client = video_probe.MIoTHttpClient(cloud_server='cn', access_token=auth['access_token'])
            mark = time.monotonic()
            report['video_key_requests'] = 1
            answer = await key_client._MIoTHttpClient__mihome_api_post_async('/app/v2/device/miss_get_vendor_security',
                {'app_pubkey': public, 'did': local['did'], 'support_vendors': 'TUTK_CS2_MTP',
                 'caller_uuid': auth.get('device_id', '')}, timeout=6)
            result = answer.get('result', {})
            if result.get('svr_pubkey') and result.get('crypt_result'):
                shared = Box(PrivateKey(bytes.fromhex(private)), PublicKey(bytes.fromhex(result['svr_pubkey']))).shared_key()
                encrypted = base64.b64decode(result['crypt_result'])
                decryptor = Cipher(algorithms.AES(shared[:16]), modes.CBC(encrypted[:16])).decryptor()
                clear = decryptor.update(encrypted[16:]) + decryptor.finalize()
                pad = clear[-1]
                assert 1 <= pad <= 16 and clear[-pad:] == bytes([pad]) * pad
                result = json.loads(clear[:-pad])
            report['key_ms'] = round(1000 * (time.monotonic() - mark))
            assert result.get('public_key') and result.get('sign') and result.get('vendor', {}).get('vendor') == 4
            (directory / 'fresh_session.json').write_text(json.dumps({'client_public': public, 'client_private': private, 'response': result}))
            decode = runpy.run_path(str(ROOT / 'cs2_rendezvous_probe.py'))['decode_init']
            vendor = result['vendor']['vendor_params']
            servers = decode(vendor['init_string'])
            import ipaddress
            usable = [x for x in servers if ipaddress.ip_address(x).is_global]
            report['credential_coordination_addresses_usable'] = bool(usable)
            if not usable:
                observed = json.loads((ROOT / 'passive_after_wake.json').read_text())
                usable = sorted({r['dst'] for r in observed['rows'] if r['src'] == local['ip'] and r['proto'] == 'udp' and r['dport'] == 32100})
                report['coordination_source'] = 'observed_actual_device_registration'
            else:
                report['coordination_source'] = 'fresh_vendor_response'
            assert usable and all(ipaddress.ip_address(x).is_global for x in usable)
            report['coordination_server_count'] = len(usable)
            report['awake_tcp_ports'] = await ports_probe(local['ip'])
            uri = 'xiaomi://' + local['ip'] + '?' + urlencode({
                'model': local['model'], 'did': local['did'], 'vendor': 'cs2',
                'client_public': public, 'client_private': private, 'device_public': result['public_key'],
                'sign': result['sign'], 'p2p_id': vendor['p2p_id'], 'p2p_servers': ','.join(usable), 'subtype': 'sd'})
            config = {'api': {'listen': '127.0.0.1:12984'}, 'rtsp': {'listen': ''},
                      'webrtc': {'listen': ''}, 'log': {'level': 'error'}, 'streams': {'pr2631': uri}}
            config_path = directory / 'go2rtc.yaml'
            config_path.write_text(yaml.safe_dump(config))
            env = dict(os.environ, CATEYE_LAN_ONLY_IP=local['ip'], CATEYE_CLIENT_LAN_IP='192.168.1.1')
            log = open(directory / 'go2rtc.log', 'ab', buffering=0)
            process = subprocess.Popen([str(ROOT / 'go2rtc-pr2631'), '-config', str(config_path)], env=env, stdout=log, stderr=log)
            log.close()
            for _ in range(30):
                try:
                    async with client.get(API + '/api/streams', timeout=.3) as response:
                        if response.status == 200:
                            break
                except aiohttp.ClientError:
                    pass
                await asyncio.sleep(.1)
            report['phase'] = 'connecting_pr_transport'
            mark = time.monotonic()
            blob = []
            async with asyncio.timeout(30):
                async with client.ws_connect(API + '/api/ws', params={'src': 'pr2631'}) as ws:
                    await ws.send_json({'type': 'mse', 'value': 'hvc1.1.6.L153.B0'})
                    while len(blob) < 10:
                        msg = await ws.receive()
                        if msg.type == aiohttp.WSMsgType.BINARY:
                            blob.append(msg.data)
                            if len(blob) >= 2:
                                try:
                                    with av.open(io.BytesIO(b''.join(blob))) as c:
                                        frames = sum(1 for _ in c.decode(video=0))
                                    report['decoded_frames'] = frames
                                    if frames and 'first_decoded_ms' not in report:
                                        report['first_decoded_ms'] = round(1000 * (time.monotonic() - began))
                                        report['media_first_decoded_ms'] = round(1000 * (time.monotonic() - mark))
                                    if frames >= 15:
                                        report['success'] = True
                                        break
                                except av.error.FFmpegError:
                                    pass
                        elif msg.type == aiohttp.WSMsgType.TEXT:
                            data = json.loads(msg.data)
                            if data.get('type') == 'error':
                                text = str(data.get('value', ''))
                                (directory / 'websocket_error_private.txt').write_text(text)
                                report['transport_error_flags'] = {word: word in text.lower() for word in ('no answer from camera', 'timeout', 'auth', 'login', 'connection refused')}
                                raise RuntimeError('pr_transport_error')
                        else:
                            raise RuntimeError('websocket_closed')
            report['phase'] = 'finished'
        except Exception as error:
            report['error'] = safe_error(error)
        finally:
            if process:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
                report['test_engine_stopped'] = process.poll() is not None
            if key_client:
                await key_client.deinit_async()
            await home.deinit_async()
            report['elapsed_ms'] = round(1000 * (time.monotonic() - began))
            receipt.write_text(json.dumps(report, indent=2))
            print(json.dumps(report), flush=True)

asyncio.run(main())
