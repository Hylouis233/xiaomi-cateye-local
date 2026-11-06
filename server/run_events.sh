#!/bin/sh
umask 077
trap 'docker exec homeassistant python /config/cateye_local/stop_worker.py mijia_events.py; exit 0' TERM INT
docker exec -e PYTHONDONTWRITEBYTECODE=1 homeassistant python /config/cateye_local/mijia_events.py >>/home/data/homeassistant/.storage/cateye_local/mijia_events.log 2>&1 &
child=$!
wait "$child"
