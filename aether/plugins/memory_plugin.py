"""Local Chroma memory with tier caps, summaries, and portable JSON backups."""

from __future__ import annotations

import json
import hashlib
import math
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from aether.agent_loop import AetherPlugin, ToolResult, ToolSpec
from aether.hardware_probe import TierConfig
from aether.llm_provider import LLMProviderError, ManagedLLM


@dataclass
class MemoryRecord:
    id: str
    text: str
    kind: str
    created_at: str
    metadata: dict


class LocalHashEmbeddingFunction:
    """Small deterministic lexical embedding with no model download or telemetry.

    Word tokens and character trigrams make search tolerant of minor spelling
    differences.  Chroma still provides indexed nearest-neighbour retrieval,
    while memory remains usable on Lite machines and completely offline.
    """

    dimensions = 384

    @staticmethod
    def name() -> str:
        return "aether-local-hash-v1"

    def __call__(self, input: list[str]) -> list[list[float]]:
        return [self.embed(text) for text in input]

    def embed_query(self, input: list[str]) -> list[list[float]]:
        """Chroma 1.5+ calls a dedicated query hook when one is present."""
        return self(input)

    def embed(self, text: str) -> list[float]:
        normalized = re.sub(r"\s+", " ", text.lower()).strip()
        features = re.findall(r"[\w']+", normalized)
        features.extend(normalized[index : index + 3] for index in range(max(len(normalized) - 2, 0)))
        vector = [0.0] * self.dimensions
        for feature in features:
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
            slot = int.from_bytes(digest[:4], "little") % self.dimensions
            vector[slot] += 1.0 if digest[4] & 1 else -1.0
        magnitude = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / magnitude for value in vector]

    def get_config(self) -> dict:
        return {"dimensions": self.dimensions}

    @staticmethod
    def build_from_config(config: dict) -> "LocalHashEmbeddingFunction":
        if int(config.get("dimensions", LocalHashEmbeddingFunction.dimensions)) != LocalHashEmbeddingFunction.dimensions:
            raise ValueError("Unsupported Aether local embedding dimensions.")
        return LocalHashEmbeddingFunction()


class ChromaMemoryStore:
    def __init__(self, path: Path, collection_name: str = "aether_memory") -> None:
        try:
            import chromadb
        except ImportError as exc:
            raise RuntimeError("Memory requires the optional 'chromadb' package.") from exc
        path.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=str(path))
        self.collection = self.client.get_or_create_collection(
            collection_name,
            embedding_function=LocalHashEmbeddingFunction(),
            metadata={"embedding": LocalHashEmbeddingFunction.name()},
        )

    def count(self) -> int:
        return self.collection.count()

    def add(self, record: MemoryRecord) -> None:
        metadata = dict(record.metadata)
        metadata.update({"kind": record.kind, "created_at": record.created_at})
        self.collection.add(ids=[record.id], documents=[record.text], metadatas=[metadata])

    def update(self, record_id: str, text: str, metadata: Optional[dict] = None) -> None:
        payload: dict = {"ids": [record_id], "documents": [text]}
        if metadata is not None:
            payload["metadatas"] = [metadata]
        self.collection.update(**payload)

    def delete(self, record_ids: list[str]) -> None:
        if record_ids:
            self.collection.delete(ids=record_ids)

    def all(self, limit: Optional[int] = None) -> list[MemoryRecord]:
        kwargs = {"include": ["documents", "metadatas"]}
        if limit is not None:
            kwargs["limit"] = limit
        raw = self.collection.get(**kwargs)
        records = []
        for record_id, text, metadata in zip(
            raw.get("ids", []), raw.get("documents", []), raw.get("metadatas", [])
        ):
            metadata = dict(metadata or {})
            records.append(
                MemoryRecord(
                    id=record_id,
                    text=text or "",
                    kind=str(metadata.pop("kind", "memory")),
                    created_at=str(metadata.pop("created_at", "")),
                    metadata=metadata,
                )
            )
        return sorted(records, key=lambda record: record.created_at, reverse=True)

    def search(self, query: str, limit: int) -> list[MemoryRecord]:
        raw = self.collection.query(
            query_texts=[query],
            n_results=max(1, limit),
            include=["documents", "metadatas", "distances"],
        )
        ids = (raw.get("ids") or [[]])[0]
        documents = (raw.get("documents") or [[]])[0]
        metadatas = (raw.get("metadatas") or [[]])[0]
        distances = (raw.get("distances") or [[]])[0]
        records = []
        for record_id, text, metadata, distance in zip(ids, documents, metadatas, distances):
            metadata = dict(metadata or {})
            metadata["distance"] = float(distance)
            records.append(
                MemoryRecord(
                    id=record_id,
                    text=text or "",
                    kind=str(metadata.pop("kind", "memory")),
                    created_at=str(metadata.pop("created_at", "")),
                    metadata=metadata,
                )
            )
        return records


class MemoryPlugin(AetherPlugin):
    name = "memory"
    description = "Store, retrieve, edit, summarize, export, and import local memories."
    resource_cost = "medium"

    def __init__(
        self,
        tier_config: TierConfig,
        llm: ManagedLLM,
        store_path: Path,
        *,
        summarize_interval_sec: Optional[int] = None,
        on_summary_due: Optional[Callable[[int], None]] = None,
    ) -> None:
        self.tier_config = tier_config
        self.llm = llm
        self.store_path = store_path
        self.max_items = tier_config.vector_memory_max_items
        self.summarize_interval_sec = summarize_interval_sec or max(
            3600, tier_config.background_poll_interval_sec * 20
        )
        self.on_summary_due = on_summary_due or (lambda count: None)
        self._store: Optional[ChromaMemoryStore] = None
        self._stop = threading.Event()
        self._summary_thread: Optional[threading.Thread] = None

    @property
    def store(self) -> ChromaMemoryStore:
        if self._store is None:
            self._store = ChromaMemoryStore(self.store_path)
        return self._store

    def on_enable(self) -> None:
        try:
            self.store
        except RuntimeError:
            return
        self._stop.clear()
        self._summary_thread = threading.Thread(
            target=self._summary_loop, name="AetherMemorySummary", daemon=True
        )
        self._summary_thread.start()

    def on_disable(self) -> None:
        self._stop.set()
        if self._summary_thread:
            self._summary_thread.join(timeout=2)

    def _trim_to_cap(self, incoming: int = 1) -> None:
        overflow = self.store.count() + incoming - self.max_items
        if overflow <= 0:
            return
        oldest = sorted(self.store.all(), key=lambda record: record.created_at)[:overflow]
        self.store.delete([record.id for record in oldest])

    def add_record(self, text: str, kind: str = "memory", metadata: Optional[dict] = None) -> MemoryRecord:
        self._trim_to_cap()
        record = MemoryRecord(
            id=str(uuid.uuid4()),
            text=text,
            kind=kind,
            created_at=datetime.now(timezone.utc).isoformat(),
            metadata=metadata or {},
        )
        self.store.add(record)
        return record

    def _summary_loop(self) -> None:
        while not self._stop.wait(self.summarize_interval_sec):
            try:
                recent = [record for record in self.store.all(limit=60) if record.kind != "summary"]
                if len(recent) < 20:
                    continue
                # A periodic timer may suggest maintenance, but it never calls a
                # cloud model or modifies memory without the central approval UI.
                self.on_summary_due(len(recent))
            except (RuntimeError, ValueError):
                continue

    def _summarize(self, args: dict) -> ToolResult:
        recent = [record for record in self.store.all(limit=60) if record.kind != "summary"]
        if not recent:
            return ToolResult(ok=True, output="There are no memories to summarize.")
        response = self.llm.chat(
            messages=[{"role": "user", "content": "\n".join(f"- {item.text}" for item in recent)}],
            tools=[],
            system=(
                "Create a compact factual memory summary. Preserve stable preferences and "
                "decisions; omit transient chatter and do not invent details."
            ),
        )
        if not response.text.strip():
            return ToolResult(ok=False, output="The model returned an empty summary.")
        record = self.add_record(
            response.text.strip(),
            kind="summary",
            metadata={"source_count": len(recent), "automatic": False},
        )
        return ToolResult(ok=True, output=f"Stored summary {record.id}.", data=asdict(record))

    def _add(self, args: dict) -> ToolResult:
        record = self.add_record(
            str(args["text"]), str(args.get("kind", "memory")), dict(args.get("metadata", {}))
        )
        return ToolResult(ok=True, output=f"Stored memory {record.id}.", data=asdict(record))

    def _search(self, args: dict) -> ToolResult:
        matches = self.store.search(str(args["query"]), min(int(args.get("limit", 8)), 50))
        return ToolResult(
            ok=True,
            output="\n\n".join(f"[{record.id}] {record.text}" for record in matches) or "No matching memories.",
            data={"records": [asdict(record) for record in matches]},
        )

    def _list(self, args: dict) -> ToolResult:
        records = self.store.all(limit=min(int(args.get("limit", 100)), 500))
        return ToolResult(
            ok=True,
            output=f"Loaded {len(records)} memory records.",
            data={"records": [asdict(record) for record in records], "count": self.store.count()},
        )

    def _edit(self, args: dict) -> ToolResult:
        self.store.update(str(args["id"]), str(args["text"]))
        return ToolResult(ok=True, output=f"Updated memory {args['id']}.")

    def _delete(self, args: dict) -> ToolResult:
        self.store.delete([str(args["id"])])
        return ToolResult(ok=True, output=f"Deleted memory {args['id']}.")

    def _export_json(self, args: dict) -> ToolResult:
        destination = Path(args["path"]).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        records = [asdict(record) for record in self.store.all()]
        payload = {
            "format": "aether-memory-v1",
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "records": records,
        }
        destination.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return ToolResult(ok=True, output=f"Exported {len(records)} records to {destination}.")

    def _import_json(self, args: dict) -> ToolResult:
        source = Path(args["path"]).expanduser().resolve()
        payload = json.loads(source.read_text(encoding="utf-8"))
        if payload.get("format") != "aether-memory-v1" or not isinstance(payload.get("records"), list):
            return ToolResult(ok=False, output="Not a supported Aether memory export.")
        imported = 0
        for item in payload["records"]:
            if not isinstance(item, dict) or not str(item.get("text", "")).strip():
                continue
            self.add_record(
                str(item["text"]),
                kind=str(item.get("kind", "memory")),
                metadata={**dict(item.get("metadata", {})), "imported_from": str(source)},
            )
            imported += 1
        return ToolResult(ok=True, output=f"Imported {imported} memory records from {source}.")

    def get_tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="remember",
                description="Store a durable local memory for future conversations.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "kind": {"type": "string"},
                        "metadata": {"type": "object"},
                    },
                    "required": ["text"],
                },
                handler=self._add,
                requires_confirmation=True,
            ),
            ToolSpec(
                name="search_memory",
                description="Semantically search the user's local Aether memory.",
                input_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
                    "required": ["query"],
                },
                handler=self._search,
                requires_confirmation=False,
            ),
            ToolSpec(
                name="list_memories",
                description="List recent local memory records for the memory viewer.",
                input_schema={"type": "object", "properties": {"limit": {"type": "integer"}}},
                handler=self._list,
                requires_confirmation=False,
            ),
            ToolSpec(
                name="summarize_memories",
                description="Use the configured model to create and store a compact memory summary.",
                input_schema={"type": "object", "properties": {}},
                handler=self._summarize,
                requires_confirmation=True,
            ),
            ToolSpec(
                name="edit_memory",
                description="Replace the text of a local memory record.",
                input_schema={
                    "type": "object",
                    "properties": {"id": {"type": "string"}, "text": {"type": "string"}},
                    "required": ["id", "text"],
                },
                handler=self._edit,
                requires_confirmation=True,
            ),
            ToolSpec(
                name="delete_memory",
                description="Delete a local memory record.",
                input_schema={
                    "type": "object",
                    "properties": {"id": {"type": "string"}},
                    "required": ["id"],
                },
                handler=self._delete,
                requires_confirmation=True,
            ),
            ToolSpec(
                name="export_memories_json",
                description="Export all memories/conversations to a portable JSON backup.",
                input_schema={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
                handler=self._export_json,
                requires_confirmation=True,
            ),
            ToolSpec(
                name="import_memories_json",
                description="Import memories from an Aether JSON backup without deleting existing history.",
                input_schema={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
                handler=self._import_json,
                requires_confirmation=True,
            ),
        ]
