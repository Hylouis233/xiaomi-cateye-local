#!/usr/bin/env python3
"""Stage Xiaomi cloud pre-roll for the door peephole.

Reads the Mi Home session in xiaomiio.json, lists cloud clips, and writes
clear fMP4 segments for clips Frigate does not already have. A later step on
nvr-host transcodes and inserts them. This process never wakes the camera and
never prints tokens, playlists, or file ids.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path
from urllib.parse import quote, urlparse

import aiohttp
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

STORE = Path("/config/.storage/cateye_local/xiaomiio.json")
STAGE = Path("/config/.storage/cateye_local/preroll-stage")
DID = "1234567890"
MODEL = "midr.cateye.ph300"
EVENT_URL = "https://business.smartcamera.api.io.mi.com/common/app/get/eventlist"
M3U8_URL = "https://business.smartcamera.api.io.mi.com/common/app/m3u8"
FRIGATE = "http://192.168.1.100:19002/api/cateye/recordings"
ACCOUNT = "https://account.xiaomi.com/pass/serviceLogin?sid=xiaomiio&_json=true"
UA_LOGIN = "APP/com.xiaomi.mihome APPV/6.0.103 iosPassportSDK/3.9.0 iOS/14.4 miHSTS"
UA_MIOT = "Android-7.1.1-1.0.0 APP/xiaomi.smarthome APPV/62830"
REFRESH_AFTER = 20 * 3600


def rc4(key: bytes, data: bytes) -> bytes:
    s = list(range(256))
    j = 0
    for i in range(256):
        j = (j + s[i] + key[i % len(key)]) & 255
        s[i], s[j] = s[j], s[i]
    i = j = 0
    out = bytearray()
    buf = b"\x00" * 1024 + data
    for n, b in enumerate(buf):
        i = (i + 1) & 255
        j = (j + s[i]) & 255
        s[i], s[j] = s[j], s[i]
        if n >= 1024:
            out.append(b ^ s[(s[i] + s[j]) & 255])
    return bytes(out)


def sha1_sign(method: str, url: str, dat: dict, signed_nonce: str) -> str:
    path = urlparse(url).path
    if path[:5] == "/app/":
        path = path[4:]
    arr = [method.upper(), path]
    arr.extend(f"{k}={v}" for k, v in dat.items())
    arr.append(signed_nonce)
    return base64.b64encode(hashlib.sha1("&".join(arr).encode()).digest()).decode()


def make_nonce() -> str:
    return base64.b64encode(os.urandom(8) + int(time.time() // 60).to_bytes(4, "big")).decode()


def load_store() -> dict:
    data = json.loads(STORE.read_text())
    for key in ("userId", "passToken", "ssecurity", "serviceToken"):
        if not data.get(key):
            raise SystemExit(f"login fail missing {key}")
    return data


def save_store(data: dict) -> None:
    raw = json.dumps(data, separators=(",", ":")).encode()
    tmp = STORE.with_suffix(".json.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, raw)
    finally:
        os.close(fd)
    os.chmod(tmp, 0o600)
    os.replace(tmp, STORE)
    os.chmod(STORE, 0o600)


def backup_store() -> Path:
    bak = STORE.with_suffix(".json.bak")
    shutil.copy2(STORE, bak)
    os.chmod(bak, 0o600)
    return bak


def parse_account(raw: bytes) -> dict:
    if raw.startswith(b"&&&START&&&"):
        raw = raw[11:]
    return json.loads(raw) if raw else {}


class Cloud:
    def __init__(self, store: dict):
        self.store = store
        self.session: aiohttp.ClientSession | None = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(
            headers={
                "User-Agent": UA_MIOT,
                "Accept-Encoding": "identity",
                "MIOT-ENCRYPT-ALGORITHM": "ENCRYPT-RC4",
                "x-xiaomi-protocal-flag-cli": "PROTOCAL-HTTP2",
            },
            cookie_jar=aiohttp.CookieJar(unsafe=True),
            timeout=aiohttp.ClientTimeout(total=40),
        )
        return self

    async def __aexit__(self, *_):
        if self.session:
            await self.session.close()

    def cookies(self) -> dict:
        return {
            "userId": str(self.store["userId"]),
            "yetAnotherServiceToken": self.store["serviceToken"],
            "serviceToken": self.store["serviceToken"],
            "locale": "zh_CN",
            "timezone": "GMT+08:00",
            "is_daylight": "0",
            "dst_offset": "0",
            "channel": "MI_APP_STORE",
        }

    async def call(self, url: str, payload: dict) -> tuple[int, str, int | None]:
        nonce = make_nonce()
        signed = base64.b64encode(
            hashlib.sha256(base64.b64decode(self.store["ssecurity"]) + base64.b64decode(nonce)).digest()
        ).decode()
        params = {"data": json.dumps(payload, separators=(",", ":"))}
        params["rc4_hash__"] = sha1_sign("GET", url, params, signed)
        for key, value in list(params.items()):
            params[key] = base64.b64encode(rc4(base64.b64decode(signed), str(value).encode())).decode()
        params["signature"] = sha1_sign("GET", url, params, signed)
        params["ssecurity"] = self.store["ssecurity"]
        params["_nonce"] = nonce
        assert self.session is not None
        async with self.session.get(url, params=params, cookies=self.cookies()) as resp:
            text = await resp.text()
            http = resp.status
        body = text
        if text and not text.startswith("{") and not text.startswith("#") and "message" not in text[:80]:
            try:
                body = rc4(base64.b64decode(signed), base64.b64decode(text)).decode()
            except Exception:
                body = text
        code = None
        if body.startswith("{"):
            try:
                code = json.loads(body).get("code")
            except Exception:
                code = None
        return http, body, code

    def invalid(self, http: int, body: str, code: int | None) -> bool:
        if http == 401 or code in (2, 3):
            return True
        return "serviceToken" in body[:180]

    async def refresh(self) -> str:
        cookies = {
            "userId": str(self.store["userId"]),
            "passToken": self.store["passToken"],
            "sdkVersion": "3.9",
        }
        if self.store.get("deviceId"):
            cookies["deviceId"] = self.store["deviceId"]
        assert self.session is not None
        async with self.session.get(ACCOUNT, headers={"User-Agent": UA_LOGIN}, cookies=cookies) as resp:
            data = parse_account(await resp.read())
            http = resp.status
        code = data.get("code")
        if http != 200 or code != 0:
            return f"code={code} http={http}"
        for key in ("location", "nonce", "ssecurity", "passToken", "userId"):
            if key not in data:
                return f"missing={key}"
        location = data["location"]
        if "clientSign=" not in location:
            sign = base64.b64encode(
                hashlib.sha1(f"nonce={data['nonce']}&{data['ssecurity']}".encode()).digest()
            ).decode()
            location = location + "&clientSign=" + quote(sign)
        async with self.session.get(location, headers={"User-Agent": UA_LOGIN}, allow_redirects=True) as resp:
            await resp.read()
            service = resp.cookies.get("serviceToken")
            service = service.value if service else None
        if not service:
            for cookie in self.session.cookie_jar:
                if cookie.key == "serviceToken" and cookie.value:
                    service = cookie.value
        if not service:
            return "no_service_token"
        self.store["userId"] = str(data.get("userId") or self.store["userId"])
        self.store["cUserId"] = str(data.get("cUserId") or self.store.get("cUserId") or "")
        self.store["passToken"] = data["passToken"]
        self.store["ssecurity"] = data["ssecurity"]
        self.store["serviceToken"] = service
        self.store["saved_at"] = time.time()
        save_store(self.store)
        return "ok"


def frigate_recordings(after: float, before: float) -> list[dict]:
    import urllib.request

    url = f"{FRIGATE}?after={after:.3f}&before={before:.3f}"
    with urllib.request.urlopen(url, timeout=15) as resp:
        data = json.load(resp)
    if not isinstance(data, list):
        raise RuntimeError("frigate recordings shape")
    rows = []
    for row in data:
        rows.append({
            "start": float(row["start_time"]),
            "end": float(row["end_time"]),
        })
    return rows


def covered(recs: list[dict], start: float) -> bool:
    for row in recs:
        if abs(row["start"] - start) < 1.5:
            return True
        if row["start"] <= start <= row["end"]:
            return True
    return False


def unpad(plain: bytes) -> bytes:
    pad = plain[-1]
    if 1 <= pad <= 16 and plain.endswith(bytes([pad]) * pad):
        return plain[:-pad]
    return plain


async def list_clips(cloud: Cloud, begin_ms: int, end_ms: int) -> list[dict]:
    clips = []
    cursor = end_ms
    for _ in range(4):
        http, body, code = await cloud.call(EVENT_URL, {
            "did": DID,
            "model": MODEL,
            "doorBell": True,
            "eventType": "Default",
            "needMerge": True,
            "sortType": "DESC",
            "region": "CN",
            "language": "zh_CN",
            "beginTime": begin_ms,
            "endTime": cursor,
            "limit": 20,
        })
        if cloud.invalid(http, body, code):
            raise PermissionError(str(code))
        if not body.startswith("{"):
            raise RuntimeError(f"eventlist http={http}")
        data = json.loads(body).get("data") or {}
        for row in data.get("thirdPartPlayUnits") or []:
            if not isinstance(row, dict) or not row.get("fileId"):
                continue
            dur = float(row.get("duration") or 0)
            if dur < 1:
                continue
            clips.append({
                "fileId": row["fileId"],
                "create": int(row["createTime"]),
                "duration": dur,
                "isAlarm": bool(row.get("isAlarm")),
                "expire": int(row.get("expireTime") or 0),
            })
        nxt = data.get("nextTime")
        if not data.get("isContinue") or not nxt or int(nxt) >= cursor:
            break
        cursor = int(nxt)
    return clips


async def stage_clip(cloud: Cloud, clip: dict, dest: Path) -> int:
    http, body, code = await cloud.call(M3U8_URL, {
        "did": DID,
        "model": MODEL,
        "fileId": clip["fileId"],
        "isAlarm": clip["isAlarm"],
        "videoCodec": "H265",
    })
    if cloud.invalid(http, body, code):
        raise PermissionError(str(code))
    text = body
    if text.startswith("{"):
        meta = json.loads(text)
        nested = meta.get("result") or meta.get("data") or ""
        if isinstance(nested, dict):
            nested = nested.get("m3u8") or nested.get("url") or ""
        text = nested if isinstance(nested, str) else ""
    if isinstance(text, str) and text.startswith("http"):
        assert cloud.session is not None
        async with cloud.session.get(text) as resp:
            text = await resp.text()
    if not isinstance(text, str) or "#EXT" not in text:
        raise RuntimeError(f"m3u8 http={http}")
    key = None
    iv = None
    urls = []
    for line in text.splitlines():
        if line.startswith("#EXT-X-KEY") and "METHOD=AES-128" in line:
            uri = line.split('URI="', 1)[1].split('"', 1)[0]
            iv_hex = line.split("IV=0x", 1)[1].split(",", 1)[0].strip()
            assert cloud.session is not None
            async with cloud.session.get(uri) as resp:
                key = await resp.read()
            iv = bytes.fromhex(iv_hex)
        elif line.startswith("http"):
            urls.append(line)
    if not key or len(key) != 16 or iv is None or not urls:
        raise RuntimeError("hls key")
    dest.mkdir(parents=True, exist_ok=True)
    os.chmod(dest, 0o700)
    count = 0
    for index, url in enumerate(urls):
        assert cloud.session is not None
        async with cloud.session.get(url) as resp:
            blob = await resp.read()
            status = resp.status
        if status != 200 or len(blob) < 16 or len(blob) % 16:
            raise RuntimeError(f"seg http={status}")
        dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
        plain = unpad(dec.update(blob) + dec.finalize())
        if plain[4:8] != b"ftyp":
            raise RuntimeError("seg not fmp4")
        (dest / f"seg{index}.mp4").write_bytes(plain)
        count += 1
    (dest / "meta.json").write_text(json.dumps({
        "start": clip["create"] / 1000,
        "playlist_duration": clip["duration"],
    }))
    return count


async def run(args: argparse.Namespace) -> int:
    store = load_store()
    age = int(time.time() - float(store.get("saved_at") or 0))
    async with Cloud(store) as cloud:
        refreshed = False
        if args.refresh or age >= REFRESH_AFTER:
            backup_store()
            result = await cloud.refresh()
            if result != "ok":
                print(f"refresh fail {result}")
                if args.refresh:
                    return 2
            else:
                refreshed = True
                print("refresh ok")
        now = time.time()
        begin = now - args.lookback_hours * 3600

        async def clips_or_refresh():
            nonlocal refreshed
            try:
                return await list_clips(cloud, int(begin * 1000), int(now * 1000))
            except PermissionError:
                if refreshed:
                    bak = STORE.with_suffix(".json.bak")
                    if bak.is_file():
                        shutil.copy2(bak, STORE)
                        os.chmod(STORE, 0o600)
                        cloud.store.clear()
                        cloud.store.update(load_store())
                        try:
                            found = await list_clips(cloud, int(begin * 1000), int(now * 1000))
                            print("refresh reverted")
                            return found
                        except PermissionError:
                            pass
                    print("login fail after refresh")
                    return None
                backup_store()
                result = await cloud.refresh()
                if result != "ok":
                    print(f"refresh fail {result}")
                    return None
                refreshed = True
                print("refresh ok")
                try:
                    return await list_clips(cloud, int(begin * 1000), int(now * 1000))
                except PermissionError:
                    print("login fail after refresh")
                    return None

        clips = await clips_or_refresh()
        if clips is None:
            return 2
        clips = list({clip["create"]: clip for clip in clips}.values())
        recs = frigate_recordings(begin - 60, now + 60)
        print(f"login ok age_s={int(time.time() - float(cloud.store.get('saved_at') or 0))} clips={len(clips)} recs={len(recs)}")
        staged = 0
        for clip in clips:
            start = clip["create"] / 1000
            if clip["expire"] and clip["expire"] / 1000 < now:
                print(f"clip skip {start:.3f} expired")
                continue
            if covered(recs, start):
                print(f"clip skip {start:.3f} present")
                continue
            end = start + clip["duration"]
            later = [row["start"] for row in recs if row["start"] > start + 1]
            nxt = min(later) if later else None
            # Only the pre-roll that belongs to a local recording. A cloud clip
            # that ends more than 15s before the next file is a separate visit.
            if nxt is None or end < nxt - 15:
                print(f"clip skip {start:.3f} solo")
                continue
            if args.check:
                print(f"clip need {start:.3f} dur={clip['duration']:.3f}")
                continue
            job = STAGE / str(clip["create"])
            if (job / "meta.json").is_file() and any(job.glob("seg*.mp4")):
                print(f"clip staged {start:.3f} existing")
                staged += 1
                continue
            if job.exists():
                shutil.rmtree(job)
            count = await stage_clip(cloud, clip, job)
            print(f"clip staged {start:.3f} segs={count}")
            staged += 1
        print(f"staged {staged}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lookback-hours", type=float, default=36)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    try:
        code = asyncio.run(run(args))
    except Exception as exc:
        print(f"error {type(exc).__name__}")
        code = 1
    raise SystemExit(code)


if __name__ == "__main__":
    main()
