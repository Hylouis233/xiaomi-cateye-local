set -eu
cateye_fifo=/tmp/cateye-passive-metadata.fifo
rm -f "$cateye_fifo"
mkfifo -m 600 "$cateye_fifo"
tcpdump -n -i br-lan -U -s 128 -w - 'ether host XX:XX:XX:XX:XX:XX and (udp or tcp)' > "$cateye_fifo" 2>/tmp/cateye-passive-metadata-errors.txt &
cateye_capture_pid=$!
trap 'kill "$cateye_capture_pid" 2>/dev/null || true; wait "$cateye_capture_pid" 2>/dev/null || true; rm -f "$cateye_fifo"' EXIT HUP INT TERM
docker exec -i homeassistant python3 /config/cateye_local/research/passive_metadata.py "${1:-45}" "${2:-passive_idle.json}" < "$cateye_fifo"
