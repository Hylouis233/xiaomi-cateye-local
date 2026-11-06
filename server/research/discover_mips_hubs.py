import asyncio
import json
import os
from pathlib import Path
import time
from zeroconf import IPVersion, ServiceStateChange
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

os.umask(0o077)
async def main():
    found = {}
    tasks = set()
    report = {'at': time.time(), 'service_type': '_miot-central._tcp.local.', 'duration_s': 8}
    z = AsyncZeroconf(interfaces=['192.168.1.1'], ip_version=IPVersion.V4Only)
    async def resolve(kind, name):
        info = AsyncServiceInfo(kind, name)
        if await info.async_request(z.zeroconf, 1500):
            found[name] = {'port': info.port, 'addresses': info.parsed_addresses(),
                           'property_keys': [x.decode(errors='replace') for x in info.properties]}
    def changed(zeroconf, service_type, name, state_change):
        if state_change == ServiceStateChange.Removed:
            return
        task = asyncio.create_task(resolve(service_type, name))
        tasks.add(task)
        task.add_done_callback(tasks.discard)
    browser = AsyncServiceBrowser(z.zeroconf, report['service_type'], handlers=[changed])
    try:
        await asyncio.sleep(8)
        if tasks:
            await asyncio.gather(*list(tasks), return_exceptions=True)
    finally:
        await browser.async_cancel()
        await z.async_close()
    report['hub_count'] = len(found)
    report['hubs'] = list(found.values())
    Path('/config/cateye_local/research/mips_hub_discovery.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report), flush=True)

asyncio.run(main())
