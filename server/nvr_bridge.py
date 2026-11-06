"""Fixed read-only history routes and an on-demand MSE relay for the door lock."""
import asyncio
import re
from aiohttp import web, WSMsgType

LOCK = 'http://127.0.0.1:18781'


def install(app):
    async def proxy(request):
        # This bridge is called by the existing protected NVR proxy only.
        if request.remote not in ('192.168.1.1', '192.168.1.100'):
            raise web.HTTPForbidden()
        tail = request.match_info['tail']
        if not re.fullmatch(r'(status|nvr/snapshots|nvr/history/(summary|recordings|events)|nvr/(snapshot|clip)/[A-Za-z0-9_-]{1,100})', tail):
            raise web.HTTPNotFound()
        range_headers = {'Range':request.headers['Range']} if 'Range' in request.headers else {}
        async with app['client'].get(LOCK + '/' + tail, params=request.query, headers=range_headers) as upstream:
            response = web.StreamResponse(status=upstream.status, headers={k:v for k,v in upstream.headers.items() if k.lower() in ('content-type','content-length','content-range','accept-ranges')})
            await response.prepare(request)
            async for chunk in upstream.content.iter_chunked(65536):
                await response.write(chunk)
            return response

    async def media(request):
        if request.remote != '192.168.1.100':
            raise web.HTTPForbidden()
        downstream = web.WebSocketResponse()
        await downstream.prepare(request)
        upstream = None
        tasks = []
        try:
            # Always request browser-compatible H.264; the upstream starts only here.
            upstream = await app['client'].ws_connect('http://127.0.0.1:11984/api/ws', params={'src':'haier_lock_live'})
            async def relay(source, target):
                async for item in source:
                    if item.type == WSMsgType.TEXT:
                        await target.send_str(item.data)
                    elif item.type == WSMsgType.BINARY:
                        await target.send_bytes(item.data)
                    else:
                        break
            tasks = [asyncio.create_task(relay(upstream, downstream)), asyncio.create_task(relay(downstream, upstream))]
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if upstream:
                await upstream.close()
            await downstream.close()
        return downstream

    app.router.add_get('/api/lock/media', media)
    app.router.add_get('/api/lock/{tail:.*}', proxy)
