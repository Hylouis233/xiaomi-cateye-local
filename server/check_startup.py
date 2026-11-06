import asyncio,aiohttp,json,time,uuid,io,av,sys
from pathlib import Path

async def main():
    start=time.monotonic();at=time.time();session=uuid.uuid4().hex;r={'at':at};blob=[]
    async with aiohttp.ClientSession()as s:
        try:
            async with s.post('http://192.168.1.1:18766/api/prepare',json={'session':session})as resp:
                r['prepare_status']=resp.status;r['prepare']=await resp.json();r['prepare_ms']=round(1000*(time.monotonic()-start))
            async with s.ws_connect('http://192.168.1.1:18766/api/media?session='+session)as w:
                await w.send_json({'type':'mse','value':'hvc1.1.6.L153.B0'})
                while len(blob)<14:
                    m=await asyncio.wait_for(w.receive(),12)
                    if m.type==aiohttp.WSMsgType.BINARY:
                        blob.append(m.data)
                        if len(blob)==2:r['first_media_ms']=round(1000*(time.monotonic()-start))
                    elif m.type==aiohttp.WSMsgType.TEXT:
                        v=json.loads(m.data)
                        if v.get('type')=='mse':r['mime']=v['value']
                        elif v.get('type')=='error':raise RuntimeError('media_error')
                    else:raise RuntimeError('closed_before_frames')
            with av.open(io.BytesIO(b''.join(blob)))as c:r['decoded_frames']=sum(1 for _ in c.decode(video=0))
        except Exception as e:r['error']=type(e).__name__
        finally:
            mark=time.monotonic()
            async with s.post('http://192.168.1.1:18766/api/stop',json={'session':session,'reason':'bounded_cold_phase_probe'})as resp:r['stop_status']=resp.status
            r['stop_ms']=round(1000*(time.monotonic()-mark))
    p=Path('/config/.storage/cateye_local')
    r['wake']=json.loads((p/'wake_timing.json').read_text());r['media']=json.loads((p/'media_timing.json').read_text())
    phases=[]
    for line in (p/'go2rtc.log').read_text().splitlines():
        if line.startswith('CATEYE_PHASE'):
            fields=dict(x.split('=',1)for x in line.split()[1:])
            if int(fields['at_ms'])>=at*1000:phases.append(fields)
    r['source_phases']=phases
    name=sys.argv[1]if len(sys.argv)>1 else'latest_startup_probe.json'
    Path('/config/cateye_local',name).write_text(json.dumps(r,indent=2));print(json.dumps(r))

asyncio.run(main())
