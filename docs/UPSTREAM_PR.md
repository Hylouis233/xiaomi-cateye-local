# Upstream PR draft and ready-to-use fork

## Ready-to-use fork (no patching needed)

**[`Hylouis233/go2rtc`](https://github.com/Hylouis233/go2rtc)** is go2rtc `master`
(`990f4de3cc`, v1.9.15) with the cold-start fixes already merged into its default branch.
Anyone who just wants a working build can use it directly:

```sh
git clone https://github.com/Hylouis233/go2rtc
cd go2rtc
CGO_ENABLED=0 go build ./...
CGO_ENABLED=0 go test -count=1 ./pkg/xiaomi/miss/...
```

The fork root contains `COLD_START_PATCH.md` (what changed and why) and
`go2rtc-cold-start.patch` (the exact diff in standard `git apply` format).

## Two patches in this repository

| File | Purpose |
| --- | --- |
| `go2rtc/go2rtc-cold-start.patch` | The **deployment** patch used by this project. It also adds `CATEYE_PHASE` timing prints and touches `client.go`; intended for local cold-start diagnosis, not for upstream. |
| `go2rtc/go2rtc-upstream-pr.patch` | The **minimal upstream PR** patch: 5 files, no diagnostic prints, no environment-specific code. Applies cleanly to go2rtc `master` with `git apply`; `go test ./pkg/xiaomi/miss/...` and `go build ./...` pass. |

Both patch go2rtc v1.9.15 base. The upstream-PR patch was verified against a fresh
clone of `master` (2026-11-05): `git apply --check` OK, unit tests green, full build OK.

## Upstream PR (submitted)

> Status: **submitted 2026-11-06** by `Hylouis233`. PR: [#2654](https://github.com/AlexxIT/go2rtc/pull/2654)
> (OPEN, branch `Hylouis233:xiaomi-miss-cold-start` → `AlexxIT/go2rtc:master`, 5 files under
> `pkg/xiaomi/miss/**`). A model report was also posted to the upstream known-cameras
> [issue #1982](https://github.com/AlexxIT/go2rtc/issues/1982#issuecomment-6686794503).
> The body below is the submitted text (desensitized). Follow the PR link for review status.

### Suggested PR title

`xiaomi/miss: reliable cold start for battery doorbells (cs2 ACK + parameter sets)`

### Suggested PR body

```markdown
## What
Four related fixes to `pkg/xiaomi/miss/**` for Xiaomi `miss` cameras on the `cs2`
transport, observed on a battery-powered camera doorbell that emits HEVC. The device
sleeps; after a cloud wake it answers LAN discovery late and splits codec parameter
sets across access units.

1. `cs2/conn.go`: command ACK lifecycle via a buffered channel guarded by a mutex,
   replacing the ticker + atomic that a late ACK could restart. An auth response
   counts as delivery proof only while an auth request is pending. Discovery and
   readiness negotiation get separate bounded deadlines.
2. `producer.go`: collect HEVC VPS+SPS(+PPS) / H.264 SPS+PPS across access units,
   tolerate AUD/SEI prefixes, release as soon as parameter sets are complete without
   waiting for an audio packet, and replay buffered initial media (4 MiB cap) so the
   first keyframe is not lost during probing.
3. Regression tests: cold discovery recovery, ACK lifecycle (late / unrelated ACK),
   parameter-set splitting without audio.

## Why
The previous probe waited for one access unit containing VPS, then blocked on an
audio packet that a muted doorbell never sends. Cold starts timed out or produced a
black picture. A ticker-based ACK could also be kept alive by a late ACK from a
previous command.

## Testing
CGO_ENABLED=0 go test -count=1 ./pkg/xiaomi/miss/...
CGO_ENABLED=0 go build ./...
Applies cleanly to master (990f4de, 2026-11-05); gofmt clean. No changes outside
pkg/xiaomi/miss/**. Cloud wake / key retrieval is still required each session (per
the upstream Xiaomi source README); this PR only fixes the LAN media path after wake.
```

### Known-cameras issue comment (posted)

The model report was posted to the upstream known-cameras issue:
[#1982 comment](https://github.com/AlexxIT/go2rtc/issues/1982#issuecomment-6686794503).
It reports the public model identifier (`midr.cateye.ph300`), region, firmware version,
`cs2` transport, `xiaomi/mess` format, HEVC + Opus codecs, and that the cold-start
fixes above were needed — plus a pointer to the PR. It contains **no device ID, account
data, private network address, local path or environment-specific detail**; a model
identifier is public product information and is the point of that issue.

## Note on publishing

The fork and this repository are public and use noreply commit identities. All
device IDs, account credentials, private network addresses and local paths were
removed before publishing. On 2026-11-06 the PR was submitted upstream (#2654) and
the model report was posted to issue #1982, both with desensitized text (see links
above). The only device-specific string in the public comment is the public product
model identifier, which is the purpose of the known-cameras issue.
