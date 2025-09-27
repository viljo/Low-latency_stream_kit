"""TimescaleDB persistence layer for the JetStream archiver."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Dict, Optional, Sequence

try:  # pragma: no cover - optional dependency resolution
    import asyncpg
except ModuleNotFoundError:  # pragma: no cover - deferred error until connect()
    asyncpg = None  # type: ignore[assignment]


def _to_datetime(value: float | int | str | datetime) -> datetime:
    """Normalise timestamps into timezone-aware ``datetime`` objects."""

    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, (float, int)):
        return datetime.fromtimestamp(float(value), tz=UTC)
    return datetime.fromisoformat(str(value)).astimezone(UTC)


def _to_timestamp(value: datetime | float | int | str | None) -> Optional[float]:
    if value is None:
        return None
    return _to_datetime(value).timestamp()


@dataclass(slots=True, frozen=True)
class MessageRecord:
    """Structured representation of a TimescaleDB message row."""

    id: int
    subject: str
    kind: str
    published_ts: float
    headers: Dict[str, Any]
    payload: Dict[str, Any]
    cbor: bytes
    recv_epoch_ms: Optional[int]
    recv_iso: Optional[str]
    message_type: Optional[str]
    sensor_id: Optional[int]
    day: Optional[int]
    time_s: Optional[float]


@dataclass(slots=True, frozen=True)
class TagRecord:
    """Structured representation of a TimescaleDB tag row."""

    id: str
    ts: str
    creator: Optional[str]
    label: Optional[str]
    category: Optional[str]
    notes: Optional[str]
    extra: Dict[str, Any]
    status: str
    updated_ts: str


class TimescaleDatastore:
    """Async TimescaleDB client used by the archiver and replayer."""

    def __init__(
        self,
        dsn: str,
        *,
        schema: str = "public",
        min_size: int = 1,
        max_size: int = 10,
    ) -> None:
        self._dsn = dsn
        self._schema = schema
        self._pool: asyncpg.Pool | None = None
        self._pool_settings = {"min_size": min_size, "max_size": max_size}

    async def connect(self) -> None:
        """Initialise the connection pool and ensure the schema exists."""

        if self._pool is not None:
            return
        if asyncpg is None:  # pragma: no cover - handled at runtime
            raise ModuleNotFoundError(
                "asyncpg is required to use TimescaleDatastore"
            ) from None
        self._pool = await asyncpg.create_pool(self._dsn, **self._pool_settings)
        async with self._pool.acquire() as conn:
            await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {self._schema}")
            await conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self._schema}.messages (
                    id BIGSERIAL PRIMARY KEY,
                    subject TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    nats_msg_id TEXT UNIQUE,
                    published_ts TIMESTAMPTZ NOT NULL,
                    recv_epoch_ms BIGINT,
                    recv_iso TIMESTAMPTZ,
                    message_type TEXT,
                    sensor_id INTEGER,
                    day INTEGER,
                    time_s DOUBLE PRECISION,
                    payload_json JSONB NOT NULL,
                    headers_json JSONB NOT NULL,
                    tspi_extracts_json JSONB,
                    cbor BYTEA NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS {self._schema}.commands (
                    cmd_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    ts TIMESTAMPTZ NOT NULL,
                    sender TEXT,
                    units TEXT,
                    payload_json JSONB NOT NULL,
                    published_ts TIMESTAMPTZ NOT NULL,
                    message_id BIGINT NOT NULL REFERENCES {self._schema}.messages(id) ON DELETE CASCADE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS {self._schema}.tags (
                    id TEXT PRIMARY KEY,
                    ts TIMESTAMPTZ NOT NULL,
                    creator TEXT,
                    label TEXT,
                    category TEXT,
                    notes TEXT,
                    extra_json JSONB NOT NULL,
                    status TEXT NOT NULL,
                    updated_ts TIMESTAMPTZ NOT NULL,
                    message_id BIGINT REFERENCES {self._schema}.messages(id) ON DELETE SET NULL
                );
                """
            )

    async def close(self) -> None:
        if self._pool is None:
            return
        await self._pool.close()
        self._pool = None

    # ------------------------------------------------------------------
    # Message persistence
    # ------------------------------------------------------------------
    async def insert_message(
        self,
        *,
        subject: str,
        kind: str,
        payload: Dict[str, Any],
        headers: Dict[str, Any],
        published_ts: datetime | float | int | str,
        raw_cbor: bytes,
    ) -> Optional[int]:
        """Insert a JetStream message into TimescaleDB if not already stored."""

        pool = self._require_pool()
        message_id = headers.get("Nats-Msg-Id")
        extracts = payload.get("payload") if isinstance(payload, dict) else None
        recv_iso = payload.get("recv_iso") if isinstance(payload, dict) else None

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                INSERT INTO {self._schema}.messages (
                    subject,
                    kind,
                    nats_msg_id,
                    published_ts,
                    recv_epoch_ms,
                    recv_iso,
                    message_type,
                    sensor_id,
                    day,
                    time_s,
                    payload_json,
                    headers_json,
                    tspi_extracts_json,
                    cbor
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
                ON CONFLICT (nats_msg_id) DO NOTHING
                RETURNING id
                """,
                subject,
                kind,
                message_id,
                _to_datetime(published_ts),
                payload.get("recv_epoch_ms") if isinstance(payload, dict) else None,
                _to_datetime(recv_iso) if recv_iso else None,
                payload.get("type") if isinstance(payload, dict) else None,
                payload.get("sensor_id") if isinstance(payload, dict) else None,
                payload.get("day") if isinstance(payload, dict) else None,
                payload.get("time_s") if isinstance(payload, dict) else None,
                json.dumps(payload, separators=(",", ":")),
                json.dumps(dict(headers), separators=(",", ":")),
                json.dumps(extracts, separators=(",", ":")) if extracts else None,
                raw_cbor,
            )
        if row is None:
            return None
        return int(row["id"])

    async def fetch_messages_between(
        self, start_ts: float, end_ts: float
    ) -> Sequence[MessageRecord]:
        pool = self._require_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT * FROM {self._schema}.messages
                WHERE published_ts BETWEEN $1 AND $2
                ORDER BY published_ts ASC, id ASC
                """,
                _to_datetime(start_ts),
                _to_datetime(end_ts),
            )
        return [self._message_from_row(row) for row in rows]

    async def fetch_messages_for_tag(
        self, tag_id: str, *, window_seconds: float = 10.0
    ) -> Sequence[MessageRecord]:
        tag = await self.get_tag(tag_id)
        if tag is None:
            return []
        centre = _to_datetime(tag.ts).timestamp()
        half_window = window_seconds / 2.0
        return await self.fetch_messages_between(centre - half_window, centre + half_window)

    def _message_from_row(self, row: Any) -> MessageRecord:
        return MessageRecord(
            id=int(row["id"]),
            subject=row["subject"],
            kind=row["kind"],
            published_ts=_to_timestamp(row["published_ts"]) or 0.0,
            headers=row["headers_json"],
            payload=row["payload_json"],
            cbor=bytes(row["cbor"]),
            recv_epoch_ms=row["recv_epoch_ms"],
            recv_iso=(
                row["recv_iso"].astimezone(UTC).isoformat() if row["recv_iso"] is not None else None
            ),
            message_type=row["message_type"],
            sensor_id=row["sensor_id"],
            day=row["day"],
            time_s=row["time_s"],
        )

    # ------------------------------------------------------------------
    # Command persistence
    # ------------------------------------------------------------------
    async def upsert_command(
        self, payload: Dict[str, Any], *, message_id: int, published_ts: datetime | float | int | str
    ) -> None:
        cmd_id = payload.get("cmd_id")
        if not cmd_id:
            return

        pool = self._require_pool()
        units = None
        body = payload.get("payload")
        if isinstance(body, dict):
            units = body.get("units")

        async with pool.acquire() as conn:
            await conn.execute(
                f"""
                INSERT INTO {self._schema}.commands (
                    cmd_id,
                    name,
                    ts,
                    sender,
                    units,
                    payload_json,
                    published_ts,
                    message_id
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (cmd_id) DO UPDATE SET
                    name = EXCLUDED.name,
                    ts = EXCLUDED.ts,
                    sender = EXCLUDED.sender,
                    units = EXCLUDED.units,
                    payload_json = EXCLUDED.payload_json,
                    published_ts = EXCLUDED.published_ts,
                    message_id = EXCLUDED.message_id
                """,
                cmd_id,
                payload.get("name"),
                _to_datetime(payload.get("ts") or datetime.now(tz=UTC)),
                payload.get("sender"),
                units,
                json.dumps(payload, separators=(",", ":")),
                _to_datetime(published_ts),
                message_id,
            )

    async def latest_command(self, name: str) -> Optional[Dict[str, Any]]:
        pool = self._require_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT payload_json FROM {self._schema}.commands
                WHERE name = $1
                ORDER BY published_ts DESC
                LIMIT 1
                """,
                name,
            )
        if row is None:
            return None
        return row["payload_json"]

    # ------------------------------------------------------------------
    # Tag persistence
    # ------------------------------------------------------------------
    async def apply_tag_event(
        self,
        subject: str,
        payload: Dict[str, Any],
        *,
        message_id: int,
    ) -> None:
        tag_id = payload.get("id")
        if not tag_id:
            return

        existing = await self.get_tag(tag_id)
        now = datetime.now(tz=UTC)
        updated_ts = payload.get("ts") or (existing.updated_ts if existing else now.isoformat())
        base_ts = payload.get("ts") or (existing.ts if existing else now.isoformat())

        creator = payload.get("creator") or (existing.creator if existing else None)
        label = payload.get("label") or (existing.label if existing else None)
        category = payload.get("category") or (existing.category if existing else None)
        notes = payload.get("notes") or (existing.notes if existing else None)
        extra = payload.get("extra") or (existing.extra if existing else {})

        status = "active"
        if subject.endswith("delete"):
            status = "deleted"
        elif subject.endswith("broadcast"):
            status = payload.get("status", existing.status if existing else "active") or "active"

        pool = self._require_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                f"""
                INSERT INTO {self._schema}.tags (
                    id,
                    ts,
                    creator,
                    label,
                    category,
                    notes,
                    extra_json,
                    status,
                    updated_ts,
                    message_id
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                ON CONFLICT (id) DO UPDATE SET
                    ts = EXCLUDED.ts,
                    creator = EXCLUDED.creator,
                    label = EXCLUDED.label,
                    category = EXCLUDED.category,
                    notes = EXCLUDED.notes,
                    extra_json = EXCLUDED.extra_json,
                    status = EXCLUDED.status,
                    updated_ts = EXCLUDED.updated_ts,
                    message_id = EXCLUDED.message_id
                """,
                tag_id,
                _to_datetime(base_ts),
                creator,
                label,
                category,
                notes,
                json.dumps(extra, separators=(",", ":")),
                status,
                _to_datetime(updated_ts),
                message_id,
            )

    async def get_tag(self, tag_id: str) -> Optional[TagRecord]:
        pool = self._require_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT * FROM {self._schema}.tags WHERE id = $1",
                tag_id,
            )
        if row is None:
            return None
        return TagRecord(
            id=row["id"],
            ts=row["ts"].astimezone(UTC).isoformat(),
            creator=row["creator"],
            label=row["label"],
            category=row["category"],
            notes=row["notes"],
            extra=row["extra_json"],
            status=row["status"],
            updated_ts=row["updated_ts"].astimezone(UTC).isoformat(),
        )

    async def list_tags(self, *, include_deleted: bool = False) -> Sequence[TagRecord]:
        pool = self._require_pool()
        condition = "" if include_deleted else "WHERE status != 'deleted'"
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT * FROM {self._schema}.tags {condition} ORDER BY updated_ts DESC"
            )
        return [
            TagRecord(
                id=row["id"],
                ts=row["ts"].astimezone(UTC).isoformat(),
                creator=row["creator"],
                label=row["label"],
                category=row["category"],
                notes=row["notes"],
                extra=row["extra_json"],
                status=row["status"],
                updated_ts=row["updated_ts"].astimezone(UTC).isoformat(),
            )
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------
    async def count_messages(self) -> int:
        pool = self._require_pool()
        async with pool.acquire() as conn:
            value = await conn.fetchval(
                f"SELECT COUNT(*) FROM {self._schema}.messages"
            )
        return int(value)

    async def count_commands(self) -> int:
        pool = self._require_pool()
        async with pool.acquire() as conn:
            value = await conn.fetchval(
                f"SELECT COUNT(*) FROM {self._schema}.commands"
            )
        return int(value)

    async def count_tags(self) -> int:
        pool = self._require_pool()
        async with pool.acquire() as conn:
            value = await conn.fetchval(
                f"SELECT COUNT(*) FROM {self._schema}.tags"
            )
        return int(value)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _require_pool(self) -> Any:
        if self._pool is None:
            raise RuntimeError("TimescaleDatastore.connect() must be awaited before use")
        return self._pool


__all__ = [
    "TimescaleDatastore",
    "MessageRecord",
    "TagRecord",
]

