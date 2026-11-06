import asyncio
import json
import logging
import os
from pathlib import Path
import re
import sys
import time

os.umask(0o077)
logging.disable(logging.CRITICAL)
sys.path.insert(0, '/config/cateye_local')
import video_probe

ROOT = Path('/config/cateye_local/research')
PRIVATE = Path('/config/.storage/cateye_local')

def safe_error(error):
    code = getattr(error, 'code', None)
    text = str(error)
    http = re.search(r'failed, (\d{3})', text)
    response = re.search(r'response code, (-?\d+)', text)
    return {'error': type(error).__name__, 'code': getattr(code, 'value', code),
            'http_status': int(http.group(1)) if http else None,
            'response_code': int(response.group(1)) if response else None}

async def main():
    auth_file = Path('/config/.storage/xiaomi_home/miot_config/973758982_cn.dict')
    auth = json.JSONDecoder().raw_decode(auth_file.read_bytes().decode(errors='replace'))[0]['auth_info']
    client = video_probe._legacy_cloud.MIoTHttpClient('cn', video_probe._legacy_const.OAUTH2_CLIENT_ID, auth['access_token'])
    local = json.loads((PRIVATE / 'miio.json').read_text())
    cached = json.loads((PRIVATE / 'video_session.json').read_text())
    report = {'at': time.time(), 'cloud_wake_sent': False, 'stream_started': False}
    try:
        mark = time.monotonic()
        try:
            data = await asyncio.wait_for(client.get_devices_async(), 18)
            report['inventory_top_keys'] = list(data)
            hubs = []
            camera = []
            def walk(v):
                if isinstance(v, dict):
                    model = v.get('model', '')
                    if isinstance(model, str) and any(k in model for k in ('.gateway.', '.wifispeaker.', '.controller.', '.router.')):
                        hubs.append({k: v.get(k) for k in ('model', 'online', 'isOnline', 'localip', 'connect_type') if k in v})
                    if v.get('model') == local['model']:
                        camera.append({k: v.get(k) for k in ('model', 'online', 'isOnline', 'localip', 'connect_type', 'pid', 'fw_ver') if k in v})
                    for x in v.values():
                        walk(x)
                elif isinstance(v, list):
                    for x in v:
                        walk(x)
            walk(data)
            report['eligible_hub_candidates'] = hubs
            report['camera_records'] = camera
        except Exception as error:
            report['inventory_error'] = safe_error(error)
        report['inventory_ms'] = round(1000 * (time.monotonic() - mark))
        if '--inventory-only' in sys.argv:
            (ROOT / 'authorized_hub_inventory_complete.json').write_text(json.dumps(report, indent=2))
            print(json.dumps(report), flush=True)
            return
        mark = time.monotonic()
        try:
            answer = await client._MIoTHttpClient__mihome_api_post_async(
                '/app/v2/device/miss_get_vendor',
                {'app_pubkey': cached['client_public'], 'did': local['did'], 'support_vendors': 'TUTK_CS2_MTP'}, timeout=4)
            directory = PRIVATE / 'research'
            directory.mkdir(mode=0o700, exist_ok=True)
            (directory / 'home_oauth_vendor_metadata.json').write_text(json.dumps(answer))
            result = answer.get('result', {})
            report['home_oauth_vendor'] = {'response_keys': list(answer),
                                         'result_keys': list(result) if isinstance(result, dict) else [],
                                         'code': answer.get('code')}
        except Exception as error:
            report['home_oauth_vendor'] = safe_error(error)
        report['home_oauth_vendor_ms'] = round(1000 * (time.monotonic() - mark))
    finally:
        await client.deinit_async()
    (ROOT / 'authorized_route_inventory.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report), flush=True)

asyncio.run(main())
