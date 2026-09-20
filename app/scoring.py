"""Round scoring: distance plus climate-class agreement."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from app import config
from epw_ingest.koppen import similarity

EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def bearing_to(lat1: float, lon1: float, lat2: float, lon2: float) -> str:
    """Compass direction from the guess toward the answer, as one of eight points.

    Deliberately coarse, and deliberately not accompanied by a distance. A
    direction narrows the search; a direction plus a magnitude solves it
    outright from three guesses, with the charts never opened.
    """
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    deg = (math.degrees(math.atan2(y, x)) + 360.0) % 360.0
    # 8 sectors of 45 degrees, centred on each compass point.
    index = int((deg + 22.5) % 360.0 // 45.0)
    return config.COMPASS_POINTS[index]


def attempt_multiplier(attempts_used: int) -> float:
    """1.00 on the first guess, then ATTEMPT_PENALTY off per retry."""
    retries = max(0, attempts_used - 1)
    return max(0.0, 1.0 - config.ATTEMPT_PENALTY * retries)


@dataclass
class ScoreBreakdown:
    score: int
    distance_km: float
    distance_points: int
    climate_points: int
    climate_similarity: float
    hint_multiplier: float
    hints_used: int
    scored_climate: bool
    attempt_multiplier: float
    attempts_used: int


def score_round(
    guess_lat: float,
    guess_lon: float,
    answer_lat: float,
    answer_lon: float,
    guess_koppen: Optional[str],
    answer_koppen: Optional[str],
    hints_used: int = 0,
    attempts_used: int = 1,
) -> ScoreBreakdown:
    """Blend a distance score with a Köppen-class score.

    Pure distance is a bad fit for this game. Lisbon and San Francisco have
    near-identical psychrometric charts; so do Chicago and Boston. A player who
    reads "warm-summer Mediterranean" off the chart and picks the wrong
    continent has understood the climate, and the score should say so.

    The climate half only applies when both classes are known - many EPW files
    carry no precipitation data, so the answer's class can be missing, and the
    player may decline to name one. When it doesn't apply, distance takes the
    full weight rather than the round being silently capped at 70%.
    """
    distance = haversine_km(guess_lat, guess_lon, answer_lat, answer_lon)
    distance_fraction = math.exp(-distance / config.DISTANCE_SCALE_KM)

    scored_climate = bool(guess_koppen and answer_koppen)
    climate_fraction = similarity(guess_koppen, answer_koppen) if scored_climate else 0.0

    if scored_climate:
        dist_w, clim_w = config.DISTANCE_WEIGHT, config.CLIMATE_WEIGHT
    else:
        dist_w, clim_w = 1.0, 0.0

    hint_mult = max(
        config.MIN_HINT_MULTIPLIER,
        1.0 - config.HINT_PENALTY * max(0, hints_used),
    )
    # Hints and retries are independent axes, so they compound rather than
    # sharing the hint floor. MIN_HINT_MULTIPLIER caps what hints alone can
    # cost; burning retries on top is a separate decision with its own price.
    attempt_mult = attempt_multiplier(attempts_used)
    multiplier = hint_mult * attempt_mult

    distance_points = config.MAX_SCORE * dist_w * distance_fraction * multiplier
    climate_points = config.MAX_SCORE * clim_w * climate_fraction * multiplier

    return ScoreBreakdown(
        score=round(distance_points + climate_points),
        distance_km=round(distance, 1),
        distance_points=round(distance_points),
        climate_points=round(climate_points),
        climate_similarity=round(climate_fraction, 2),
        hint_multiplier=round(hint_mult, 2),
        hints_used=hints_used,
        scored_climate=scored_climate,
        attempt_multiplier=round(attempt_mult, 2),
        attempts_used=attempts_used,
    )
