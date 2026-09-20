"""Climate GeoGuessr API.

The one rule this file exists to enforce: the client is never told where the
round is until it has committed a guess. The answer lives in SQLite, the round
is addressed by an opaque id, and the chart URLs are content hashes. Anything
that would let a player read the answer out of the Network tab is a bug.
"""
from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import config, db
from app.scoring import attempt_multiplier, bearing_to, haversine_km, score_round
from epw_ingest.catalog import CHARTS, CHARTS_BY_ID, TIER_CORE
from epw_ingest.koppen import DESCRIPTIONS

_conn = None


def get_conn():
    if _conn is None:  # pragma: no cover - lifespan always sets this
        raise HTTPException(503, "database not ready")
    return _conn


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _conn
    _conn = db.connect()
    db.init(_conn)
    loaded = db.load_manifest(_conn)
    count = db.location_count(_conn)
    print(f"[climate] {loaded} manifest entries synced, {count} locations playable")
    if count == 0:
        print("[climate] No locations. Drop EPWs in epw_ingest/raw/ and run "
              "`python -m epw_ingest.build_assets`.")
    yield
    _conn.close()


app = FastAPI(title="Climate GeoGuessr", lifespan=lifespan)


# --- Request/response models ------------------------------------------------
class NewRound(BaseModel):
    session_id: str = Field(min_length=8, max_length=64)


class HintRequest(BaseModel):
    session_id: str = Field(min_length=8, max_length=64)
    round_id: str
    hint: str  # "summary" or a hint-tier chart id


VALID_HINTS = {c.id for c in CHARTS if c.tier != TIER_CORE} | {"summary"}


class Guess(BaseModel):
    session_id: str = Field(min_length=8, max_length=64)
    round_id: str
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    koppen: Optional[str] = None


# --- Routes -----------------------------------------------------------------
@app.get("/api/meta")
def meta(conn=Depends(get_conn)):
    """Static game metadata: chart catalogue and the Köppen picker options."""
    return {
        "locations": db.location_count(conn),
        "max_score": config.MAX_SCORE,
        "max_attempts": config.MAX_ATTEMPTS,
        "attempt_penalty": config.ATTEMPT_PENALTY,
        "accept_radius_km": config.ACCEPT_RADIUS_KM,
        "charts": [
            {"id": c.id, "label": c.label, "tier": c.tier, "blurb": c.blurb}
            for c in CHARTS
        ],
        "koppen_options": [
            {"code": code, "label": label} for code, label in sorted(DESCRIPTIONS.items())
        ],
    }


@app.post("/api/round")
def new_round(payload: NewRound, conn=Depends(get_conn)):
    location = db.pick_location(conn, payload.session_id)
    if location is None:
        raise HTTPException(503, "No locations built yet — run the asset build.")

    round_id = uuid.uuid4().hex
    db.create_round(conn, round_id, payload.session_id, location["id"])

    available = set(json.loads(location["charts"]))
    # Only core charts ship with the round. Hint-tier charts (the sun path)
    # are fetched separately and cost score, so their URLs must not appear here.
    charts = [
        {
            "id": c.id,
            "label": c.label,
            "blurb": c.blurb,
            "url": f"/charts/{location['id']}/{c.id}.json",
        }
        for c in CHARTS
        if c.tier == TIER_CORE and c.id in available
    ]
    return {
        "round_id": round_id,
        "charts": charts,
        # Descriptions only — no URLs. Contents come from POST /api/hint.
        "hints": [
            {"id": c.id, "label": c.label, "blurb": c.blurb, "cost": config.HINT_PENALTY}
            for c in CHARTS
            if c.tier != TIER_CORE and c.id in available
        ]
        + [{
            "id": "summary",
            "label": "Monthly data table",
            "blurb": "The same information as the charts, as numbers.",
            "cost": config.HINT_PENALTY,
        }],
    }


@app.post("/api/hint")
def reveal_hint(payload: HintRequest, conn=Depends(get_conn)):
    """Serve a hint's contents inline, after charging for it.

    The payload is returned in the response body rather than as a URL into the
    static mount. A URL would be forgeable: the opaque location id is already
    visible on the core chart URLs, so `/charts/<id>/sunpath.json` would hand
    over the most revealing chart in the game for free.
    """
    rnd = db.get_open_round(conn, payload.round_id, payload.session_id)
    if rnd is None:
        raise HTTPException(404, "round not found, already played, or expired")
    if payload.hint not in VALID_HINTS:
        raise HTTPException(400, "unknown hint")

    path = config.HINTS_DIR / rnd["location_id"] / f"{payload.hint}.json"
    if not path.is_file():
        raise HTTPException(404, "hint not built for this location")

    hints_used, _already = db.reveal_hint(conn, payload.round_id, payload.hint)
    spec = CHARTS_BY_ID.get(payload.hint)
    return {
        "hint": payload.hint,
        "kind": "summary" if payload.hint == "summary" else "figure",
        "label": spec.label if spec else "Monthly data table",
        "payload": json.loads(path.read_text(encoding="utf-8")),
        "hints_used": hints_used,
        "multiplier": max(
            config.MIN_HINT_MULTIPLIER, 1.0 - config.HINT_PENALTY * hints_used
        ),
    }


@app.post("/api/guess")
def submit_guess(payload: Guess, conn=Depends(get_conn)):
    rnd = db.get_open_round(conn, payload.round_id, payload.session_id)
    if rnd is None:
        raise HTTPException(404, "round not found, already played, or expired")

    location = db.get_location(conn, rnd["location_id"])
    attempts_used = db.record_attempt(conn, payload.round_id)
    distance = haversine_km(payload.lat, payload.lon, location["lat"], location["lon"])

    # A miss with attempts left does not end the round and does not reveal the
    # answer. The compass direction is the only thing that crosses the wire —
    # no distance, or three guesses would trilaterate the location outright.
    if distance > config.ACCEPT_RADIUS_KM and attempts_used < config.MAX_ATTEMPTS:
        return {
            "result": "retry",
            "accepted": False,
            "direction": bearing_to(
                payload.lat, payload.lon, location["lat"], location["lon"]
            ),
            "attempts_used": attempts_used,
            "attempts_left": config.MAX_ATTEMPTS - attempts_used,
            "attempt_multiplier": attempt_multiplier(attempts_used + 1),
        }

    breakdown = score_round(
        payload.lat, payload.lon,
        location["lat"], location["lon"],
        payload.koppen, location["koppen"],
        hints_used=rnd["hints_used"],
        attempts_used=attempts_used,
    )
    db.finish_round(
        conn, payload.round_id, breakdown.score, breakdown.distance_km,
        payload.lat, payload.lon, payload.koppen,
    )

    # Only now does the answer cross the wire.
    return {
        "result": "final",
        "accepted": distance <= config.ACCEPT_RADIUS_KM,
        "attempts_left": config.MAX_ATTEMPTS - attempts_used,
        **breakdown.__dict__,
        "answer": {
            "city": location["city"],
            "state": location["state"],
            "country": location["country"],
            "lat": location["lat"],
            "lon": location["lon"],
            "elevation_m": location["elevation_m"],
            "koppen": location["koppen"],
            "koppen_description": location["koppen_description"],
        },
        "stats": db.session_stats(conn, payload.session_id),
    }


@app.get("/api/history/{session_id}")
def get_history(session_id: str, conn=Depends(get_conn)):
    return {
        "stats": db.session_stats(conn, session_id),
        "rounds": db.history(conn, session_id),
    }


# --- Static ------------------------------------------------------------------
# Mount the charts directory only. assets/manifest.json is the answer key and
# lives one level above it, so there is no URL that reaches it.
config.CHARTS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/charts", StaticFiles(directory=config.CHARTS_DIR), name="charts")


@app.get("/")
def index():
    return FileResponse(config.WEB_DIR / "index.html")


app.mount("/", StaticFiles(directory=config.WEB_DIR, html=True), name="web")
