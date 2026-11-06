"""On-demand LAN viewer with explicit, cancellable WebRTC sessions."""
import asyncio
import ipaddress
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import signal
import time
from urllib.parse import urlencode, urlsplit, parse_qs
import yaml
from aiohttp import ClientSession, web, WSMsgType

PRIVATE = Path('/config/.storage/cateye_local')
BASE = 'http://192.168.1.1:18766'
ALLOWED_ORIGINS = {BASE, 'http://192.168.1.1:8123', 'null', None}
PLAYER = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>猫眼实时画面</title><style>html,body{margin:0;height:100%;background:#10181d;color:#d7e8e8;font:13px system-ui}body{display:flex;flex-direction:column}video{width:100%;min-height:0;flex:1;object-fit:contain;background:#0b1115}.status{padding:7px 12px;color:#a6c6ca;min-height:18px}</style><video autoplay playsinline muted controls></video><div class="status">正在唤醒并连接猫眼…</div><script>
const session=new URLSearchParams(location.search).get('session');
const video=document.querySelector('video'),status=document.querySelector('.status');
let pc=null,ended=false,timer=null,abort=new AbortController();
function stop(){if(ended)return;ended=true;clearInterval(timer);abort.abort();if(pc)pc.close();video.srcObject=null;status.textContent='直播已关闭';navigator.sendBeacon('/api/stop',new Blob([JSON.stringify({session})],{type:'text/plain'}));}
async function heartbeat(){if(ended||!pc)return;let metrics={};try{let stats=await pc.getStats();stats.forEach(s=>{if(s.type==='inbound-rtp'&&s.kind==='video'){metrics={codec:stats.get(s.codecId)?.mimeType,fps:s.framesPerSecond,frames:s.framesDecoded,buffer_ms:s.jitterBufferEmittedCount?1000*s.jitterBufferDelay/s.jitterBufferEmittedCount:0};}});if(metrics.frames)status.textContent='WebRTC 实时画面'+(metrics.fps?' · '+Math.round(metrics.fps)+' fps':'');await fetch('/api/heartbeat',{method:'POST',body:JSON.stringify({session,metrics}),headers:{'Content-Type':'application/json'},signal:abort.signal});}catch(e){}}
async function start(){try{if(!session)throw Error('缺少观看会话');pc=new RTCPeerConnection({iceServers:[]});pc.addTransceiver('video',{direction:'recvonly'});pc.addTransceiver('audio',{direction:'recvonly'});pc.ontrack=e=>{if(e.streams[0])video.srcObject=e.streams[0];else{const stream=video.srcObject||new MediaStream();stream.addTrack(e.track);video.srcObject=stream;}};pc.onconnectionstatechange=()=>{if(pc.connectionState==='connected'){status.textContent='已连接，等待首帧…';heartbeat();}else if(['failed','closed'].includes(pc.connectionState)&&!ended){stop();status.textContent='连接已结束，请重新展开实时画面';}};await pc.setLocalDescription(await pc.createOffer());const r=await fetch('/api/live',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session,sdp:pc.localDescription.sdp}),signal:abort.signal});if(!r.ok)throw Error(await r.text());const result=await r.json();if(ended)return;await pc.setRemoteDescription({type:'answer',sdp:result.sdp});timer=setInterval(heartbeat,2500);heartbeat();}catch(e){if(!ended){stop();status.textContent='暂时无法连接，请重新展开实时画面';}}}
video.addEventListener('loadeddata',heartbeat);window.addEventListener('pagehide',stop);document.addEventListener('visibilitychange',()=>{if(document.hidden)stop()});window.addEventListener('message',e=>{if(e.origin==='http://192.168.1.1:8123'&&e.data?.type==='cateye-stop'&&e.data.session===session)stop()});start();
</script></html>'''


import event_history
import select
import video_probe

def private_json(name, value):
    path = PRIVATE / name
    temp = path.with_suffix('.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream)
    os.replace(temp, path)


def viewer_state(active):
    private_json('viewer_activity.json', {'active': active, 'at': time.time()})


def independent_activity():
    for name, duration in [('mijia_event.json', 12), ('external_activity.json', 5)]:
        try:
            value = json.loads((PRIVATE / name).read_text())
            if 0 <= time.time() - value.get('at', 0) < duration:
                return True
        except (OSError, ValueError):
            pass
    return False


@web.middleware
async def local_only(request, handler):
    try:
        allowed = ipaddress.ip_address(request.remote) in ipaddress.ip_network('192.168.1.0/24')
    except (ValueError, TypeError):
        allowed = False
    if not allowed or request.host != '192.168.1.1:18766':
        raise web.HTTPForbidden()
    if request.method == 'POST' and request.headers.get('Origin') not in ALLOWED_ORIGINS:
        raise web.HTTPForbidden()
    response = await handler(request)
    response.headers['Cache-Control'] = 'no-store'
    return response


def start_engine(app):
    config = yaml.safe_load((PRIVATE / 'go2rtc.yaml').read_text())
    config['streams']['cateye_live'] = ['ffmpeg:cateye_local#video=h264#audio=copy', 'ffmpeg:cateye_local#audio=aac']
    config['streams']['haier_lock_live'] = 'http://127.0.0.1:18781/live.ts'
    config['streams'].pop('cateye_record', None)
    config['streams'].pop('cateye_view', None)
    config.pop('preload', None)
    config['api'] = {'listen': '127.0.0.1:11984'}
    config['rtsp'] = {'listen': '0.0.0.0:18556'}
    config['webrtc'] = {'listen': ':11855', 'candidates': ['192.168.1.1:11855'], 'ice_servers': []}
    config['ffmpeg'] = {'h264': '-c:v libx264 -g 15 -keyint_min 15 -sc_threshold 0 -profile:v baseline -level:v 3.1 -preset:v ultrafast -tune:v zerolatency -bf 0 -pix_fmt:v yuv420p', 'rtsp':'-fflags nobuffer -flags low_delay -analyzeduration 1000000 -probesize 131072 -timeout {timeout} -user_agent go2rtc/ffmpeg -rtsp_flags prefer_tcp -i {input}'}
    fd = os.open(PRIVATE / 'go2rtc.yaml', os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as stream:
        yaml.safe_dump(config, stream)
    log = os.open(PRIVATE / 'go2rtc.log', os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    app['process'] = subprocess.Popen(['/config/cateye_local/bin/go2rtc', '-config', str(PRIVATE / 'go2rtc.yaml')], stdout=log, stderr=log)
    os.close(log)


async def kill_engine(app):
    process = app['process']
    if process.poll() is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(asyncio.to_thread(process.wait), .4)
    except asyncio.TimeoutError:
        process.kill()
        await asyncio.to_thread(process.wait)


async def hard_idle(app):
    if app.get('closing') or app['sessions'] or independent_activity():
        return
    async with app['lock']:
        if app.get('closing') or app['sessions'] or independent_activity():
            return
        await kill_engine(app)
        if app.get('closing'):return
        start_engine(app)
        private_json('stop_acceptance.json', {'at': time.time(), 'camera_transport_closed': True})


async def delete_peer(app, state):
    location = state.pop('peer', None)
    if location:
        try:
            async with app['client'].delete('http://127.0.0.1:11984' + location) as response:
                await response.read()
        except Exception:
            pass


async def close_session(app, session, reason="explicit_stop"):
    app['stopped'][session] = time.monotonic()
    state = app['sessions'].pop(session, None)
    if state is None:return
    if state:
        private_json('stopped_by.json',{'at':time.time(),'reason':reason,'elapsed_ms':round(1000*(time.monotonic()-state['started']))})
        state['closed'] = True
        await stop_primer(state)
        task=state.get('task')
        if task and task is not asyncio.current_task() and not task.done():task.cancel()
        process = state.get('prepare')
        if process and process.returncode is None:
            process.terminate()
        for peer in list(state.get('media_peers',set())):
            try:await asyncio.wait_for(peer.close(),.5)
            except Exception:pass
        await delete_peer(app, state)
    if not app['sessions']:
        viewer_state(False)
        await hard_idle(app)


async def engine_ready(app):
    for _ in range(30):
        try:
            async with app['client'].get('http://127.0.0.1:11984/api/streams',timeout=.3) as response:
                if response.status==200:return
        except Exception:pass
        await asyncio.sleep(.1)
    raise web.HTTPServiceUnavailable(text='直播服务正在准备，请稍后重试')


def auth_rejected_recent():
    path = PRIVATE / 'go2rtc.log'
    try:
        if time.time() - path.stat().st_mtime > 20:
            return False
        tail = path.read_bytes()[-2500:]
    except OSError:
        return False
    return b'miss: auth' in tail


async def stop_primer(state):
    ws = state.pop('primer_ws', None)
    if ws is not None:
        try:
            await asyncio.wait_for(ws.close(), .5)
        except Exception:
            pass
    task = state.pop('primer', None)
    if task and task is not asyncio.current_task() and not task.done():
        task.cancel()
        try:
            await asyncio.wait_for(task, .5)
        except Exception:
            pass


async def prime_stream(app, state):
    ws = None
    try:
        if state.get('closed'):
            return
        ws = await app['client'].ws_connect('http://127.0.0.1:11984/api/ws', params={'src': 'cateye_local'})
        state['primer_ws'] = ws
        await ws.send_str('{"type":"mse","value":"hvc1.1.6.L153.B0,hev1.1.6.L153.B0,avc1.640029,avc1.64002A,avc1.42E01F"}')
        async for msg in ws:
            if state.get('closed'):
                break
            if msg.type == WSMsgType.TEXT:
                low = (msg.data or '').lower()
                if 'auth' in low and any(word in low for word in ('miss', 'fail', 'error', 'rejected')):
                    state['auth_rejected'] = True
            elif msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR):
                break
    except asyncio.CancelledError:
        raise
    except Exception as error:
        if not state.get('closed'):
            state['prime_error'] = type(error).__name__
    finally:
        if state.get('primer_ws') is ws:
            state['primer_ws'] = None
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass


async def prepare(app, state, force_fresh=False):
    await engine_ready(app)
    async with app['client'].get('http://127.0.0.1:11984/api/streams') as response:
        streams = await response.json()
    if not force_fresh and any(p.get('id') for p in streams.get('cateye_local', {}).get('producers') or []):
        state['dial_pending'] = False
        return
    state['dial_pending'] = False
    # Imported at server startup; no device access until this explicit request.
    # main() uses sys.argv only for options, so pass a request-local flag instead.
    result = await asyncio.wait_for(video_probe.main(force_fresh=force_fresh), 25)
    if state['closed']:raise web.HTTPGone(text='观看已取消')
    if not result or not result.get('ready'):raise web.HTTPServiceUnavailable(text='猫眼暂未就绪')
    state['used_cached_session']=result.get('used_cached_session',False)
    config = yaml.safe_load((PRIVATE / 'go2rtc.yaml').read_text())
    query = urlencode({'name': 'cateye_local', 'src': config['streams']['cateye_local']})
    async with app['client'].put('http://127.0.0.1:11984/api/streams?' + query) as response:
        if response.status != 200:
            raise web.HTTPServiceUnavailable()
    state['dial_pending'] = True


async def prepare_ha(request):
    data=await request.json()
    session=data.get('session','')
    if not re.fullmatch(r'[A-Za-z0-9-]{16,80}',session):raise web.HTTPBadRequest()
    app=request.app
    if session in app['stopped']:raise web.HTTPGone(text='观看已取消')
    if session in app['sessions']:raise web.HTTPConflict()
    state={'closed':False,'connected':False,'task':asyncio.current_task(),'last_seen':time.monotonic(),'started':time.monotonic(),'transport':'mse','video_path':'ha_same_origin'}
    app['sessions'][session]=state
    try:
        async with app['lock']:
            if state['closed']:raise web.HTTPGone()
            await prepare(app,state)
            if state['closed']:raise web.HTTPGone()
            query=urlencode([('name','cateye_view'),('src','rtsp://127.0.0.1:18556/cateye_local'),('src','rtsp://127.0.0.1:18556/cateye_live')])
            async with app['client'].put('http://127.0.0.1:11984/api/streams?'+query) as response:
                if response.status!=200:raise web.HTTPServiceUnavailable()
            state['connected']=True;state['last_seen']=time.monotonic()
            state['prepare_ms']=round(1000*(time.monotonic()-state['started']))
            state['dial_started']=False
        return web.json_response({'ready':True,'prepare_ms':state['prepare_ms'],'used_cached_session':state.get('used_cached_session',False),'dial_started':bool(state.get('primer'))})
    except Exception as error:
        private_json('live_failure.json',{'at':time.time(),'error':type(error).__name__,'status':getattr(error,'status',None),'message':getattr(error,'text','')[:160]})
        process=state.get('prepare')
        if process and process.returncode is None:process.terminate()
        state['closed']=True
        await stop_primer(state)
        if app['sessions'].get(session) is state:app['sessions'].pop(session,None)
        if not app['sessions']:viewer_state(False);await hard_idle(app)
        raise


async def media(request):
    state=request.app['sessions'].get(request.query.get('session'))
    if not state or state['closed'] or not state.get('connected'):raise web.HTTPGone()
    state['media_bridge_ms']=round(1000*(time.monotonic()-state['started']))
    downstream=web.WebSocketResponse()
    await downstream.prepare(request)
    peers=state.setdefault('media_peers',set());peers.add(downstream)
    upstream=None
    try:
        first=await asyncio.wait_for(downstream.receive(),5)
        if first.type!=WSMsgType.TEXT:raise ValueError('expected_mse')
        msg=json.loads(first.data)
        if msg.get('type')!='mse' or not isinstance(msg.get('value'),str) or len(msg['value'])>1000:raise ValueError('expected_mse_codecs')
        source='cateye_local' if any(c in msg['value'] for c in ['hvc1','hev1']) else 'cateye_live'
        upstream=await request.app['client'].ws_connect('http://127.0.0.1:11984/api/ws',params={'src':source})
        state['upstream_ws_ms']=round(1000*(time.monotonic()-state['started']))
        await upstream.send_str(first.data)
        async def relay(src,dst):
            binary_count=0
            async for item in src:
                if item.type==WSMsgType.TEXT:
                    if src is upstream:state.setdefault('media_mime_ms',round(1000*(time.monotonic()-state['started'])))
                    await dst.send_str(item.data)
                elif item.type==WSMsgType.BINARY:
                    binary_count+=1
                    if src is upstream and binary_count==2:
                        state['first_fragment_ms']=round(1000*(time.monotonic()-state['started']))
                        private_json('media_timing.json',{'at':time.time(),**{k:state.get(k) for k in ['prepare_ms','media_bridge_ms','upstream_ws_ms','media_mime_ms','first_fragment_ms']}})
                    await dst.send_bytes(item.data)
                else:break
        tasks=[asyncio.create_task(relay(upstream,downstream)),asyncio.create_task(relay(downstream,upstream))]
        try:
            await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
    except (Exception,asyncio.CancelledError):
        pass
    finally:
        peers.discard(downstream)
        if upstream:await upstream.close()
        await downstream.close()
    return downstream


async def rekey(request):
    data=await request.json()
    state=request.app['sessions'].get(data.get('session'))
    if not state or state['closed']:raise web.HTTPGone()
    if state.get('rekeyed'):raise web.HTTPConflict()
    if not state.get('auth_rejected') and not auth_rejected_recent():
        return web.json_response({'ready':True,'skipped':True,'reason':'not_auth'})
    state['rekeyed']=True
    state['task']=asyncio.current_task()
    async with request.app['lock']:
        if state['closed']:raise web.HTTPGone()
        await stop_primer(state)
        await prepare(request.app,state,force_fresh=True)
        if state['closed']:raise web.HTTPGone()
        state['last_seen']=time.monotonic()
    return web.json_response({'ready':True,'skipped':False,'dial_started':bool(state.get('primer'))})


async def live(request):
    data = await request.json()
    session = data.get('session', '')
    if not re.fullmatch(r'[A-Za-z0-9-]{16,80}', session) or len(data.get('sdp', '')) > 65536:
        raise web.HTTPBadRequest()
    app = request.app
    if session in app['stopped']:
        raise web.HTTPGone(text='观看已取消')
    if session in app['sessions']:
        raise web.HTTPConflict()
    state = {'closed': False, 'connected':False, 'task':asyncio.current_task(), 'last_seen': time.monotonic(), 'started': time.monotonic()}
    app['sessions'][session] = state
    try:
        async with app['lock']:
            if state['closed']:
                raise web.HTTPGone()
            await prepare(app, state)
            state['prepare_ms']=round(1000*(time.monotonic()-state['started']))
            if state['closed']:
                raise web.HTTPGone()
            hevc=bool(re.search(r'^a=rtpmap:\d+ H265/90000',data['sdp'],re.M|re.I))
            state['video_path']='hevc_direct' if hevc else 'h264_compat'
            source='cateye_local' if hevc else 'cateye_live'
            async with app['client'].post('http://127.0.0.1:11984/api/webrtc?src='+source, data=data['sdp'], headers={'Content-Type': 'application/sdp'}) as response:
                answer = await response.text()
                private_json('rtc_negotiation.json', {'status':response.status,'location':response.headers.get('Location',''),'answer_type':response.headers.get('Content-Type',''),'body_bytes':len(answer)})
                if response.status not in (200, 201):
                    raise web.HTTPBadGateway(text='实时推流协商失败')
                parsed = urlsplit(response.headers.get('Location', ''))
                peer_id = parse_qs(parsed.query).get('id', [''])[0]
                if parsed.path == '/api/webrtc' and peer_id:
                    state['peer'] = parsed.path + '?' + urlencode({'id': peer_id})
                # This go2rtc build does not expose IDs for WHEP consumers.
                # Last-viewer stop closes the owned media engine instead.
            if state['closed']:
                await delete_peer(app, state)
                raise web.HTTPGone()
            state['last_seen'] = time.monotonic()
            state['connected']=True
        state['negotiated_ms']=round(1000*(time.monotonic()-state['started']))
        return web.json_response({'sdp': answer})
    except Exception as error:
        private_json('live_failure.json', {'at':time.time(),'error':type(error).__name__,'status':getattr(error,'status',None),'message':getattr(error,'text','')[:160]})
        process=state.get('prepare')
        if process and process.returncode is None:process.terminate()
        state['closed'] = True
        if app['sessions'].get(session) is state:
            app['sessions'].pop(session, None)
        for peer in list(state.get('media_peers',set())):
            try:await asyncio.wait_for(peer.close(),.5)
            except Exception:pass
        await delete_peer(app, state)
        if not app['sessions']:
            viewer_state(False)
            await hard_idle(app)
        raise


async def stop(request):
    data = json.loads(await request.text())
    session = data.get('session', '')
    if re.fullmatch(r'[A-Za-z0-9-]{16,80}', session):
        await close_session(request.app, session, str(data.get('reason','explicit_stop'))[:40])
    return web.json_response({'stopped': True})


async def heartbeat(request):
    data = await request.json()
    state = request.app['sessions'].get(data.get('session'))
    if not state or state['closed']:
        raise web.HTTPGone()
    state['last_seen'] = time.monotonic()
    metrics = data.get('metrics', {})
    if metrics.get('frames', 0) > 0:
        viewer_state(True)
        if 'first_frame' not in state:
            state['first_frame'] = round(1000 * (time.monotonic() - state['started']))
            event_history.record('viewer', source='ha_viewer')
        private_json('live_metrics.json', {'at': time.time(), 'session':data.get('session'),'transport':state.get('transport','webrtc'), 'client':data.get('client','browser'), 'video_path':state.get('video_path'),'codec':metrics.get('codec'),'fps': metrics.get('fps'), 'frames': metrics.get('frames'), 'buffer_ms': metrics.get('buffer_ms'), 'first_frame_ms': state['first_frame'],'prepare_ms':state.get('prepare_ms'),'negotiated_ms':state.get('negotiated_ms'),'dropped_frames':metrics.get('dropped_frames'),'waits':metrics.get('waits'),'catchups':metrics.get('catchups'),'media_bridge_ms':state.get('media_bridge_ms'),'media_mime_ms':state.get('media_mime_ms'),'first_fragment_ms':state.get('first_fragment_ms'),'client_first_frame_ms':metrics.get('first_frame_ms')})
    return web.json_response({'ok': True})


async def idle(request):
    if request.remote != '192.168.1.1':
        raise web.HTTPForbidden()
    await hard_idle(request.app)
    return web.json_response({'ok': True})


async def maintenance(app):
    while True:
        await asyncio.sleep(2)
        now = time.monotonic()
        for session, state in list(app['sessions'].items()):
            limit=15 if state.get('connected') else 45
            if now - state['last_seen'] > limit:
                await close_session(app, session, 'heartbeat_timeout' if state.get('connected') else 'connect_timeout')
        app['stopped'] = {k: v for k, v in app['stopped'].items() if now - v < 60}


async def events(request):
    data=await request.json()
    try:
        after=float(data.get('after',time.time()-3600));before=float(data.get('before',time.time()))
    except (TypeError,ValueError):raise web.HTTPBadRequest()
    if before<after or before-after>86400:raise web.HTTPBadRequest()
    return web.json_response({'events':event_history.read(after,before)})


async def index(request):
    if not request.query.get('session'):
        return web.Response(text='<meta charset="utf-8"><p>请在 HA 安防页展开门口猫眼，再选择实时画面。</p>',content_type='text/html')
    return web.Response(text=PLAYER, content_type='text/html')


async def stop_orphan_engines():
    def owned():
        found=[]
        for path in Path('/proc').iterdir():
            if not path.name.isdigit():continue
            try:
                if (path/'cmdline').read_bytes().split(b'\0')[0]==b'/config/cateye_local/bin/go2rtc':found.append(int(path.name))
            except OSError:pass
        return found
    for pid in owned():
        try:os.kill(pid,signal.SIGTERM)
        except ProcessLookupError:pass
    for _ in range(10):
        if not owned():return
        await asyncio.sleep(.1)
    for pid in owned():
        try:os.kill(pid,signal.SIGKILL)
        except ProcessLookupError:pass


async def shutdown(app):
    app['closing']=True
    for session in list(app['sessions']):await close_session(app,session,'service_shutdown')
    await kill_engine(app)


async def startup(app):
    app['lock'] = asyncio.Lock()
    app['sessions'] = {}
    app['stopped'] = {}
    app['client'] = ClientSession()
    await stop_orphan_engines()
    start_engine(app)
    await engine_ready(app)
    viewer_state(False)
    private_json('viewer_build.json', {'build': '20261031-dial', 'at': time.time()})
    app['maintenance'] = asyncio.create_task(maintenance(app))


async def cleanup(app):
    app['closing'] = True
    app['maintenance'].cancel()
    await asyncio.gather(app['maintenance'], return_exceptions=True)
    for session in list(app['sessions']):
        await close_session(app, session)
    await app['client'].close()
    async with app['lock']:
        await kill_engine(app)
    viewer_state(False)


def main():
    logging.disable(logging.CRITICAL)
    app = web.Application(middlewares=[local_only], client_max_size=100000)
    from nvr_bridge import install
    install(app)
    app.on_startup.append(startup)
    app.on_shutdown.append(shutdown)
    app.on_cleanup.append(cleanup)
    app.add_routes([web.get('/', index), web.post('/api/events', events), web.post('/api/live', live), web.post('/api/prepare', prepare_ha), web.post('/api/rekey', rekey), web.get('/api/media', media), web.post('/api/stop', stop), web.post('/api/heartbeat', heartbeat), web.post('/api/idle', idle)])
    web.run_app(app, host='192.168.1.1', port=18766, access_log=None, print=None)


if __name__ == '__main__':
    main()
