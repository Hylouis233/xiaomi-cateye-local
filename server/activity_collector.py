"""Observe existing CS2 media; never poll or wake an idle camera."""
import collections
import atexit
import ipaddress
import json
import yaml
import os
from pathlib import Path
import signal
import struct
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import nvr_control

PRIVATE=Path('/config/.storage/cateye_local')
CAMERA='192.168.1.210'
STATE=PRIVATE/'collector_status.json'
last_external=0.0
event=threading.Event()
lock=threading.Lock()
# A mijia/viewer/external notice is only a few seconds, but video_probe may block
# for 25s before the NVR gate opens. Remember the trigger across that gap, then
# keep the gate open long enough for Frigate to retain a segment. Never wake here.
pending_until=0.0
pending_kind=None
hold_until=0.0
HOLD_AFTER_READY={'mijia':25,'viewer':20,'external':15}

def save(state):
    state.update(at=int(time.time()))
    f=STATE.with_suffix('.tmp')
    fd=os.open(f,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w') as stream:json.dump(state,stream)
    os.replace(f,STATE)

def classify(packet):
    if len(packet)<34:return None
    offset=14
    proto=int.from_bytes(packet[12:14],'big')
    if proto==0x8100:
        proto=int.from_bytes(packet[16:18],'big');offset=18
    if proto!=0x0800 or len(packet)<offset+20:return None
    ip=packet[offset:]
    if str(ipaddress.ip_address(ip[12:16]))!=CAMERA:return None
    dest=str(ipaddress.ip_address(ip[16:20]))
    # All our consumers use the router address. They must not retrigger collection.
    if dest in ('192.168.1.1','127.0.0.1'):return None
    ihl=(ip[0]&15)*4
    if ip[9]==17:payload=ip[ihl+8:]
    elif ip[9]==6:
        tcp=ip[ihl:]
        if len(tcp)<20:return None
        payload=tcp[(tcp[12]>>4)*4:]
    else:return None
    # CS2 DRW, channel 2 is the audio/video channel; reject control and keepalive.
    for n in range(min(20,max(0,len(payload)-7))):
        if payload[n:n+2]==b'\xf1\xd0' and payload[n+4:n+6]==b'\xd1\x02':
            return dest
    return None

def refresh_source():
    with urllib.request.urlopen('http://127.0.0.1:11984/api/streams',timeout=4) as r:
        current=json.load(r).get('cateye_local',{})
    if any(p.get('id') for p in current.get('producers') or []):return
    result=subprocess.run(['python','/config/cateye_local/video_probe.py','--no-wake'],capture_output=True,timeout=25)
    if result.returncode or b'DIRECT_LAN_CONFIG_SAVED' not in result.stdout:
        raise RuntimeError('video_session_key_unavailable')
    config=yaml.safe_load((PRIVATE/'go2rtc.yaml').read_text())
    query=urllib.parse.urlencode({'name':'cateye_local','src':config['streams']['cateye_local']})
    req=urllib.request.Request('http://127.0.0.1:11984/api/streams?'+query,method='PUT')
    with urllib.request.urlopen(req,timeout=4) as r:
        if r.status!=200:raise RuntimeError('local_stream_update_failed')

def stop_current():
    try:nvr_control.disable()
    except Exception:pass

atexit.register(stop_current)
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

def note_trigger(kind,remember=40):
    global pending_until,pending_kind
    with lock:
        pending_until=max(pending_until,time.monotonic()+remember)
        pending_kind=kind
    event.set()

def extend_hold(seconds):
    global hold_until
    with lock:
        hold_until=max(hold_until,time.monotonic()+seconds)

def active_reason():
    try:
        event_data=json.loads((PRIVATE/'mijia_event.json').read_text())
        if 0<=time.time()-float(event_data.get('at',0))<12:return 'mijia'
    except (OSError,ValueError,TypeError):pass
    if time.monotonic()-last_external<5:return 'external'
    try:
        v=json.loads((PRIVATE/'viewer_activity.json').read_text())
        if v.get('active') and 0<=time.time()-float(v.get('at',0))<8:return 'viewer'
    except (OSError,ValueError,TypeError):pass
    return None

def media_active():
    return active_reason() is not None

def record_worker():
    global pending_until,pending_kind
    while True:
        event.wait();event.clear()
        with lock:
            owed=time.monotonic()<pending_until or time.monotonic()<hold_until
            kind=pending_kind
        if not owed and not media_active():continue
        kind=active_reason() or kind or 'hold'
        opened=False
        try:
            refresh_source()
            with lock:
                pending_until=0.0
                pending_kind=None
            # The notice may already have expired while the probe ran. Record from now.
            extend_hold(HOLD_AFTER_READY.get(kind,20))
            nvr_control.enable()
            opened=True
            save({'state':'nvr_active','recording':True,'storage':'shared_with_living_room','face_recognition':True,'trigger':kind})
            while True:
                reason=active_reason()
                if reason=='mijia':extend_hold(HOLD_AFTER_READY['mijia'])
                elif reason=='viewer':extend_hold(HOLD_AFTER_READY['viewer'])
                elif reason=='external':extend_hold(HOLD_AFTER_READY['external'])
                with lock:
                    keep=time.monotonic()<hold_until
                if not keep and reason is None:break
                time.sleep(.5)
        except Exception as error:
            save({'state':'capture_failed','recording':False,'error':type(error).__name__,'trigger':kind,'storage':'shared_with_living_room'})
            time.sleep(3)
        finally:
            if opened:
                try:nvr_control.disable()
                except Exception:pass
                if not media_active():
                    with lock:
                        pending_until=0.0
                        pending_kind=None
                save({'state':'idle','recording':False,'storage':'shared_with_living_room'})

def watch_mijia_events():
    seen_at=None
    while True:
        try:
            v=json.loads((PRIVATE/'mijia_event.json').read_text())
            at=v.get('at')
            if at is not None and 0<=time.time()-float(at)<12 and at!=seen_at:
                seen_at=at
                note_trigger('mijia')
        except (OSError,ValueError,TypeError):pass
        time.sleep(.5)

def watch_local_viewer():
    was=False
    while True:
        fresh=False
        try:
            v=json.loads((PRIVATE/'viewer_activity.json').read_text())
            fresh=bool(v.get('active') and 0<=time.time()-float(v.get('at',0))<8)
        except (OSError,ValueError,TypeError):pass
        if fresh and not was:note_trigger('viewer')
        was=fresh
        time.sleep(1)

def read_exact(stream,n):
    data=b''
    while len(data)<n:
        part=stream.read(n-len(data))
        if not part:return None
        data+=part
    return data

def main():
    global last_external
    try:nvr_control.disable()
    except Exception:pass
    save({'state':'observing_passively','recording':False,'coverage':'CS2 media visible at router; SD-only/PIR events unverified'})
    threading.Thread(target=record_worker,daemon=True).start()
    threading.Thread(target=watch_local_viewer,daemon=True).start()
    threading.Thread(target=watch_mijia_events,daemon=True).start()
    stream=sys.stdin.buffer;header=read_exact(stream,24)
    if not header:raise SystemExit('No pcap input')
    endian='<' if header[:4] in (b'\xd4\xc3\xb2\xa1',b'\x4d\x3c\xb2\xa1') else '>'
    if struct.unpack(endian+'I',header[20:24])[0]!=1:raise SystemExit('Ethernet capture required')
    recent=collections.deque()
    while True:
        record=read_exact(stream,16)
        if record is None:break
        length=struct.unpack(endian+'IIII',record)[2]
        if length>65536:raise SystemExit('Invalid capture length')
        packet=read_exact(stream,length)
        if packet is None:break
        peer=classify(packet)
        if peer is None:continue
        now=time.monotonic();recent.append(now)
        while recent and now-recent[0]>2:recent.popleft()
        if len(recent)>=2:
            last_external=now
            note_trigger('external')
            try:
                f=PRIVATE/'external_activity.json';temp=f.with_suffix('.tmp')
                fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
                # Do not name this `stream`: that is the pcap reader in this function.
                with os.fdopen(fd,'w') as out:json.dump({'at':time.time()},out)
                os.replace(temp,f)
            except OSError:pass
    save({'state':'observer_stopped','recording':False})

if __name__=='__main__':main()
