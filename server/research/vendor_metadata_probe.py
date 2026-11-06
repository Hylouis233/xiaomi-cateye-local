"""Read existing-pubkey coordination metadata; never wake or replace live keys."""
import asyncio
import ipaddress
import json
import logging
import os
from pathlib import Path
import runpy
import re
import sys
import time

os.umask(0o077)
logging.disable(logging.CRITICAL)
sys.path.insert(0, '/config/cateye_local/sdk')
from miot.cloud import MIoTHttpClient

PRIVATE = Path('/config/.storage/cateye_local')
ROOT = Path('/config/cateye_local/research')

async def main():
    auth = json.loads((PRIVATE / 'mico_oauth.json').read_text())
    local = json.loads((PRIVATE / 'miio.json').read_text())
    cached = json.loads((PRIVATE / 'video_session.json').read_text())
    decode = runpy.run_path(str(ROOT / 'cs2_rendezvous_probe.py'))['decode_init']
    client = MIoTHttpClient(cloud_server='cn', access_token=auth['access_token'])
    started = time.monotonic()
    report = {'at': time.time(), 'cloud_wake_sent': False, 'external_metadata_request': True,
              'key_pair_reused': True, 'production_credentials_changed': False}
    endpoint = '/app/v2/device/miss_get_vendor'
    try:
        answer = await client._MIoTHttpClient__mihome_api_post_async(endpoint,
            {'app_pubkey': cached['client_public'], 'did': local['did'],
             'support_vendors': 'TUTK_CS2_MTP', 'caller_uuid': auth.get('device_id', '')}, timeout=4)
        directory = PRIVATE / 'research'
        directory.mkdir(mode=0o700, exist_ok=True)
        (directory / 'legacy_vendor_metadata.json').write_text(json.dumps(answer))
        report['response_keys'] = list(answer)
        report['code'] = answer.get('code')
        result = answer.get('result', {})
        if isinstance(result, dict):
            report['result_keys'] = list(result)
            params = result.get('vendor', {}).get('vendor_params', {})
            report['vendor'] = result.get('vendor', {}).get('vendor')
            report['has_public_key'] = bool(result.get('public_key'))
            report['has_signature'] = bool(result.get('sign'))
            report['has_p2p_id'] = bool(params.get('p2p_id'))
            if params.get('init_string'):
                servers = decode(params['init_string'])
                report['server_addresses'] = servers
                report['usable_public_servers'] = sum(ipaddress.ip_address(x).is_global for x in servers)
    except Exception as error:
        report['error'] = type(error).__name__
        code = getattr(error, 'code', None)
        report['code'] = getattr(code, 'value', code)
        text = str(error)
        status = re.search(r'failed, (\d{3})', text)
        response_code = re.search(r'response code, (-?\d+)', text)
        report['http_status'] = int(status.group(1)) if status else None
        report['response_code'] = int(response_code.group(1)) if response_code else None
        report['unauthorized'] = 'unauthorized' in text.lower()
    finally:
        await client.deinit_async()
    report['elapsed_ms'] = round(1000 * (time.monotonic() - started))
    (ROOT / 'legacy_vendor_metadata_result.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report), flush=True)

asyncio.run(main())
