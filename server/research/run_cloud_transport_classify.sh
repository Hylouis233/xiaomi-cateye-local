set -eu
cateye_fifo=/tmp/cateye-cloud-classify.fifo
rm -f "$cateye_fifo"
mkfifo -m 600 "$cateye_fifo"
tcpdump -n -i br-lan -U -s 160 -w - 'host 192.168.1.210 and host 111.13.40.5 and tcp port 443' > "$cateye_fifo" 2>/tmp/cateye-cloud-classify-errors.txt &
cateye_capture_pid=$!
trap 'kill "$cateye_capture_pid" 2>/dev/null || true; wait "$cateye_capture_pid" 2>/dev/null || true; rm -f "$cateye_fifo"' EXIT HUP INT TERM
docker exec -i homeassistant python3 /config/cateye_local/research/classify_cloud_transport.py < "$cateye_fifo"
