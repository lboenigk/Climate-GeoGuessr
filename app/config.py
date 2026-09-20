"""Paths and tunable game constants."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# assets/charts/ is mounted as static. assets/manifest.json is the answer key
# and sits deliberately one level *outside* that mount, so it cannot be fetched.
ASSETS_DIR = ROOT / "assets"
CHARTS_DIR = ASSETS_DIR / "charts"

# Hint-tier assets (sun path, the numeric summary) live outside the static
# mount on purpose. If they sat in assets/charts/ next to the core charts, a
# player could read the opaque id off a core chart URL and fetch the sun path
# directly, paying no score penalty. They are served only through an endpoint
# that checks the round first.
HINTS_DIR = ASSETS_DIR / "hints"

MANIFEST = ASSETS_DIR / "manifest.json"
WEB_DIR = ROOT / "web"

DB_PATH = Path(os.environ.get("CLIMATE_DB", ROOT / "game.db"))

# --- Scoring ---------------------------------------------------------------
MAX_SCORE = 5000

# Distance at which the distance component decays to 1/e of full marks.
# Generous on purpose: this is a climate game, not a geography game, and
# Chicago vs Boston is a *correct* read of the charts.
DISTANCE_SCALE_KM = 1500.0

# Split between "where is it" and "what kind of climate is it".
DISTANCE_WEIGHT = 0.70
CLIMATE_WEIGHT = 0.30

# Each hint revealed costs this fraction, floored so a hinted round is still
# worth playing out.
HINT_PENALTY = 0.15
MIN_HINT_MULTIPLIER = 0.40

# --- Attempts --------------------------------------------------------------
# A round is not one shot. A guess outside ACCEPT_RADIUS_KM comes back as a
# miss with a compass direction and costs ATTEMPT_PENALTY, until the player
# either lands inside the radius or burns MAX_ATTEMPTS.
#
# The radius is wide on purpose, for the same reason DISTANCE_SCALE_KM is:
# reading "hot-summer continental, maritime influence" off the charts and
# landing on New York instead of Boston is a correct read, not a near miss.
MAX_ATTEMPTS = 3
ATTEMPT_PENALTY = 0.25
ACCEPT_RADIUS_KM = 1000.0

# Only a compass direction is returned on a miss, never the distance. Distance
# would let a player trilaterate the answer from three guesses without ever
# opening a chart, which is the one thing this game cannot afford.
COMPASS_POINTS = ("north", "north-east", "east", "south-east",
                  "south", "south-west", "west", "north-west")

# How long a round stays open before the answer is discarded.
ROUND_TTL_SECONDS = 60 * 60

# Don't repeat a location until this many other rounds have gone by.
RECENT_MEMORY = 20
