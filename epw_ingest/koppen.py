"""Köppen-Geiger classification from monthly temperature and precipitation.

Used for the climate-similarity half of the score: a player who reads
"hot-summer Mediterranean" correctly but picks the wrong continent should not
walk away with zero.

Follows the rule set in Peel, Finlayson & McMahon (2007), which is the version
most of the published Köppen maps use.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

# Precipitation is the weak link: a lot of TMY files leave the liquid
# precipitation depth field at its missing sentinel or flat zero.
EPW_MISSING_PRECIP = 999.0


@dataclass
class KoppenResult:
    code: Optional[str]          # e.g. "Csa", or None if unclassifiable
    reason: Optional[str] = None  # why it failed, for the build report


def _half_years(southern: bool) -> tuple[List[int], List[int]]:
    """Return (summer_months, winter_months) as 0-based indices."""
    north_summer = [3, 4, 5, 6, 7, 8]          # Apr-Sep
    north_winter = [9, 10, 11, 0, 1, 2]        # Oct-Mar
    if southern:
        return north_winter, north_summer
    return north_summer, north_winter


def classify(
    monthly_temp_c: List[float],
    monthly_precip_mm: List[float],
    latitude: float,
) -> KoppenResult:
    if len(monthly_temp_c) != 12 or len(monthly_precip_mm) != 12:
        return KoppenResult(None, "need exactly 12 monthly values")

    p_ann = sum(monthly_precip_mm)
    if p_ann <= 0:
        return KoppenResult(None, "no usable precipitation data in EPW")

    southern = latitude < 0
    summer, winter = _half_years(southern)

    mat = sum(monthly_temp_c) / 12.0
    t_hot = max(monthly_temp_c)
    t_cold = min(monthly_temp_c)
    t_mon10 = sum(1 for t in monthly_temp_c if t >= 10.0)

    p_dry = min(monthly_precip_mm)
    p_s_dry = min(monthly_precip_mm[m] for m in summer)
    p_w_dry = min(monthly_precip_mm[m] for m in winter)
    p_s_wet = max(monthly_precip_mm[m] for m in summer)
    p_w_wet = max(monthly_precip_mm[m] for m in winter)

    p_summer = sum(monthly_precip_mm[m] for m in summer)
    p_winter = sum(monthly_precip_mm[m] for m in winter)

    # Aridity threshold shifts with *when* the rain falls: summer rain
    # evaporates harder, so the same annual total goes further in a winter-rain
    # climate.
    if p_winter >= 0.7 * p_ann:
        p_threshold = 2 * mat
    elif p_summer >= 0.7 * p_ann:
        p_threshold = 2 * mat + 28
    else:
        p_threshold = 2 * mat + 14

    # --- E: polar (checked first; temperature alone decides it)
    if t_hot < 10.0:
        return KoppenResult("EF" if t_hot < 0.0 else "ET")

    # --- B: arid (outranks A/C/D)
    if p_ann < 10 * p_threshold:
        first = "BW" if p_ann < 5 * p_threshold else "BS"
        return KoppenResult(first + ("h" if mat >= 18.0 else "k"))

    # --- A: tropical
    if t_cold >= 18.0:
        if p_dry >= 60.0:
            return KoppenResult("Af")
        if p_dry >= 100.0 - (p_ann / 25.0):
            return KoppenResult("Am")
        return KoppenResult("Aw")

    # --- C / D share the seasonality and summer-heat letters
    dry_summer = p_s_dry < 40.0 and p_s_dry < p_w_wet / 3.0
    dry_winter = p_w_dry < p_s_wet / 10.0
    if dry_summer:
        second = "s"
    elif dry_winter:
        second = "w"
    else:
        second = "f"

    if t_hot >= 22.0:
        third = "a"
    elif t_mon10 >= 4:
        third = "b"
    elif t_cold < -38.0:
        third = "d"
    else:
        third = "c"

    if t_cold >= 0.0:
        return KoppenResult("C" + second + third)

    # D only: 'd' outranks 'c' when winters are extreme
    if t_cold < -38.0:
        third = "d"
    return KoppenResult("D" + second + third)


def similarity(a: Optional[str], b: Optional[str]) -> float:
    """0.0-1.0 climate-class agreement, used to soften pure-distance scoring.

    Graded rather than binary so that Cfa vs Cfb (Atlanta vs London) still
    beats Cfa vs BWh (Atlanta vs Dubai).
    """
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a[:2] == b[:2]:
        return 0.7
    if a[0] == b[0]:
        return 0.4
    # Groups that genuinely look alike on a psych chart get partial credit.
    near = {frozenset("CD"), frozenset("AC")}
    if frozenset(a[0] + b[0]) in near:
        return 0.15
    return 0.0


DESCRIPTIONS = {
    "Af": "Tropical rainforest", "Am": "Tropical monsoon", "Aw": "Tropical savanna",
    "As": "Tropical savanna (dry summer)",
    "BWh": "Hot desert", "BWk": "Cold desert", "BSh": "Hot semi-arid", "BSk": "Cold semi-arid",
    "Csa": "Hot-summer Mediterranean", "Csb": "Warm-summer Mediterranean",
    "Csc": "Cold-summer Mediterranean",
    "Cwa": "Monsoon humid subtropical", "Cwb": "Subtropical highland", "Cwc": "Cold subtropical highland",
    "Cfa": "Humid subtropical", "Cfb": "Oceanic", "Cfc": "Subpolar oceanic",
    "Dsa": "Hot-summer Mediterranean continental", "Dsb": "Warm-summer Mediterranean continental",
    "Dsc": "Mediterranean subarctic", "Dsd": "Mediterranean subarctic (severe winter)",
    "Dwa": "Monsoon hot-summer continental", "Dwb": "Monsoon warm-summer continental",
    "Dwc": "Monsoon subarctic", "Dwd": "Monsoon subarctic (severe winter)",
    "Dfa": "Hot-summer continental", "Dfb": "Warm-summer continental",
    "Dfc": "Subarctic", "Dfd": "Subarctic (severe winter)",
    "ET": "Tundra", "EF": "Ice cap",
}
