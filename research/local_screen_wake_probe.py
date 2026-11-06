"""One spec-declared local screen wake; no setting changes or cloud fallback."""
import json
import logging
import os
from pathlib import Path
import subprocess
import time
from miio import Device

os.umask(0o077)
logging.disable(logging.CRITICAL)
ROOT = Path('/config/cateye_local/research')
path = ROOT / 'local_screen_wake_probe.json'
if path.exists():
    print(json.dumps({'repeat_refused': True}), flush=True)
    raise SystemExit(0)
local = json.loads(Path('/config/.storage/cateye_local/miio.json').read_text())
spec = json.loads(Path('/config/cateye_local/ph300-spec.json').read_text())
report = {'at': time.time(), 'cloud_called': False, 'persistent_settings_changed': False,
          'action': {'siid': 2, 'aiid': 2, 'description': 'Light Up Screen'},
          'declared_gatt_access_operations': sum(len(p.get('gatt-access', [])) for s in spec['services'] for p in s.get('properties', []))}
path.write_text(json.dumps(report, indent=2))
device = Device(local['ip'], local['token'], timeout=3)
try:
    info = device.send('miIO.info', [], retry_count=0)
    report['device_firmware'] = {k: info[k] for k in ('model', 'fw_ver', 'hw_ver', 'miio_ver') if k in info}
except Exception as error:
    report['info_error'] = type(error).__name__
mark = time.monotonic()
try:
    result = device.send('action', {'did': local['did'], 'siid': 2, 'aiid': 2, 'in': []}, retry_count=0)
    report['result_code'] = result.get('code') if isinstance(result, dict) else None
    report['accepted'] = report['result_code'] == 0
except Exception as error:
    code = getattr(error, 'code', None)
    report.update(error=type(error).__name__, code=getattr(code, 'value', code), busy='busy' in str(error).lower(), accepted=False)
report['elapsed_ms'] = round(1000 * (time.monotonic() - mark))
if report.get('accepted'):
    trial = subprocess.run(['python3', '/config/cateye_local/pure_local_probe.py', 'awake_no_wake'], capture_output=True, text=True, timeout=22)
    report['media_probe_exit'] = trial.returncode
    try:
        report['media'] = json.loads(trial.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        report['media_result_missing'] = True
path.write_text(json.dumps(report, indent=2))
print(json.dumps(report), flush=True)
