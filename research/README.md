# Sanitized research evidence

Probe scripts and receipts behind the findings in the README. Values have been
scrubbed (device serial → 1234567890, LAN addresses → 192.168.1.x, MACs →
XX:XX:XX:XX:XX:XX); timings and verdicts are original.

Key documents | 关键材料:

- `route_exhaustion_results.json`, `route_matrix.json` — every candidate local
  route tried and why each failed.
- `dynamic_port_experiments.json` — cached-port and armed-window experiments.
- `cloud_transport_classification.json`, `cloud_wake_*` — what the cloud wake
  actually does on the wire.
- `pr2631_oauth_chain.json`, `pr2631_cached_chain.json` — the native-SDK
  alternative route (works, but needs the same cloud wake).
- `passive_*.json`, `local_*_probe.json` — passive observations while the
  device was awake/asleep.
- `*_acceptance.json` (in `../docs/`) — browser-level acceptance runs.

Scripts are frozen as-run; they reference the retired lab layout and are kept
for reproducibility, not as supported tooling.
