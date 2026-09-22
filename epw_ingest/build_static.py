"""Emit the static-site data layer from assets/manifest.json.

GitHub Pages runs no Python, so the answer key has to reach the browser
somehow. It reaches it in two pieces:

  assets/index.json        the round pool and game constants. Carries the
                           opaque location ids and which charts each one has,
                           and nothing that identifies a place.
  assets/answers/<id>.json one location's answer. Fetched only after a guess
                           is committed.

That keeps the answer out of the page load, which is as far as a static host
can go: someone who opens devtools, reads an id off a chart URL and fetches
the answer path by hand can still cheat. Honour system, deliberately.

Run after `python -m epw_ingest.build_assets`, or via `make static`.
"""
from __future__ import annotations

import json
import shutil

from app import config
from epw_ingest.catalog import CHARTS
from epw_ingest.koppen import DESCRIPTIONS

ANSWERS_DIR = config.ASSETS_DIR / "answers"

# Everything the client needs to name a place once the round is over.
ANSWER_FIELDS = (
    "city", "state", "country", "lat", "lon",
    "elevation_m", "koppen", "koppen_description",
)


def build() -> int:
    if not config.MANIFEST.exists():
        raise SystemExit(
            "assets/manifest.json missing — run `python -m epw_ingest.build_assets` first."
        )

    manifest = json.loads(config.MANIFEST.read_text(encoding="utf-8"))
    locations = manifest.get("locations", [])
    if not locations:
        raise SystemExit("manifest has no locations.")

    # Rewritten from scratch each run so a location dropped from the manifest
    # does not leave a stale answer file behind on the published site.
    if ANSWERS_DIR.exists():
        shutil.rmtree(ANSWERS_DIR)
    ANSWERS_DIR.mkdir(parents=True)

    pool = []
    for entry in locations:
        loc_id = entry["id"]
        (ANSWERS_DIR / f"{loc_id}.json").write_text(
            json.dumps({k: entry.get(k) for k in ANSWER_FIELDS}), encoding="utf-8"
        )
        # The pool entry must survive being read in full by a curious player,
        # so it carries the id, the chart list, and nothing else.
        pool.append({"id": loc_id, "charts": entry.get("charts", [])})

    index = {
        "max_score": config.MAX_SCORE,
        "max_attempts": config.MAX_ATTEMPTS,
        "attempt_penalty": config.ATTEMPT_PENALTY,
        "accept_radius_km": config.ACCEPT_RADIUS_KM,
        "distance_scale_km": config.DISTANCE_SCALE_KM,
        "distance_weight": config.DISTANCE_WEIGHT,
        "climate_weight": config.CLIMATE_WEIGHT,
        "hint_penalty": config.HINT_PENALTY,
        "min_hint_multiplier": config.MIN_HINT_MULTIPLIER,
        "compass_points": list(config.COMPASS_POINTS),
        "recent_memory": config.RECENT_MEMORY,
        "charts": [
            {"id": c.id, "label": c.label, "tier": c.tier, "blurb": c.blurb}
            for c in CHARTS
        ],
        "koppen_options": [
            {"code": code, "label": label} for code, label in sorted(DESCRIPTIONS.items())
        ],
        "locations": pool,
    }
    (config.ASSETS_DIR / "index.json").write_text(json.dumps(index), encoding="utf-8")

    print(f"[static] {len(pool)} locations -> assets/index.json + assets/answers/")
    return len(pool)


if __name__ == "__main__":
    build()
