# xiaomi-cateye-local

**Local-first video for the Xiaomi Smart Cat Eye (Mijia PH300 / `midr.cateye.ph300`) in Home Assistant.**
On-demand cloud wake-up, then pure-LAN H.265/Opus streaming through go2rtc — with cold-start research
notes on why a fully-local wake is not possible on this firmware.

小米智能猫眼（PH300）的 Home Assistant 本地优先视频接入：按需云端唤醒 + 局域网 H.265/Opus 直传
（经 go2rtc），并附完整冷启动研究记录（为什么这台固件做不到纯本地唤醒）。

---

## What it does | 功能

- **Live view on demand** — opening the camera card wakes the cat eye via the cloud (once), then all
  media stays on the LAN. Closing the card ends the session; nothing keeps the device awake.
- **Pure-LAN transport** — Xiaomi's CS2 media channel (H.265 video + Opus audio) is bridged straight
  into a patched go2rtc; HEVC-capable browsers play the original stream, others get a local H.264 path.
- **Power-safe by design** — no polling, no keep-alives, no scheduled wakes of the sleeping device;
  credentials are re-fetched at most once per session on failure.
- **Frigate recording + face recognition** — continuous/event clips land in Frigate; recognized faces
  are surfaced in HA using Frigate's local models.
- **Device events** — doorbell / passerby / loitering events from the MIoT cloud subscription trigger
  bounded capture windows (event history kept locally, 90 days).
- **Measured performance** (real browser, LAN): ~0.45 s buffer after first frame, first frame ≈ 8–10 s
  including the cold wake.

实时画面按需查看：点开才唤醒（一次云端授权），媒体全程走局域网；H.265 原流直供（兼容浏览器走
本地 H.264）；不带任何保活/轮询，合上即断，不惊扰休眠设备；录像与识别交给本机 Frigate；
门铃/经过/停留事件触发有界采集。实测浏览器 LAN 首帧约 8–10 秒（含唤醒），播发后缓冲约 0.45 秒。

## How it works | 工作原理

```
                    ┌────────────────────────── HA host (Docker) ──────────────────────────┐
 Browser ──MSE──▶   │  HA lovelace card ─▶ live_server.py ─▶ go2rtc (patched)              │
 (same-origin WSS)  │      │                    ▲ LAN CS2 media (H.265/Opus)               │
                    │      │ wake (1x, on open) │                                        │
                    │      ▼                    │                                        │
                    │  MIoT cloud auth ─────────┼─▶ Cat Eye PH300 (battery, sleeps)       │
                    │  Frigate ◀── recording    └──────────────────────────────────────────┘
```

- `server/live_server.py` — session manager: prepare / heartbeat / stop, bounded timeouts, the
  session-scoped MSE bridge, and the old LAN-WebRTC path.
- `server/video_probe.py` + `server/cloud_wake.py` — on-demand cloud authorization, wake and re-key.
- `server/activity_collector.py` — passive LAN observer that gates Frigate capture windows on real
  viewer activity (never wakes the device).
- `server/mijia_events.py` — lightweight MIoT event subscription (doorbell / passerby / loitering).
- `server/event_history.py` — local event log (SQLite, 90-day retention).
- `go2rtc/go2rtc-cold-start.patch`, `go2rtc/go2rtc-first-frame.patch` — cold-start deadline and
  first-frame handling for go2rtc v1.9.15 (`7c05066`); stock binary stays untouched beside it.
- `homeassistant/` — HA package (REST command wiring) and the two Lovelace cards.
- `research/` — sanitized evidence and probe scripts behind every claim below.

## Findings | 研究结论（重点）

Firmware `5.3.1_0347` (hw `BL616_ECO`, miio `0.0.9`):

1. **No RTSP/ONVIF/HomeKit** and no open service ports while active (80/443/554/8554/8899/2020/8000/8080 tested).
2. **The only viable media path is Xiaomi's CS2** after a cloud-authorized wake; the LAN leg is plain
   H.265/Opus — that part is fully local.
3. **Pure-local cold wake is not achievable** on this firmware. Exhaustively tested: local actions 2/3,
   `wakeup_host`/`wakeup`, `Start P2P Stream 12/1`, screen-light actions, WoWLAN magic packets, cached
   dynamic ports, and the native SDK without an explicit wake (see `research/route_exhaustion_results.json`,
   `research/dynamic_port_experiments.json`). Local reads return `-30012 busy` while the device sleeps.
4. **A wake without a viewer is wasteful** — the device registers to CS2 ~4.5 s after the cloud call but
   stays busy; every extra wake costs battery for nothing. Hence: wake only on open, once.
5. Video credentials (`vendor_params`) delivered by the cloud may embed loopback coordinator addresses —
   they authorize the LAN media leg but are not usable as a public rendezvous.

固件 5.3.1_0347：无 RTSP/ONVIF；活跃期端口全关；唯一可行媒体路径是云端授权唤醒后的 CS2（局域网段为
裸 H.265/Opus）；纯本地冷唤醒已穷尽验证为不可行（本地 action/唤醒 action/P2P/WoWLAN/缓存端口/SDK 不唤醒
均失败，休眠时本地读返回 busy）；无观看者的唤醒只会白白耗电。

## Requirements | 依赖

- Home Assistant (any host; examples assume Docker with `/config`)
- Python ≥ 3.11 (`aiohttp`, `cryptography`), go2rtc **v1.9.15** + our patch
- Optional: Frigate (recording / face recognition), an MQTT broker
- **Xiaomi miot SDK is NOT bundled** — it is proprietary (Xiaomi Miloco license). Copy the `miot`
  package out of your own `hass-xiaomi-miot` / `xiaomi_home` installation into `server/sdk/miot/`
  and follow that project's license. See `NOTICE`.

All credentials (account session, device token, video keys) are read at runtime from HA storage —
**no secrets in this repository**, and the docs contain no real device IDs, tokens or signing URLs.

## Quick start | 快速开始

```sh
git clone https://github.com/Hylouis233/xiaomi-cateye-local
cd xiaomi-cateye-local
# 1) vendor the miot SDK (see NOTICE), then:
cp -r server /config/cateye_local          # or wherever your HA config lives
patch -d go2rtc-src -p1 < go2rtc/go2rtc-cold-start.patch   # build go2rtc v1.9.15
# 2) point homeassistant/packages/cateye_same_origin.yaml at your server
# 3) open the camera card — first open performs the one-time cloud auth+wake
```

Detailed runbook, tuning and acceptance data: [`docs/OPERATIONS.md`](docs/OPERATIONS.md).

## Status | 状态

Production use since 2026-10 on one PH300 (battery ~months per charge with daily live views).
Treat the research JSONs as evidence, not API documentation. PRs welcome — especially for newer
firmware (a local cold-wake on 5.3.2+ would retire the cloud leg entirely).

## Credits | 致谢

- [AlexxIT/go2rtc](https://github.com/AlexxIT/go2rtc) (MIT) — media hub; our patches build on v1.9.15.
- [al-one/hass-xiaomi-miot](https://github.com/al-one/hass-xiaomi-miot) — MIoT integration lineage
  and the vendor SDK source (not redistributed here).
- [miiot/micam](https://github.com/miiot/micam) — reference for the camera P2P stack studied during
  research (not included).

## License

MIT for everything in this repository except third-party notices in [`NOTICE`](NOTICE).
