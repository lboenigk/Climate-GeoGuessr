"""Offline build: EPW files in -> sanitized chart assets + answer key out.

Run this whenever you add or replace an EPW. Nothing here happens at request
time: parsing an 8760-row EPW and rendering eight Plotly figures takes seconds,
which is fine once per city and unacceptable per round.

    python -m epw_ingest.build_assets                 # JSON only, fast
    python -m epw_ingest.build_assets --png           # + raster fallbacks
    python -m epw_ingest.build_assets --only boston   # rebuild one file

Outputs
    assets/charts/<opaque id>/<chart>.json    served to players
    assets/charts/<opaque id>/summary.json    text alternative / hint data
    assets/manifest.json                      THE ANSWER KEY - never served
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import traceback
import warnings
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional

# ladybug-charts 1.19.4 still calls a few pandas APIs that 2.1 deprecates.
# Harmless, but it buries the per-location build report in noise.
warnings.filterwarnings("ignore", category=FutureWarning, module="ladybug_charts")

from ladybug.epw import EPW

from epw_ingest import koppen
from epw_ingest import charts as renderers
from epw_ingest.catalog import CHARTS, TIER_CORE
from epw_ingest.sanitize import audit, sanitize_figure

ROOT = Path(__file__).resolve().parent.parent

# Override with EPW_RAW_DIR when the repo lives on cloud-synced storage.
# OneDrive/Dropbox "files on demand" will evict a 1.5 MB EPW it thinks is cold,
# and the next read blocks for ~20s and then fails outright. Keeping the raw
# files on local disk avoids the whole class of problem.
RAW_DIR = Path(os.environ.get("EPW_RAW_DIR", ROOT / "epw_ingest" / "raw"))
CURATION = ROOT / "epw_ingest" / "curation.csv"
SUGGESTED = ROOT / "epw_ingest" / "curation.suggested.csv"
OUT_DIR = ROOT / "assets" / "charts"
HINT_DIR = ROOT / "assets" / "hints"
MANIFEST = ROOT / "assets" / "manifest.json"

HDD_BASE_C = 18.0
CDD_BASE_C = 18.0


@dataclass
class Curation:
    """Manual overrides, keyed by EPW filename. All columns optional."""
    koppen: Optional[str] = None
    difficulty: Optional[int] = None      # 1 easy .. 5 hard
    display_name: Optional[str] = None    # overrides the EPW's city field
    enabled: bool = True


def load_curation() -> Dict[str, Curation]:
    if not CURATION.exists():
        return {}
    out: Dict[str, Curation] = {}
    with CURATION.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            name = (row.get("filename") or "").strip()
            if not name or name.startswith("#"):
                continue
            diff = (row.get("difficulty") or "").strip()
            out[name] = Curation(
                koppen=(row.get("koppen") or "").strip() or None,
                difficulty=int(diff) if diff else None,
                display_name=(row.get("display_name") or "").strip() or None,
                enabled=(row.get("enabled") or "true").strip().lower()
                not in ("false", "0", "no"),
            )
    return out


def opaque_id(filename: str) -> str:
    """Asset folder name. Must not be reversible to a city by eye.

    Players open the Network tab. `/charts/USA_MA_Boston/psychrometric.json`
    ends the game.
    """
    return hashlib.sha1(filename.encode("utf-8")).hexdigest()[:12]


def _monthly_lists(epw: EPW):
    temp = epw.dry_bulb_temperature
    by_month = temp.group_by_month()
    months = sorted(by_month)
    temp_mean = [round(sum(by_month[m]) / len(by_month[m]), 1) for m in months]
    temp_min = [round(min(by_month[m]), 1) for m in months]
    temp_max = [round(max(by_month[m]), 1) for m in months]

    rh_month = epw.relative_humidity.group_by_month()
    rh_mean = [round(sum(rh_month[m]) / len(rh_month[m]), 1) for m in months]

    rad_month = epw.global_horizontal_radiation.group_by_month()
    rad_total = [round(sum(rad_month[m]) / 1000.0, 1) for m in months]  # kWh/m2

    wind_month = epw.wind_speed.group_by_month()
    wind_mean = [round(sum(wind_month[m]) / len(wind_month[m]), 1) for m in months]

    return temp_mean, temp_min, temp_max, rh_mean, rad_total, wind_mean


def _monthly_precip(epw: EPW) -> tuple[Optional[List[float]], Optional[str]]:
    """Monthly precipitation totals in mm, or (None, reason) if unusable.

    A lot of TMY files leave this field at the 999 missing sentinel or flat
    zero, which would silently produce a desert classification for London.
    """
    try:
        values = epw.liquid_precipitation_depth.values
    except Exception as exc:  # field absent in some non-standard EPWs
        return None, f"no precipitation field ({exc.__class__.__name__})"

    missing = sum(1 for v in values if v >= koppen.EPW_MISSING_PRECIP)
    if missing > len(values) * 0.5:
        return None, f"precipitation {missing / len(values):.0%} missing"

    by_month = epw.liquid_precipitation_depth.group_by_month()
    months = sorted(by_month)
    totals = [
        round(sum(v for v in by_month[m] if v < koppen.EPW_MISSING_PRECIP), 1)
        for m in months
    ]
    if sum(totals) <= 0:
        return None, "precipitation is all zero"
    return totals, None


def _degree_days(epw: EPW) -> tuple[int, int]:
    hdd = cdd = 0.0
    for t in epw.dry_bulb_temperature.values:
        if t < HDD_BASE_C:
            hdd += HDD_BASE_C - t
        else:
            cdd += t - CDD_BASE_C
    return round(hdd / 24), round(cdd / 24)


def build_summary(epw: EPW, precip: Optional[List[float]]) -> dict:
    """Numbers-only view of the round.

    Two jobs at once: it is the screen-reader / low-vision alternative to the
    charts (charts alone are a WCAG fail), and it is the "hints" difficulty
    mode. Those turned out to want exactly the same data.
    """
    temp_mean, temp_min, temp_max, rh_mean, rad_total, wind_mean = _monthly_lists(epw)
    temps = epw.dry_bulb_temperature.values
    hdd, cdd = _degree_days(epw)
    return {
        "monthly": {
            "temp_mean_c": temp_mean,
            "temp_min_c": temp_min,
            "temp_max_c": temp_max,
            "rh_mean_pct": rh_mean,
            "radiation_kwh_m2": rad_total,
            "wind_mean_ms": wind_mean,
            "precip_mm": precip,
        },
        "annual": {
            "temp_mean_c": round(sum(temps) / len(temps), 1),
            "temp_min_c": round(min(temps), 1),
            "temp_max_c": round(max(temps), 1),
            "temp_range_c": round(max(temps) - min(temps), 1),
            "rh_mean_pct": round(
                sum(epw.relative_humidity.values) / len(epw.relative_humidity.values), 1
            ),
            "radiation_kwh_m2": round(sum(rad_total), 1),
            "precip_mm": round(sum(precip), 1) if precip else None,
            "hdd18": hdd,
            "cdd18": cdd,
        },
    }


def build_one(epw_path: Path, cur: Curation, write_png: bool) -> dict:
    try:
        epw = EPW(str(epw_path))
        loc = epw.location
    except OSError as exc:
        raise RuntimeError(
            f"could not read the file ({exc.strerror or exc}). If this repo is on "
            "OneDrive/Dropbox, the file has probably been evicted to the cloud - "
            "mark the folder 'always keep on this device', or point EPW_RAW_DIR "
            "at a local directory."
        ) from exc
    asset_id = opaque_id(epw_path.name)
    out_dir = OUT_DIR / asset_id
    hint_dir = HINT_DIR / asset_id
    out_dir.mkdir(parents=True, exist_ok=True)
    hint_dir.mkdir(parents=True, exist_ok=True)

    precip, precip_note = _monthly_precip(epw)
    temp_mean, *_ = _monthly_lists(epw)

    if cur.koppen:
        kop, kop_note = cur.koppen, "from curation.csv"
    elif precip:
        result = koppen.classify(temp_mean, precip, loc.latitude)
        kop, kop_note = result.code, result.reason
    else:
        kop, kop_note = None, precip_note

    charts_written = []
    for spec in CHARTS:
        # Not every location can draw every chart. Roughly a third of TMY
        # files have no usable precipitation, and a rainfall chart built from
        # the 999 sentinel would be worse than no chart at all — it would read
        # as a rainforest. The manifest's per-location `charts` list is what
        # tells the client which tabs to offer.
        if spec.id == "precipitation" and not precip:
            continue

        fig = renderers.build(spec.id, epw, precip=precip)
        clean = sanitize_figure(fig, loc)

        leaks = audit(clean, loc)
        if leaks:
            # Hard fail. A leaked chart is worse than a missing one.
            raise RuntimeError(
                f"{spec.id}: location identifiers survived sanitization: {leaks[:5]}"
            )

        target = out_dir if spec.tier == TIER_CORE else hint_dir
        (target / f"{spec.id}.json").write_text(json.dumps(clean), encoding="utf-8")
        if write_png:
            fig.write_image(target / f"{spec.id}.png", scale=2, width=1000, height=600)
        charts_written.append(spec.id)

    # Hint tier: costs score, so it must not be reachable from the static mount.
    (hint_dir / "summary.json").write_text(
        json.dumps(build_summary(epw, precip)), encoding="utf-8"
    )

    return {
        "id": asset_id,
        "source_file": epw_path.name,
        "city": cur.display_name or loc.city,
        "state": loc.state,
        "country": loc.country,
        "lat": round(loc.latitude, 4),
        "lon": round(loc.longitude, 4),
        "elevation_m": round(loc.elevation, 1),
        "time_zone": loc.time_zone,
        "koppen": kop,
        "koppen_note": kop_note,
        "koppen_description": koppen.DESCRIPTIONS.get(kop or "", None),
        "difficulty": cur.difficulty or 3,
        "charts": charts_written,
        "has_png": write_png,
        "has_precipitation": precip is not None,
    }


def _write_suggested(by_id: Dict[str, dict], curation: Dict[str, Curation]) -> None:
    """Emit a curation.csv scaffold pre-filled with what the build did know."""
    rows = sorted(by_id.values(), key=lambda e: (e["country"], e["city"]))
    with SUGGESTED.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["filename", "koppen", "difficulty", "display_name", "enabled"])
        for entry in rows:
            existing = curation.get(entry["source_file"], Curation())
            writer.writerow([
                entry["source_file"],
                entry["koppen"] or "",
                existing.difficulty or "",
                existing.display_name or "",
                "true",
            ])


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--png", action="store_true", help="also write PNG fallbacks")
    parser.add_argument("--only", help="substring match on filename")
    args = parser.parse_args(argv)

    curation = load_curation()
    epw_files = sorted(RAW_DIR.glob("*.epw"))
    if args.only:
        epw_files = [p for p in epw_files if args.only.lower() in p.name.lower()]

    if not epw_files:
        print(f"No .epw files in {RAW_DIR}. See the README there.", file=sys.stderr)
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    HINT_DIR.mkdir(parents=True, exist_ok=True)
    existing = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {"locations": []}
    by_id = {entry["id"]: entry for entry in existing.get("locations", [])}

    needs_koppen: List[str] = []
    failures: List[str] = []

    for path in epw_files:
        cur = curation.get(path.name, Curation())
        if not cur.enabled:
            print(f"  skip  {path.name} (disabled in curation.csv)")
            by_id.pop(opaque_id(path.name), None)
            continue
        try:
            entry = build_one(path, cur, args.png)
        except Exception as exc:
            failures.append(f"{path.name}: {exc}")
            print(f"  FAIL  {path.name}: {exc}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            continue

        by_id[entry["id"]] = entry
        label = f'{entry["city"]}, {entry["country"]}'
        kop = entry["koppen"] or "??"
        print(f'  ok    {label:<34} {kop:<4} {entry["id"]}')
        if not entry["koppen"]:
            needs_koppen.append(f'{path.name}  ({entry["koppen_note"]})')

    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(
        json.dumps({"locations": sorted(by_id.values(), key=lambda e: e["id"])}, indent=2),
        encoding="utf-8",
    )
    print(f"\n{len(by_id)} locations in {MANIFEST.relative_to(ROOT)}")

    if needs_koppen:
        # In practice this is most of them: EnergyPlus TMY3/IWEC files leave
        # liquid precipitation blank, and Köppen needs rainfall. Rather than
        # just complain, write a ready-to-edit CSV the user can paste over
        # curation.csv once they've filled in the class column.
        _write_suggested(by_id, curation)
        print(
            f"\n{len(needs_koppen)} location(s) have no Köppen class — they will "
            "score on distance alone.\n"
            f"  Fill in the `koppen` column of {SUGGESTED.relative_to(ROOT)} and "
            f"copy it over {CURATION.relative_to(ROOT)}."
        )
        for line in needs_koppen:
            print(f"  - {line}")

    awkward = [
        e for e in by_id.values()
        if e["city"] and (e["city"].isupper() or len(e["city"]) > 22)
    ]
    if awkward:
        print("\nThese city names will read badly on the answer screen — set "
              "`display_name` in curation.csv:")
        for entry in awkward:
            print(f'  - {entry["source_file"]}  ->  "{entry["city"]}"')

    hemispheres = [e for e in by_id.values() if e["lat"] < 0]
    if by_id and len(hemispheres) < len(by_id) * 0.25:
        print(
            f"\nWarning: only {len(hemispheres)}/{len(by_id)} locations are southern "
            "hemisphere. Players will learn to always guess north."
        )

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
