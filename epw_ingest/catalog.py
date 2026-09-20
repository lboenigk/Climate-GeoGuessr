"""What charts exist, and the axis ranges they are locked to.

Deliberately free of ladybug/plotly imports. The web app needs the chart
catalogue on every request but never renders anything, so the runtime image
installs neither library - keeping this module importable on its own is what
makes that possible. The renderers live in `charts.py`.

Fixed ranges are not cosmetic. Auto-scaled axes leak the answer outright: a
temperature heat map whose colorbar tops out at 45 C versus one topping out at
20 tells you the answer before you read the chart. Clima calls this its
"Global" value-range mode, for the same reason.

Change a range here and you must rebuild every location, or rounds stop being
comparable to one another.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

# --- Global ranges. Chosen to cover essentially every inhabited climate. -----
TEMP_RANGE = (-30.0, 50.0)        # deg C
HUMIDITY_RANGE = (0.0, 100.0)     # % RH
RADIATION_RANGE = (0.0, 1000.0)   # Wh/m2
MAX_HUMIDITY_RATIO = 0.03         # kg water / kg dry air

# "core" charts always ship with the round. "hint" charts cost score, and are
# stored outside the static mount so their URLs cannot be guessed.
TIER_CORE = "core"
TIER_HINT = "hint"


@dataclass(frozen=True)
class ChartSpec:
    id: str
    label: str
    tier: str
    blurb: str


CHARTS: List[ChartSpec] = [
    ChartSpec(
        "psychrometric", "Psychrometric chart", TIER_CORE,
        "Every hour of the year plotted by temperature and humidity ratio. "
        "The shape of the cloud is the climate's fingerprint.",
    ),
    ChartSpec(
        "temperature", "Temperature heat map", TIER_CORE,
        "Hour of day against day of year. Reads out the annual swing, the "
        "diurnal swing, and which half of the year is warm.",
    ),
    ChartSpec(
        "humidity", "Relative humidity heat map", TIER_CORE,
        "Separates maritime from continental, and finds monsoon onsets.",
    ),
    ChartSpec(
        "radiation", "Global horizontal radiation", TIER_CORE,
        "Day length and cloudiness. The envelope edges are a latitude tell.",
    ),
    ChartSpec(
        "diurnal", "Diurnal averages", TIER_CORE,
        "Average day per month for temperature, humidity and radiation.",
    ),
    ChartSpec(
        "windrose", "Wind rose", TIER_CORE,
        "Prevailing direction and strength - trade winds, westerlies, monsoon reversal.",
    ),
    # Was gated, on the theory that a latitude readout collapses the answer
    # space. In play it did the opposite: the one chart that orients you was
    # the one you had to pay for, so new players bounced off rounds they had
    # no purchase on. Latitude alone still leaves a whole parallel to search,
    # and the retry loop now carries the difficulty instead.
    ChartSpec(
        "sunpath", "Sun path", TIER_CORE,
        "Solar geometry through the year. Effectively a latitude readout.",
    ),
]

CHARTS_BY_ID: Dict[str, ChartSpec] = {c.id: c for c in CHARTS}
