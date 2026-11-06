import asyncio,aiohttp,json,pathlib,time,jwt,uuid,io,av
async def main():
 p=pathlib.Path('/config/.storage');a=json.loads((p/'auth').read_text())['data'];users={u['id']:u for u in a['users']};t=max([t for t in a['refresh_tokens'] if users.get(t['user_id'],{}).get('is_owner') and users.get(t['user_id'],{}).get('is_active')],key=lambda x:x.get('last_used_at') or x.get('created_at') or '')
 token=jwt.encode({'iss':t['id'],'iat':int(time.time()),'exp':int(time.time())+180},t['jwt_key'],algorithm='HS256')
 results=[]
 async with aiohttp.ClientSession() as c:
  async with c.ws_connect('http://127.0.0.1:8123/api/websocket') as control:
   await control.receive_json();await control.send_json({'type':'auth','access_token':token});await control.receive_json();counter=0
   async def call(message):
    nonlocal counter
    counter+=1;await control.send_json({'id':counter,**message})
    while True:
     r=await control.receive_json()
     if r.get('id')==counter:
      if not r.get('success'):raise RuntimeError(str(r.get('error'))[:150])
      return r['result']
   async def service(name,data):
    return await call({'type':'call_service','domain':'rest_command','service':name,'service_data':data,'return_response':True})
   for mode,codecs in [('hevc','hvc1.1.6.L153.B0'),('h264','avc1.640029,avc1.64002A,avc1.42E01F')]:
    session=uuid.uuid4().hex;record={'mode':mode};start=time.monotonic();blob=[]
    try:
     r=await service('cateye_prepare',{'session':session});record['prepare_status']=r['response']['status'];record['prepare_ms']=round(1000*(time.monotonic()-start))
     path='/api/frigate/frigate/go2rtc/ws/api/ws?src=cateye_ha';signed=await call({'type':'auth/sign_path','path':path,'expires':60})
     async with c.ws_connect('http://127.0.0.1:8123'+signed['path']) as w:
      await w.send_json({'type':'mse','value':codecs});binary=0;streamstart=time.monotonic();last_hb=0
      while time.monotonic()-streamstart<9:
       m=await asyncio.wait_for(w.receive(),15)
       if m.type==aiohttp.WSMsgType.TEXT:
        v=json.loads(m.data)
        if v.get('type')=='error':raise RuntimeError(v.get('value','')[:180])
        if v.get('type')=='mse':record['mime']=v['value']
       elif m.type==aiohttp.WSMsgType.BINARY:
        blob.append(m.data);binary+=1
        if binary==2:record['first_media_ms']=round(1000*(time.monotonic()-start))
       if binary>1 and time.monotonic()-last_hb>2:
        await service('cateye_heartbeat',{'session':session,'metrics':{'frames':binary}});last_hb=time.monotonic()
     record['bytes']=sum(map(len,blob))
     try:
      with av.open(io.BytesIO(b''.join(blob)),format='mp4') as container:frames=list(container.decode(video=0))
      record['decoded_frames']=len(frames);record['size']=[frames[0].width,frames[0].height] if frames else None
     except Exception as e:record['decode_error']=type(e).__name__
    except Exception as e:record['error']=type(e).__name__+': '+str(e)[:150]
    finally:
     stop=time.monotonic();await service('cateye_stop',{'session':session,'reason':'same_origin_acceptance'});record['stop_ms']=round(1000*(time.monotonic()-stop))
    results.append(record);print('SAME_ORIGIN_MEDIA_TEST',json.dumps(record),flush=True)
 pathlib.Path('/config/cateye_local/same_origin_acceptance.json').write_text(json.dumps(results,indent=2))
asyncio.run(main())
