#!/bin/sh
umask 077
stop_viewer() { docker exec homeassistant python /config/cateye_local/stop_worker.py live_server.py; }
trap 'stop_viewer; exit 0' TERM INT
docker exec -e PYTHONDONTWRITEBYTECODE=1 homeassistant python /config/cateye_local/live_server.py >>/var/lib/homeassistant/.storage/cateye_local/live_server.log 2>&1 &
child=$!
wait "$child"
