import xml.etree.ElementTree as ET
from pathlib import Path
from typing import AsyncIterator
from urllib.parse import unquote
from loguru import logger

import aiofiles
import aiohttp

from models import NCItem

_DAV_NS = "DAV:"
_PROPFIND = b"""<?xml version="1.0" encoding="UTF-8"?>
<d:propfind xmlns:d="DAV:">
  <d:prop>
    <d:displayname/>
    <d:getcontentlength/>
    <d:resourcetype/>
  </d:prop>
</d:propfind>"""


class NextcloudClient:
    def __init__(self, url: str, username: str, password: str):
        self.url = url.rstrip("/")
        self.username = username
        self._auth = aiohttp.BasicAuth(username, password)
        self._dav_base = f"{self.url}/remote.php/dav/files/{username}"

    def _dav_url(self, path: str) -> str:
        clean = path.strip("/")
        return f"{self._dav_base}/{clean}" if clean else self._dav_base

    def _session(self) -> aiohttp.ClientSession:
        return aiohttp.ClientSession(auth=self._auth)

    async def test_connection(self) -> bool:
        try:
            async with self._session() as s:
                async with s.request(
                    "PROPFIND", self._dav_url(""),
                    headers={"Depth": "0"},
                    data=_PROPFIND,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as r:
                    return r.status in (200, 207)
        except Exception as e:
            logger.warning(f"Nextcloud connection failed: {e}")
            return False

    async def list_dir(self, path: str) -> list[NCItem]:
        async with self._session() as s:
            async with s.request(
                "PROPFIND", self._dav_url(path),
                headers={"Depth": "1"},
                data=_PROPFIND,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status not in (200, 207):
                    raise RuntimeError(f"PROPFIND {path} returned {resp.status}")
                body = await resp.text()
        return self._parse(body, path)

    def _parse(self, xml_body: str, request_path: str) -> list[NCItem]:
        root = ET.fromstring(xml_body)
        prefix = f"/remote.php/dav/files/{self.username}/"
        req_norm = request_path.strip("/")
        items = []

        for resp in root.findall(f"{{{_DAV_NS}}}response"):
            href = unquote(resp.findtext(f"{{{_DAV_NS}}}href", ""))
            if prefix not in href:
                continue
            item_path = href.split(prefix, 1)[1].rstrip("/")
            if item_path == req_norm:
                continue  # skip the directory itself

            propstat = resp.find(f"{{{_DAV_NS}}}propstat")
            if propstat is None:
                continue
            prop = propstat.find(f"{{{_DAV_NS}}}prop")
            if prop is None:
                continue

            rt = prop.find(f"{{{_DAV_NS}}}resourcetype")
            is_dir = rt is not None and rt.find(f"{{{_DAV_NS}}}collection") is not None
            size_el = prop.find(f"{{{_DAV_NS}}}getcontentlength")
            size = int(size_el.text) if size_el is not None and size_el.text else 0
            name = Path(item_path).name or item_path

            items.append(NCItem(name=name, path=item_path, is_dir=is_dir, size=size))

        return sorted(items, key=lambda x: (not x.is_dir, x.name.lower()))

    async def iter_all_files(self, path: str) -> AsyncIterator[NCItem]:
        items = await self.list_dir(path)
        for item in items:
            if item.is_dir:
                async for f in self.iter_all_files(item.path):
                    yield f
            else:
                yield item

    async def download_file(
        self,
        remote_path: str,
        local_path: Path,
        pause_event,
        cancel_event,
        progress_cb=None,
    ) -> int:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        url = self._dav_url(remote_path)
        downloaded = 0

        async with self._session() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=7200)) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"GET {remote_path} returned {resp.status}")

                async with aiofiles.open(local_path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(512 * 1024):
                        if cancel_event.is_set():
                            return downloaded
                        await pause_event.wait()  # blocks here when paused
                        await f.write(chunk)
                        downloaded += len(chunk)
                        if progress_cb:
                            await progress_cb(len(chunk))

        return downloaded
