"""TimescaleDB-inspired datastore implementation for archiving JetStream traffic."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import sqlite3
from typing import Any, Dict, List, Optional


def _utc_timestamp(value: float | int | str | datetime) -> float:
    """Return a UNIX timestamp in seconds for the given value."""

    if isinstance(value, datetime):
        return value.timestamp()
    if isinstance(value, (float, int)):
        return float(value)
    # Assume ISO formatted string.
    return datetime.fromisoformat(value).timestamp()


@dataclass(frozen=True)
class MessageRecord:
    """Structured representation of a stored JetStream message."""

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


@dataclass(frozen=True)
class TagRecord:
    """Structured representation of a persisted tag."""

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
    """Simple SQLite-backed datastore emulating TimescaleDB semantics."""

    def __init__(self, *, path: str | None = None) -> None:
        self._conn = sqlite3.connect(path or ":memory:")
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._initialise_schema()

    def close(self) -> None:
        self._conn.close()

    def _initialise_schema(self) -> None:
        cursor = self._conn.cursor()
        cursor.executescript(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subject TEXT NOT NULL,
                kind TEXT NOT NULL,
                nats_msg_id TEXT,
                published_ts REAL NOT NULL,
                recv_epoch_ms INTEGER,
                recv_iso TEXT,
                message_type TEXT,
                sensor_id INTEGER,
                day INTEGER,
                time_s REAL,
                payload_json TEXT NOT NULL,
                headers_json TEXT NOT NULL,
                tspi_extracts_json TEXT,
                cbor BLOB NOT NULL,
                created_at REAL NOT NULL DEFAULT (strftime('%s', 'now')),
                UNIQUE(nats_msg_id)
            );

            CREATE TABLE IF NOT EXISTS commands (
                cmd_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                ts TEXT NOT NULL,
                sender TEXT,
                units TEXT,
                payload_json TEXT NOT NULL,
                published_ts REAL NOT NULL,
                message_id INTEGER NOT NULL,
                created_at REAL NOT NULL DEFAULT (strftime('%s', 'now')),
                FOREIGN KEY(message_id) REFERENCES messages(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS tags (
                id TEXT PRIMARY KEY,
                ts TEXT NOT NULL,
                creator TEXT,
                label TEXT,
                category TEXT,
                notes TEXT,
                extra_json TEXT NOT NULL,
                status TEXT NOT NULL,
                updated_ts TEXT NOT NULL,
                message_id INTEGER,
                FOREIGN KEY(message_id) REFERENCES messages(id) ON DELETE SET NULL
            );
            """
        )
        cursor.close()
        self._conn.commit()

    # ------------------------------------------------------------------
    # Message persistence
    # ------------------------------------------------------------------
    def insert_message(
        self,
        *,
        subject: str,
        kind: str,
        payload: Dict[str, Any],
        headers: Dict[str, str],
        published_ts: float,
        raw_cbor: bytes,
    ) -> Optional[int]:
        """Insert a JetStream message and return its row id if stored."""

        message_id = headers.get("Nats-Msg-Id")
        extracts = payload.get("payload") if isinstance(payload, dict) else None

        recv_epoch_ms = payload.get("recv_epoch_ms") if isinstance(payload, dict) else None
        recv_iso = payload.get("recv_iso") if isinstance(payload, dict) else None
        message_type = payload.get("type") if isinstance(payload, dict) else None
        sensor_id = payload.get("sensor_id") if isinstance(payload, dict) else None
        day = payload.get("day") if isinstance(payload, dict) else None
        time_s = payload.get("time_s") if isinstance(payload, dict) else None

        try:
            cursor = self._conn.execute(
                """
                INSERT INTO messages (
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
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    subject,
                    kind,
                    message_id,
                    float(published_ts),
                    recv_epoch_ms,
                    recv_iso,
                    message_type,
                    sensor_id,
                    day,
                    time_s,
                    json.dumps(payload, separators=(",", ":")),
                    json.dumps(headers, separators=(",", ":")),
                    json.dumps(extracts, separators=(",", ":")) if extracts else None,
                    sqlite3.Binary(raw_cbor),
                ),
            )
        except sqlite3.IntegrityError:
            return None

        self._conn.commit()
        return int(cursor.lastrowid)

    def fetch_messages_between(self, start_ts: float, end_ts: float) -> List[MessageRecord]:
        cursor = self._conn.execute(
            """
            SELECT * FROM messages
            WHERE published_ts BETWEEN ? AND ?
            ORDER BY published_ts ASC, id ASC
            """,
            (float(start_ts), float(end_ts)),
        )
        rows = cursor.fetchall()
        cursor.close()
        return [self._message_from_row(row) for row in rows]

    def fetch_messages_for_tag(
        self,
        tag_id: str,
        *,
        window_seconds: float = 10.0,
    ) -> List[MessageRecord]:
        tag = self.get_tag(tag_id)
        if tag is None:
            return []

        centre = _utc_timestamp(tag.ts)
        half_window = window_seconds / 2.0
        return self.fetch_messages_between(centre - half_window, centre + half_window)

    def _message_from_row(self, row: sqlite3.Row) -> MessageRecord:
        return MessageRecord(
            id=row["id"],
            subject=row["subject"],
            kind=row["kind"],
            published_ts=row["published_ts"],
            headers=json.loads(row["headers_json"]),
            payload=json.loads(row["payload_json"]),
            cbor=bytes(row["cbor"]),
            recv_epoch_ms=row["recv_epoch_ms"],
            recv_iso=row["recv_iso"],
            message_type=row["message_type"],
            sensor_id=row["sensor_id"],
            day=row["day"],
            time_s=row["time_s"],
        )

    # ------------------------------------------------------------------
    # Command persistence
    # ------------------------------------------------------------------
    def upsert_command(self, payload: Dict[str, Any], *, message_id: int, published_ts: float) -> None:
        cmd_id = payload.get("cmd_id")
        if not cmd_id:
            return

        units = None
        body = payload.get("payload")
        if isinstance(body, dict):
            units = body.get("units")

        self._conn.execute(
            """
            INSERT INTO commands (
                cmd_id,
                name,
                ts,
                sender,
                units,
                payload_json,
                published_ts,
                message_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(cmd_id) DO UPDATE SET
                name=excluded.name,
                ts=excluded.ts,
                sender=excluded.sender,
                units=excluded.units,
                payload_json=excluded.payload_json,
                published_ts=excluded.published_ts,
                message_id=excluded.message_id
            """,
            (
                cmd_id,
                payload.get("name"),
                payload.get("ts"),
                payload.get("sender"),
                units,
                json.dumps(payload, separators=(",", ":")),
                float(published_ts),
                message_id,
            ),
        )
        self._conn.commit()

    def latest_command(self, name: str) -> Optional[Dict[str, Any]]:
        cursor = self._conn.execute(
            """
            SELECT payload_json FROM commands
            WHERE name = ?
            ORDER BY published_ts DESC
            LIMIT 1
            """,
            (name,),
        )
        row = cursor.fetchone()
        cursor.close()
        if row is None:
            return None
        return json.loads(row["payload_json"])

    # ------------------------------------------------------------------
    # Tag persistence
    # ------------------------------------------------------------------
    def apply_tag_event(
        self,
        subject: str,
        payload: Dict[str, Any],
        *,
        message_id: int,
    ) -> None:
        tag_id = payload.get("id")
        if not tag_id:
            return

        existing = self.get_tag(tag_id)
        now_iso = datetime.now(timezone.utc).isoformat()
        updated_ts = payload.get("ts") or (existing.updated_ts if existing else now_iso)
        base_ts = payload.get("ts") or (existing.ts if existing else now_iso)

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

        self._conn.execute(
            """
            INSERT INTO tags (id, ts, creator, label, category, notes, extra_json, status, updated_ts, message_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                ts=excluded.ts,
                creator=excluded.creator,
                label=excluded.label,
                category=excluded.category,
                notes=excluded.notes,
                extra_json=excluded.extra_json,
                status=excluded.status,
                updated_ts=excluded.updated_ts,
                message_id=excluded.message_id
            """,
            (
                tag_id,
                base_ts,
                creator,
                label,
                category,
                notes,
                json.dumps(extra, separators=(",", ":")),
                status,
                updated_ts or base_ts,
                message_id,
            ),
        )
        self._conn.commit()

    def get_tag(self, tag_id: str) -> Optional[TagRecord]:
        cursor = self._conn.execute("SELECT * FROM tags WHERE id = ?", (tag_id,))
        row = cursor.fetchone()
        cursor.close()
        if row is None:
            return None
        return TagRecord(
            id=row["id"],
            ts=row["ts"],
            creator=row["creator"],
            label=row["label"],
            category=row["category"],
            notes=row["notes"],
            extra=json.loads(row["extra_json"]),
            status=row["status"],
            updated_ts=row["updated_ts"],
        )

    def list_tags(self, *, include_deleted: bool = False) -> List[TagRecord]:
        if include_deleted:
            cursor = self._conn.execute("SELECT * FROM tags ORDER BY updated_ts DESC")
        else:
            cursor = self._conn.execute(
                "SELECT * FROM tags WHERE status != 'deleted' ORDER BY updated_ts DESC"
            )
        rows = cursor.fetchall()
        cursor.close()
        return [
            TagRecord(
                id=row["id"],
                ts=row["ts"],
                creator=row["creator"],
                label=row["label"],
                category=row["category"],
                notes=row["notes"],
                extra=json.loads(row["extra_json"]),
                status=row["status"],
                updated_ts=row["updated_ts"],
            )
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------
    def count_messages(self) -> int:
        cursor = self._conn.execute("SELECT COUNT(*) FROM messages")
        (count,) = cursor.fetchone()
        cursor.close()
        return int(count)

    def count_commands(self) -> int:
        cursor = self._conn.execute("SELECT COUNT(*) FROM commands")
        (count,) = cursor.fetchone()
        cursor.close()
        return int(count)

    def count_tags(self) -> int:
        cursor = self._conn.execute("SELECT COUNT(*) FROM tags")
        (count,) = cursor.fetchone()
        cursor.close()
        return int(count)

