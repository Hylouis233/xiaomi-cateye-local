import asyncio,subprocess,select,json,pathlib,time,uuid,logging,os,ssl
import paho.mqtt.client as mqtt
import event_history
logging.disable(logging.CRITICAL)
p=pathlib.Path('/config/.storage/cateye_local')
def save(name,data):
 f=p/name;t=f.with_suffix('.tmp');fd=os.open(t,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
 with os.fdopen(fd,'w') as s:json.dump(data,s)
 os.replace(t,f)
def credentials():
 f=pathlib.Path('/config/.storage/xiaomi_home/miot_config/973758982_cn.dict')
 return json.JSONDecoder().raw_decode(f.read_bytes().decode('utf-8',errors='replace'))[0]['auth_info']['access_token']
client=mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,client_id='ha.'+uuid.uuid4().hex)
def connected(c,u,flags,reason,properties):
 save('mijia_events_status.json',{'at':time.time(),'connected':reason==0,'reason':str(reason)})
 if reason==0:c.subscribe('device/1234567890/up/event_occured/#',qos=1)
def subscribed(c,u,mid,reasons,props):
 save('mijia_events_status.json',{'at':time.time(),'connected':True,'subscribed':all(not r.is_failure for r in reasons)})
def message(c,u,msg):
 if msg.retain:return
 try:
  body=json.loads(msg.payload);v=body['params']
  if v.get('siid')!=2 or v.get('eiid') not in [1,3,4]:return
  at=time.time()
  save('mijia_event.json',{'at':at,'siid':2,'eiid':v['eiid'],'source':'existing_mijia_interface'})
  event_history.record(v['eiid'], at)
 except (ValueError,KeyError,TypeError):pass
client.on_connect=connected;client.on_subscribe=subscribed;client.on_message=message
client.tls_set(cert_reqs=ssl.CERT_REQUIRED)
token=credentials();client.username_pw_set('2882303761520251711',token)
client.connect_async('cn-ha.mqtt.io.mi.com',8883,60);client.loop_start()
try:
 while True:
  time.sleep(30)
  fresh=credentials()
  if fresh!=token:
   token=fresh;client.username_pw_set('2882303761520251711',token);client.reconnect()
finally:client.disconnect();client.loop_stop()
