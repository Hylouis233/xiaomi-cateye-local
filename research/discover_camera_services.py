"""Read-only standard discovery during the bounded wake; discard other devices."""
import asyncio
import json
import os
from pathlib import Path
import socket
import time
import uuid
import xml.etree.ElementTree as ET
from urllib.parse import urlparse
from zeroconf import IPVersion, ServiceStateChange
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

os.umask(0o077)
async def main():
    report = {'at': time.time(), 'camera_ip': '192.168.1.210', 'cloud_wake_sent': False,
              'mdns_services': [], 'onvif_camera_responses': [], 'other_responses_discarded': 0}
    await asyncio.sleep(5)
    z = AsyncZeroconf(interfaces=['192.168.1.1'], ip_version=IPVersion.V4Only)
    tasks = set()
    async def resolve(kind, name):
        info = AsyncServiceInfo(kind, name)
        if await info.async_request(z.zeroconf, 1000) and '192.168.1.210' in info.parsed_addresses():
            row = {'type': kind, 'port': info.port}
            if row not in report['mdns_services']:
                report['mdns_services'].append(row)
    def changed(zeroconf, service_type, name, state_change):
        if state_change == ServiceStateChange.Removed:
            return
        task = asyncio.create_task(resolve(service_type, name))
        tasks.add(task)
        task.add_done_callback(tasks.discard)
    types = ['_rtsp._tcp.local.', '_onvif._tcp.local.', '_hap._tcp.local.']
    browser = AsyncServiceBrowser(z.zeroconf, types, handlers=[changed])
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('192.168.1.1', 0))
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton('192.168.1.1'))
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
    sock.setblocking(False)
    payload = ('<?xml version="1.0"?><s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:a="http://schemas.xmlsoap.org/ws/2004/08/addressing" xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery" xmlns:dn="http://www.onvif.org/ver10/network/wsdl"><s:Header><a:MessageID>uuid:' + str(uuid.uuid4()) + '</a:MessageID><a:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery</a:To><a:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</a:Action></s:Header><s:Body><d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types></d:Probe></s:Body></s:Envelope>').encode()
    loop = asyncio.get_running_loop()
    try:
        for address in ('239.255.255.250', '192.168.1.210'):
            await loop.sock_sendto(sock, payload, (address, 3702))
        until = time.monotonic() + 8
        while time.monotonic() < until:
            try:
                data, peer = await asyncio.wait_for(loop.sock_recvfrom(sock, 8192), max(.01, until - time.monotonic()))
            except asyncio.TimeoutError:
                break
            if peer[0] != '192.168.1.210':
                report['other_responses_discarded'] += 1
                continue
            row = {'from_camera': True, 'probe_match': b'ProbeMatch' in data}
            try:
                root = ET.fromstring(data)
                row['service_ports'] = sorted({urlparse(x).port or (443 if urlparse(x).scheme == 'https' else 80)
                    for node in root.iter() if node.tag.endswith('XAddrs') and node.text
                    for x in node.text.split() if urlparse(x).hostname == '192.168.1.210'})
            except (ET.ParseError, ValueError):
                row['parse_failed'] = True
            report['onvif_camera_responses'].append(row)
    finally:
        sock.close()
        if tasks:
            await asyncio.gather(*list(tasks), return_exceptions=True)
        await browser.async_cancel()
        await z.async_close()
    report['types_queried'] = types
    Path('/config/cateye_local/research/camera_standard_discovery.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report), flush=True)

asyncio.run(main())
