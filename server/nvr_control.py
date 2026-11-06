"""Use the existing NVR owner for recording, retention and face recognition."""
import json
from pathlib import Path
import urllib.parse
import urllib.request

import paho.mqtt.publish as publish

PRIVATE = Path('/config/.storage/cateye_local')


def settings():
    return json.loads((PRIVATE / 'nvr.json').read_text())


def publish_enabled(enabled):
    c=settings()
    auth={'username':c['username'],'password':c['password']} if c.get('username') else None
    publish.single(c['prefix']+'/'+c['camera']+'/enabled/set','ON' if enabled else 'OFF',qos=1,retain=not enabled,hostname=c['broker'],port=c['port'],auth=auth)


def gate(enabled):
    query = {'name': 'cateye_record', 'src': 'rtsp://127.0.0.1:18556/cateye_local'} if enabled else {'src': 'cateye_record'}
    request = urllib.request.Request('http://127.0.0.1:11984/api/streams?' + urllib.parse.urlencode(query),
                                     method='PUT' if enabled else 'DELETE')
    with urllib.request.urlopen(request, timeout=4) as response:
        if response.status not in (200, 204):
            raise RuntimeError('NVR gate update failed')


def enable():
    gate(True)
    try:publish_enabled(True)
    except Exception:
        gate(False)
        raise


def disable():
    # Removing the restream gate terminates the media path even if MQTT is down.
    try:
        gate(False)
    except Exception:
        pass
    publish_enabled(False)
    try:
        request = urllib.request.Request('http://192.168.1.1:18766/api/idle', data=b'', method='POST')
        with urllib.request.urlopen(request,timeout=4) as response:response.read()
    except Exception:pass


def status():
    c = settings()
    with urllib.request.urlopen(c['api'] + '/api/config', timeout=5) as response:
        camera = json.load(response).get('cameras', {}).get(c['camera'], {})
    return {'enabled': bool(camera.get('enabled')), 'record': camera.get('record', {}),
            'face_recognition': camera.get('face_recognition', {})}
