import json
import os
from pathlib import Path
import time
import urllib.request

os.umask(0o077)
root = Path('/config/cateye_local/research')
read = lambda name: json.loads((root / name).read_text())
idle = read('reuse_after_idle.json')
armed = read('armed_dynamic_probe.json')
servers = read('servers_observed_no_wake.json')
first = read('cloud_wake_once_receipt.json')
passive = read('passive_after_wake.json')
with urllib.request.urlopen('http://127.0.0.1:11984/api/streams', timeout=2) as response:
    d = json.load(response)
streams = {k: {'active_producers': sum(bool(p.get('medias')) for p in v.get('producers') or []),
               'consumers': len(v.get('consumers') or [])} for k, v in d.items()}
private = Path('/config/.storage/cateye_local')
viewer_active = json.loads((private / 'viewer_activity.json').read_text()).get('active')
registration_times = [r['first_at'] - first['at'] for r in passive['rows']
                      if r['src'] == '192.168.1.210' and r['proto'] == 'udp' and r['dport'] == 32100]
report = {
    'at': time.time(), 'model': 'midr.cateye.ph300',
    'cloud_wake_calls': first['cloud_wake_calls'] + armed['cloud_wake_calls'],
    'cloud_key_refresh_calls': 0, 'video_requested': False,
    'production_configuration_changed': False,
    'first_cloud_wake_ms': first['elapsed_ms'],
    'first_device_registration_ms': round(min(registration_times) * 1000),
    'prearmed_cloud_wake_ms': armed['cloud_wake_ms'],
    'prearmed_local_handshake_ready_ms': armed['ready_ms'],
    'local_ready_after_final_observed_port_ms': armed['ready_ms'] - armed['local_observed_ports'][-1]['ms'],
    'prearmed_client_external_coordination_sent': bool(armed['external_coordination']),
    'pure_local_after_idle': {k: idle[k] for k in (
        'quiet_s_before_probe', 'cloud_called', 'cloud_wake_sent', 'cloud_key_refresh_sent',
        'external_coordination', 'business_state', 'handshake_elapsed_ms', 'local_replies')},
    'coordination_only_without_miot_wake': {
        'elapsed_ms': servers['elapsed_ms'], 'server_source': servers['server_source'],
        'reply_count': len(servers['replies']),
        'first_reply_ms': min(r['ms'] for r in servers['replies']),
        'statuses': sorted(set(r.get('status') for r in servers['replies'] if r.get('status'))),
        'peer_count': len(servers['peers']), 'ready': 'ready_ms' in servers,
        'status_meaning': 'Non-success response; exact status mapping not established.'},
    'legacy_metadata_api': read('legacy_vendor_metadata_result.json'),
    'cached_coordination_addresses': {'count': 3, 'all_loopback': True,
        'encrypted_original_matches_cache': True,
        'consequence': 'Not usable as external coordination servers; video auth keys are a separate field.'},
    'conclusion': 'Known dynamic ports work during authorized wake but did not cold-wake the idle device. No validated replacement for current on-demand cloud wake.',
    'limits': ['P2P ready is not a decoded or browser-presented frame.',
               'MIoT busy and quiet duration do not independently measure electrical sleep.',
               'Server non-success does not by itself prove whether registration, identity, or policy caused rejection.'],
    'final_state': {'streams': streams, 'viewer_active': viewer_active}
}
(root / 'dynamic_port_experiments.json').write_text(json.dumps(report, indent=2))
print(json.dumps(report), flush=True)
