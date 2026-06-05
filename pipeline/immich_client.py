from __future__ import annotations

import base64
from typing import AsyncIterator

import httpx
from loguru import logger


class ImmichClient:
    def __init__(self, base_url: str, api_key: str) -> None:
        self.base_url = base_url.rstrip("/")
        self._headers = {"x-api-key": api_key, "Accept": "application/json"}

    async def list_assets(self, page: int = 1, page_size: int = 1000) -> list[dict]:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/api/search/metadata",
                headers=self._headers,
                json={"page": page, "size": page_size},
                timeout=30.0,
            )
            resp.raise_for_status()
            return resp.json().get("assets", {}).get("items", [])

    async def iter_all_assets(self, page_size: int = 500) -> AsyncIterator[dict]:
        page = 1
        while True:
            assets = await self.list_assets(page=page, page_size=page_size)
            if not assets:
                break
            for asset in assets:
                yield asset
            if len(assets) < page_size:
                break
            page += 1
            logger.debug(f"Fetched page {page - 1}, got {len(assets)} assets")

    async def get_thumbnail_b64(self, asset_id: str, size: str = "thumbnail") -> str:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.base_url}/api/assets/{asset_id}/thumbnail",
                headers={**self._headers, "Accept": "image/jpeg"},
                params={"size": size},
                timeout=30.0,
            )
            resp.raise_for_status()
            return base64.b64encode(resp.content).decode()

    async def update_description(self, asset_id: str, description: str) -> None:
        async with httpx.AsyncClient() as client:
            resp = await client.put(
                f"{self.base_url}/api/assets/{asset_id}",
                headers=self._headers,
                json={"description": description},
                timeout=10.0,
            )
            resp.raise_for_status()
            logger.debug(f"Updated description for {asset_id}")

    async def get_asset(self, asset_id: str) -> dict:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.base_url}/api/assets/{asset_id}",
                headers=self._headers,
                timeout=10.0,
            )
            resp.raise_for_status()
            return resp.json()

    async def list_face_clusters(self) -> list[dict]:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.base_url}/api/people",
                headers=self._headers,
                timeout=30.0,
            )
            resp.raise_for_status()
            return resp.json().get("people", [])

    async def get_named_people(self) -> dict[str, str]:
        """Return {person_id: name} for all Immich people that have been named."""
        people = await self.list_face_clusters()
        return {
            p["id"]: p["name"]
            for p in people
            if p.get("name", "").strip()
        }

    async def get_asset_people(self, asset_id: str) -> list[dict]:
        """Return [{id, name}] for every face Immich detected in this asset.
        name may be empty string if the face cluster hasn't been named yet."""
        asset = await self.get_asset(asset_id)
        return [
            {"id": p["id"], "name": p.get("name", "").strip()}
            for p in asset.get("people", [])
            if p.get("id")
        ]

    async def ping(self) -> bool:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self.base_url}/api/server/ping",
                    headers=self._headers,
                    timeout=5.0,
                )
                return resp.status_code == 200
        except Exception:
            return False
