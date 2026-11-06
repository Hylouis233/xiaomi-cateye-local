set -eu
cateye_fifo=/tmp/cateye-cloud-triggered.fifo
rm -f "$cateye_fifo"
mkfifo -m 600 "$cateye_fifo"
tcpdump -n -i br-lan -U -s 4096 -w - 'host 192.168.1.210 and tcp port 443' > "$cateye_fifo" 2>/tmp/cateye-cloud-triggered-errors.txt &
cateye_capture_pid=$!
trap 'kill "$cateye_capture_pid" 2>/dev/null || true; wait "$cateye_capture_pid" 2>/dev/null || true; rm -f "$cateye_fifo"' EXIT HUP INT TERM
docker exec -i homeassistant python3 /config/cateye_local/research/classify_cloud_transport.py 25 cloud_transport_triggered.json < "$cateye_fifo"
