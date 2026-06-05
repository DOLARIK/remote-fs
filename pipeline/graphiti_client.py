from __future__ import annotations

import os

from datetime import datetime, timezone

from graphiti_core import Graphiti
from graphiti_core.llm_client.openai_client import LLMConfig, OpenAIClient
from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient
from graphiti_core.nodes import EpisodeType
from loguru import logger

from .models import EventUpdate, PersonMatch


def _build_graphiti(host: str, port: int, ollama_base: str) -> Graphiti:
    from graphiti_core.driver.falkordb_driver import FalkorDriver

    llm = OpenAIClient(
        config=LLMConfig(
            api_key="ollama",
            model="gemma4:e4b",
            base_url=f"{ollama_base}/v1",
        )
    )
    embedder = OpenAIEmbedder(
        config=OpenAIEmbedderConfig(
            api_key="ollama",
            embedding_model="nomic-embed-text",
            base_url=f"{ollama_base}/v1",
        )
    )
    cross_encoder = OpenAIRerankerClient(
        config=LLMConfig(
            api_key="ollama",
            model="gemma4:e4b",
            base_url=f"{ollama_base}/v1",
        )
    )
    driver = FalkorDriver(host=host, port=port)
    return Graphiti(
        graph_driver=driver,
        llm_client=llm,
        embedder=embedder,
        cross_encoder=cross_encoder,
    )


class GraphitiClient:
    def __init__(
        self,
        falkordb_host: str = "localhost",
        falkordb_port: int = 6379,
        ollama_base: str = "http://localhost:11434",
    ) -> None:
        self._graphiti = _build_graphiti(falkordb_host, falkordb_port, ollama_base)

    async def init(self) -> None:
        await self._graphiti.build_indices_and_constraints()
        logger.info("Graphiti indices ready")

    async def add_photo_episode(
        self,
        asset_id: str,
        description: str,
        person_matches: list[PersonMatch],
        event_updates: list[EventUpdate],
    ) -> None:
        people_str = ", ".join(
            f"{pm.person_id} (confidence={pm.confidence:.2f})"
            for pm in person_matches
            if not pm.is_new_person
        )
        event_str = ", ".join(
            f"{eu.label or eu.event_id} (confidence={eu.label_confidence:.2f})"
            for eu in event_updates
        )
        body = (
            f"Photo {asset_id}: {description}"
            + (f" | People: {people_str}" if people_str else "")
            + (f" | Event: {event_str}" if event_str else "")
        )

        await self._graphiti.add_episode(
            name=f"photo_{asset_id}",
            episode_body=body,
            source=EpisodeType.text,
            source_description="AI photo analysis pipeline",
            reference_time=datetime.now(timezone.utc),
        )
        logger.debug(f"Graph episode added for {asset_id}")

    async def search(self, query: str, limit: int = 10) -> list[dict]:
        results = await self._graphiti.search(query, num_results=limit)
        return [
            {
                "fact": r.fact,
                "valid_at": str(r.valid_at) if hasattr(r, "valid_at") else None,
            }
            for r in results
        ]

    async def get_graph_stats(self) -> dict:
        """Query FalkorDB directly for node/edge counts and recent entities."""
        driver = self._graphiti.driver

        def _str(v) -> str:
            """FalkorDB may return Node/Edge objects — extract string safely."""
            if v is None:
                return ""
            if isinstance(v, str):
                return v
            if hasattr(v, "properties"):
                return str(v.properties.get("name", v.properties.get("uuid", str(v))))
            return str(v)

        async def _query(cypher: str) -> list[dict]:
            rows, _, _ = await driver.execute_query(cypher)
            return rows or []

        try:
            rows = await _query("MATCH (n) RETURN labels(n)[0] AS label, count(n) AS cnt")
            node_counts = {_str(r["label"]): r["cnt"] for r in rows if r.get("label") is not None}
        except Exception as e:
            logger.warning(f"Graph node count query failed: {e}")
            node_counts = {}

        try:
            rows = await _query("MATCH ()-[r]->() RETURN type(r) AS rel, count(r) AS cnt")
            edge_counts = {_str(r["rel"]): r["cnt"] for r in rows if r.get("rel") is not None}
        except Exception as e:
            logger.warning(f"Graph edge count query failed: {e}")
            edge_counts = {}

        try:
            rows = await _query(
                "MATCH (n:Entity) RETURN n.name AS name, n.summary AS summary LIMIT 40"
            )
            entities = [
                {"name": _str(r.get("name")), "summary": _str(r.get("summary"))}
                for r in rows if r.get("name")
            ]
        except Exception as e:
            logger.warning(f"Graph entity query failed: {e}")
            entities = []

        try:
            rows = await _query(
                "MATCH (a:Entity)-[r]->(b:Entity) "
                "RETURN a.name AS src, r.fact AS fact, b.name AS tgt LIMIT 40"
            )
            edges = [
                {"src": _str(r.get("src")), "fact": _str(r.get("fact")), "tgt": _str(r.get("tgt"))}
                for r in rows if r.get("src") and r.get("tgt")
            ]
        except Exception as e:
            logger.warning(f"Graph edge query failed: {e}")
            edges = []

        return {
            "node_counts": node_counts,
            "edge_counts": edge_counts,
            "entities": entities,
            "edges": edges,
        }

    async def close(self) -> None:
        await self._graphiti.close()
