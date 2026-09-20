"""SQLite storage: the answer key, plus in-flight and finished rounds.

SQLite rather than Redis for in-flight rounds so a restart doesn't void
everyone's open round, and so there is one fewer thing to run locally. Swap
`rounds` for Redis with a TTL if this ever needs more than one worker process.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from app import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS locations (
    id                  TEXT PRIMARY KEY,
    source_file         TEXT NOT NULL,
    city                TEXT,
    state               TEXT,
    country             TEXT,
    lat                 REAL NOT NULL,
    lon                 REAL NOT NULL,
    elevation_m         REAL,
    koppen              TEXT,
    koppen_description  TEXT,
    difficulty          INTEGER NOT NULL DEFAULT 3,
    charts              TEXT NOT NULL,
    has_png             INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS rounds (
    round_id     TEXT PRIMARY KEY,
    session_id   TEXT NOT NULL,
    location_id  TEXT NOT NULL REFERENCES locations(id),
    started_at   REAL NOT NULL,
    hints_used   INTEGER NOT NULL DEFAULT 0,
    hints_shown  TEXT NOT NULL DEFAULT '[]',
    attempts     INTEGER NOT NULL DEFAULT 0,
    finished_at  REAL,
    score        INTEGER,
    distance_km  REAL,
    guess_lat    REAL,
    guess_lon    REAL,
    guess_koppen TEXT
);

CREATE INDEX IF NOT EXISTS rounds_by_session ON rounds(session_id, started_at DESC);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns that CREATE TABLE IF NOT EXISTS will not add to an existing
    table. Dropping game.db would also work — it only holds scores — but not at
    the cost of wiping someone's run just because they pulled a new version."""
    have = {row["name"] for row in conn.execute("PRAGMA table_info(rounds)")}
    if "attempts" not in have:
        conn.execute("ALTER TABLE rounds ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")


def load_manifest(conn: sqlite3.Connection, manifest_path: Optional[Path] = None) -> int:
    """Sync the answer key from the build output. Idempotent."""
    path = manifest_path or config.MANIFEST
    if not path.exists():
        return 0
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("locations", [])
    conn.executemany(
        """
        INSERT INTO locations (id, source_file, city, state, country, lat, lon,
                               elevation_m, koppen, koppen_description,
                               difficulty, charts, has_png)
        VALUES (:id, :source_file, :city, :state, :country, :lat, :lon,
                :elevation_m, :koppen, :koppen_description,
                :difficulty, :charts, :has_png)
        ON CONFLICT(id) DO UPDATE SET
            source_file=excluded.source_file, city=excluded.city,
            state=excluded.state, country=excluded.country,
            lat=excluded.lat, lon=excluded.lon,
            elevation_m=excluded.elevation_m, koppen=excluded.koppen,
            koppen_description=excluded.koppen_description,
            difficulty=excluded.difficulty, charts=excluded.charts,
            has_png=excluded.has_png
        """,
        [
            {
                **{k: entry.get(k) for k in (
                    "id", "source_file", "city", "state", "country", "lat", "lon",
                    "elevation_m", "koppen", "koppen_description",
                )},
                "difficulty": entry.get("difficulty") or 3,
                "charts": json.dumps(entry.get("charts", [])),
                "has_png": int(bool(entry.get("has_png"))),
            }
            for entry in rows
        ],
    )
    conn.commit()
    return len(rows)


def location_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) AS n FROM locations").fetchone()["n"]


def pick_location(conn: sqlite3.Connection, session_id: str) -> Optional[sqlite3.Row]:
    """Random location, avoiding this session's recent rounds.

    Falls back to the unfiltered pool once the session has seen everything,
    which matters a lot at 20 locations.
    """
    recent = [
        row["location_id"]
        for row in conn.execute(
            "SELECT location_id FROM rounds WHERE session_id=? "
            "ORDER BY started_at DESC LIMIT ?",
            (session_id, config.RECENT_MEMORY),
        )
    ]
    if recent:
        placeholders = ",".join("?" * len(recent))
        row = conn.execute(
            f"SELECT * FROM locations WHERE id NOT IN ({placeholders}) "
            "ORDER BY RANDOM() LIMIT 1",
            recent,
        ).fetchone()
        if row:
            return row
    return conn.execute("SELECT * FROM locations ORDER BY RANDOM() LIMIT 1").fetchone()


def create_round(conn: sqlite3.Connection, round_id: str, session_id: str,
                 location_id: str) -> None:
    conn.execute(
        "INSERT INTO rounds (round_id, session_id, location_id, started_at) "
        "VALUES (?,?,?,?)",
        (round_id, session_id, location_id, time.time()),
    )
    conn.commit()


def get_open_round(conn: sqlite3.Connection, round_id: str,
                   session_id: str) -> Optional[sqlite3.Row]:
    row = conn.execute(
        "SELECT * FROM rounds WHERE round_id=? AND session_id=?",
        (round_id, session_id),
    ).fetchone()
    if row is None or row["finished_at"] is not None:
        return None
    if time.time() - row["started_at"] > config.ROUND_TTL_SECONDS:
        return None
    return row


def get_location(conn: sqlite3.Connection, location_id: str) -> sqlite3.Row:
    return conn.execute("SELECT * FROM locations WHERE id=?", (location_id,)).fetchone()


def reveal_hint(conn: sqlite3.Connection, round_id: str, hint: str) -> tuple[int, bool]:
    """Record a hint reveal. Returns (hints_used, was_already_revealed).

    Charging per *distinct* hint rather than per request means a page reload or
    a double-click doesn't quietly drain the player's score.
    """
    row = conn.execute("SELECT hints_shown, hints_used FROM rounds WHERE round_id=?",
                       (round_id,)).fetchone()
    shown = json.loads(row["hints_shown"])
    if hint in shown:
        return row["hints_used"], True
    shown.append(hint)
    conn.execute(
        "UPDATE rounds SET hints_shown=?, hints_used=? WHERE round_id=?",
        (json.dumps(shown), len(shown), round_id),
    )
    conn.commit()
    return len(shown), False


def record_attempt(conn: sqlite3.Connection, round_id: str) -> int:
    """Count one submitted guess. Returns the new attempt number (1-based)."""
    conn.execute(
        "UPDATE rounds SET attempts = attempts + 1 WHERE round_id=?", (round_id,)
    )
    conn.commit()
    return conn.execute(
        "SELECT attempts FROM rounds WHERE round_id=?", (round_id,)
    ).fetchone()["attempts"]


def finish_round(conn: sqlite3.Connection, round_id: str, score: int,
                 distance_km: float, guess_lat: float, guess_lon: float,
                 guess_koppen: Optional[str]) -> None:
    conn.execute(
        "UPDATE rounds SET finished_at=?, score=?, distance_km=?, "
        "guess_lat=?, guess_lon=?, guess_koppen=? WHERE round_id=?",
        (time.time(), score, distance_km, guess_lat, guess_lon, guess_koppen, round_id),
    )
    conn.commit()


def session_stats(conn: sqlite3.Connection, session_id: str) -> Dict[str, Any]:
    row = conn.execute(
        "SELECT COUNT(*) AS rounds, COALESCE(SUM(score),0) AS total, "
        "COALESCE(AVG(distance_km),0) AS avg_km, COALESCE(MAX(score),0) AS best "
        "FROM rounds WHERE session_id=? AND finished_at IS NOT NULL",
        (session_id,),
    ).fetchone()
    return {
        "rounds": row["rounds"],
        "total_score": row["total"],
        "average_distance_km": round(row["avg_km"], 1),
        "best_score": row["best"],
    }


def history(conn: sqlite3.Connection, session_id: str, limit: int = 10) -> List[dict]:
    rows = conn.execute(
        "SELECT r.score, r.distance_km, r.finished_at, l.city, l.country, l.koppen "
        "FROM rounds r JOIN locations l ON l.id = r.location_id "
        "WHERE r.session_id=? AND r.finished_at IS NOT NULL "
        "ORDER BY r.finished_at DESC LIMIT ?",
        (session_id, limit),
    )
    return [dict(row) for row in rows]
