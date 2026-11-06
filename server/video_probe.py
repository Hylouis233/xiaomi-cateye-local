"""On-demand local-first wake and direct-LAN video preparation; never keep awake."""
import asyncio
import base64
from nacl.public import PrivateKey, PublicKey, Box
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
import json
import yaml
import logging
import os
import subprocess
from pathlib import Path
import sys
import time
import signal
import importlib
import types
from urllib.parse import urlencode

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, PrivateFormat, NoEncryption
from miio import Device

sys.path.insert(0, "/config/cateye_local/sdk")
from miot.cloud import MIoTHttpClient, MIoTOAuth2Client

PRIVATE = Path("/config/.storage/cateye_local")
# Code preload only; no client or network connection is created at import time.
_legacy_pkg=types.ModuleType('cateye_miot_legacy')
_legacy_pkg.__path__=['/config/custom_components/xiaomi_home/miot']
sys.modules.setdefault(_legacy_pkg.__name__,_legacy_pkg)
_legacy_cloud=importlib.import_module('cateye_miot_legacy.miot_cloud')
_legacy_const=importlib.import_module('cateye_miot_legacy.const')

async def cloud_wake():
    path=Path('/config/.storage/xiaomi_home/miot_config/973758982_cn.dict')
    auth=json.JSONDecoder().raw_decode(path.read_bytes().decode('utf-8',errors='replace'))[0]['auth_info']
    client=_legacy_cloud.MIoTHttpClient('cn',_legacy_const.OAUTH2_CLIENT_ID,auth['access_token'])
    try:
        result=await asyncio.wait_for(client.action_async('1234567890',2,3,[]),10)
        return isinstance(result,dict) and result.get('code',0)==0
    finally:await client.deinit_async()


def save(name, data):
    path = PRIVATE / name
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as stream:
        yaml.safe_dump(data, stream) if name.endswith('.yaml') else json.dump(data, stream)


async def main(force_fresh=False):
    no_wake=force_fresh or '--no-wake' in sys.argv
    started = time.monotonic()
    timing = {'at': time.time(), 'no_wake': no_wake}
    def phase(name, since):
        timing[name] = round(1000 * (time.monotonic()-since))
        timing['elapsed_ms'] = round(1000 * (time.monotonic()-started))
        save('wake_timing.json',timing)
    logging.disable(logging.CRITICAL)
    if not (PRIVATE / "mico_oauth.json").exists():
        print("BLOCKED: camera OAuth not saved", flush=True)
        return
    auth = json.loads((PRIVATE / "mico_oauth.json").read_text())
    local = json.loads((PRIVATE / "miio.json").read_text())
    if not no_wake:
        try:policy=json.loads((PRIVATE/'wake_policy.json').read_text()).get('mode','local_first')
        except (OSError,ValueError):policy='local_first'
        local_ok=False
        timing['wake_policy']=policy
        if policy!='cloud_on_demand':
            device=Device(local['ip'],local['token'],timeout=.6)
            mark=time.monotonic()
            try:
                result=await asyncio.to_thread(device.send,'action',{'did':local['did'],'siid':2,'aiid':3,'in':[]},0)
                code=result.get('code',0) if isinstance(result,dict) else None
                local_ok=code==0;timing['local_code']=code
            except Exception as error:timing['local_error']=type(error).__name__
            phase('local_wake_ms',mark)
        if not local_ok:
            mark=time.monotonic()
            ok=await cloud_wake()
            phase('cloud_wake_ms',mark)
            if not ok:
                print('BLOCKED: authorized cloud wake failed',flush=True)
                return
    # A saved credential is inert data. Reuse only after this explicit wake;
    # the player requests a fresh credential once if the device rejects it.
    if not no_wake and not force_fresh and '--force-refresh' not in sys.argv:
        try:
            cached=json.loads((PRIVATE/'video_session.json').read_text())
            config=yaml.safe_load((PRIVATE/'go2rtc.yaml').read_text())
            old=cached.get('response',{})
            if all(cached.get(k) for k in ['client_private','client_public']) and old.get('public_key') and old.get('sign') and old.get('vendor',{}).get('vendor')==4:
                uri='xiaomi://'+local['ip']+'?'+urlencode({'model':local['model'],'did':local['did'],'vendor':'cs2','client_public':cached['client_public'],'client_private':cached['client_private'],'device_public':old['public_key'],'sign':old['sign'],'subtype':'sd'})
                config.setdefault('streams',{})['cateye_local']=uri
                save('go2rtc.yaml',config)
                timing['used_cached_session']=True
                phase('total_ms',started)
                print('CACHED_SESSION_USED DIRECT_LAN_CONFIG_SAVED',flush=True)
                return {'ready': True, 'used_cached_session': True}
        except (OSError,ValueError,TypeError):pass
    if auth.get("expires_ts", 0) <= time.time()+60:
        refresher=MIoTOAuth2Client(redirect_uri='https://mico.api.mijia.tech/login_redirect',cloud_server='cn',uuid=auth.get('device_id','mico.cateye').removeprefix('mico.'))
        try:
            refreshed=await refresher.refresh_access_token_async(auth['refresh_token'])
            auth.update(access_token=refreshed.access_token,refresh_token=refreshed.refresh_token,expires_ts=refreshed.expires_ts)
            save('mico_oauth.json',auth)
            print('CAMERA_OAUTH_REFRESHED',flush=True)
        except Exception as error:
            print('BLOCKED: camera OAuth refresh failed',type(error).__name__,flush=True)
            return
        finally:await refresher.deinit_async()
    key = X25519PrivateKey.generate()
    public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    private = key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()).hex()
    client = MIoTHttpClient(cloud_server="cn", access_token=auth["access_token"])
    try:
        mark = time.monotonic()
        delays = [0] if no_wake else [0,.25,.5,1]
        key_deadline = time.monotonic()+10
        timing['key_requests'] = []
        for attempt, delay in enumerate(delays):
            if delay:await asyncio.sleep(delay)
            request_started = time.monotonic()
            try:
                remaining = key_deadline-time.monotonic()
                if remaining<=0:raise asyncio.TimeoutError()
                response = await client._MIoTHttpClient__mihome_api_post_async(
                    "/app/v2/device/miss_get_vendor_security",
                    {"app_pubkey": public, "did": local["did"], "support_vendors": "TUTK_CS2_MTP",
                     "caller_uuid": auth.get("device_id", "")}, timeout=min(1.5 if attempt==0 and not no_wake else 4,remaining))
                candidate = response.get('result',{})
                timing['key_requests'].append({'ms':round(1000*(time.monotonic()-request_started)),'ready':bool(isinstance(candidate,dict) and (candidate.get('crypt_result') or candidate.get('public_key')))})
                if isinstance(candidate,dict) and (candidate.get('crypt_result') or candidate.get('public_key')):break
                if attempt+1==len(delays):raise RuntimeError('video_key_not_ready')
            except Exception as error:
                timing['key_requests'].append({'ms':round(1000*(time.monotonic()-request_started)),'error':type(error).__name__})
                if attempt+1==len(delays):raise
        timing['key_attempts'] = attempt+1
        phase('key_ms',mark)
        result = response.get("result", {})
        save("video_security.json", {"client_public": public, "client_private": private, "response": result})
        if isinstance(result, dict) and result.get("svr_pubkey") and result.get("crypt_result"):
            shared = Box(PrivateKey(bytes.fromhex(private)), PublicKey(bytes.fromhex(result["svr_pubkey"]))).shared_key()
            encrypted = base64.b64decode(result["crypt_result"])
            decryptor = Cipher(algorithms.AES(shared[:16]), modes.CBC(encrypted[:16])).decryptor()
            clear = decryptor.update(encrypted[16:]) + decryptor.finalize()
            pad = clear[-1]
            if not 1 <= pad <= 16 or clear[-pad:] != bytes([pad]) * pad:
                raise ValueError("Invalid video credential padding")
            result = json.loads(clear[:-pad])
        print("VIDEO_KEY_RESULT_FIELDS", list(result) if isinstance(result, dict) else type(result).__name__, flush=True)
        if not isinstance(result, dict) or not result.get("public_key") or not result.get("sign"):
            print("BLOCKED: camera key response incomplete", flush=True)
            return
        vendor = result.get("vendor", {}).get("vendor")
        print("VIDEO_VENDOR", vendor, flush=True)
        if vendor != 4:
            print("BLOCKED: this probe supports direct-LAN CS2 only", flush=True)
            return
        save("video_session.json", {"client_public": public, "client_private": private, "response": result})
        uri = "xiaomi://" + local["ip"] + "?" + urlencode({
            "model": local["model"], "did": local["did"], "vendor": "cs2",
            "client_public": public, "client_private": private,
            "device_public": result["public_key"], "sign": result["sign"],
            "subtype": "sd"})
        config_path=PRIVATE/'go2rtc.yaml'
        try:config=yaml.safe_load(config_path.read_text()) or {}
        except (OSError,ValueError):config={}
        config.update(api={'listen':'127.0.0.1:11984'},rtsp={'listen':'0.0.0.0:18556'},webrtc={'listen':':11855','candidates':['192.168.1.1:11855'],'ice_servers':[]},log={'level':'error'})
        config.setdefault('streams',{})['cateye_local']=uri
        save('go2rtc.yaml',config)
        phase('total_ms',started)
        print("DIRECT_LAN_CONFIG_SAVED: no Xiaomi cloud account in stream URL", flush=True)
        return {'ready': True, 'used_cached_session': False}
    except Exception as error:
        timing['error'] = type(error).__name__
        phase('total_ms',started)
        print("VIDEO_KEY_ERROR", type(error).__name__, flush=True)
    finally:
        await client.deinit_async()


async def guarded_main():
    task = asyncio.current_task()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM,task.cancel)
    await main()

if __name__ == "__main__":
    asyncio.run(guarded_main())
