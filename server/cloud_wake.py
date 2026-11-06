import asyncio, subprocess, select, sys, json, pathlib, logging
logging.disable(logging.CRITICAL)
sys.path.insert(0, '/config/custom_components/xiaomi_home')
from miot.miot_cloud import MIoTHttpClient
from miot.const import OAUTH2_CLIENT_ID
async def main():
 p=pathlib.Path('/config/.storage/xiaomi_home/miot_config/973758982_cn.dict')
 a=json.JSONDecoder().raw_decode(p.read_bytes().decode('utf-8', errors='replace'))[0]['auth_info']
 c=MIoTHttpClient('cn',OAUTH2_CLIENT_ID,a['access_token'])
 try:
  r=await c.action_async('1234567890',2,3,[])
  print('CLOUD_WAKE_RESULT',json.dumps(r))
  if isinstance(r,dict) and r.get('code',0)!=0:raise SystemExit(1)
 finally:await c.deinit_async()
asyncio.run(main())
