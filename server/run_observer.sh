#!/bin/sh
umask 077
pipe=/home/data/homeassistant/.storage/cateye_local/activity.pipe
test -p "$pipe" || mkfifo "$pipe"
/usr/bin/tcpdump -i br-lan -nn -s 160 -U -w "$pipe" 'src host 192.168.1.210 and (udp or tcp)' 2>>/home/data/homeassistant/.storage/cateye_local/observer_process.log &
capture=$!
stop_observer() { kill "$capture" 2>/dev/null; docker exec homeassistant python /config/cateye_local/stop_worker.py activity_collector.py; }
trap 'stop_observer; exit 0' TERM INT
docker exec -i -e PYTHONDONTWRITEBYTECODE=1 homeassistant python /config/cateye_local/activity_collector.py < "$pipe" >>/home/data/homeassistant/.storage/cateye_local/observer_process.log 2>&1 &
parser=$!
wait "$parser"
kill "$capture" 2>/dev/null
