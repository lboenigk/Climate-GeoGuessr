"""Leak and integrity checks for the published site.

scripts/smoke_test.py covers the FastAPI path. This covers the one that
actually ships: _site/, as GitHub Pages will serve it.

The interesting assertion is the first one. On a static host the answer key is
reachable in principle, so the thing worth defending is that nothing a player
loads *during* a round names a place — index.json and the chart files. Those
are checked here against every city, country and coordinate in the manifest.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "_site"

passed, failed = 0, 0


def check(ok: bool, label: str) -> None:
    global passed, failed
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if ok:
        passed += 1
    else:
        failed += 1


def main() -> int:
    if not SITE.exists():
        sys.exit("_site/ not built — run `make site` first.")

    manifest = json.loads((ROOT / "assets" / "manifest.json").read_text())["locations"]
    index = json.loads((SITE / "assets" / "index.json").read_text())

    print("\nPublished layout")
    check(not (SITE / "assets" / "manifest.json").exists(),
          "manifest.json is not published")
    check(not list(SITE.rglob("*.epw")), "no raw EPW files published")
    check((SITE / ".nojekyll").exists(), "Jekyll is disabled")
    check((SITE / "index.html").exists() and (SITE / "engine.js").exists(),
          "entry point and engine present")

    print("\nBoot payload names no place")
    index_text = json.dumps(index)
    # Names short enough to collide with ordinary words would produce false
    # positives; the coordinates below are the check that cannot be fooled.
    names = {n for e in manifest for n in (e.get("city"), e.get("country"))
             if n and len(n) > 3}
    hits = sorted(n for n in names if re.search(rf"\b{re.escape(n)}\b", index_text, re.I))
    check(not hits, f"index.json mentions no city or country{' — ' + str(hits) if hits else ''}")
    check(not re.search(r'"(lat|lon|koppen|city|country|elevation_m)"\s*:', index_text),
          "index.json carries no location fields")

    print("\nRound assets name no place")
    # Coordinates are the tell that survives sanitizing: a figure that leaked
    # its station header would carry the real lat/lon as a literal.
    coords = {str(round(float(e[k]), 2)) for e in manifest for k in ("lat", "lon")}
    leaked = []
    for entry in manifest[:8]:          # a sample; the full sweep is make check
        for chart in (SITE / "assets" / "charts" / entry["id"]).glob("*.json"):
            text = chart.read_text()
            for name in (entry.get("city"), entry.get("country")):
                if name and len(name) > 3 and re.search(rf"\b{re.escape(name)}\b", text, re.I):
                    leaked.append(f"{chart.name}: {name}")
            for c in (str(round(entry["lat"], 2)), str(round(entry["lon"], 2))):
                if f'"{c}"' in text:
                    leaked.append(f"{chart.name}: coord {c}")
    check(not leaked, f"sampled chart files leak nothing{' — ' + str(leaked[:3]) if leaked else ''}")

    print("\nEvery round is playable")
    missing = []
    for entry in manifest:
        if not (SITE / "assets" / "answers" / f"{entry['id']}.json").is_file():
            missing.append(f"answer {entry['id']}")
        for chart in entry.get("charts", []):
            if not (SITE / "assets" / "charts" / entry["id"] / f"{chart}.json").is_file():
                missing.append(f"chart {entry['id']}/{chart}")
        if not (SITE / "assets" / "hints" / entry["id"] / "summary.json").is_file():
            missing.append(f"summary {entry['id']}")
    check(not missing, f"all {len(manifest)} locations complete"
                       f"{' — missing ' + str(missing[:3]) if missing else ''}")
    check(len(index["locations"]) == len(manifest),
          f"index pool matches manifest ({len(index['locations'])})")
    check(all(a["lat"] is not None and a["lon"] is not None for a in (
              json.loads((SITE / "assets" / "answers" / f"{e['id']}.json").read_text())
              for e in manifest)), "every answer has coordinates")

    print("\nNo absolute asset paths")
    # A leading slash resolves to the domain root, not /<repo>/, so it 404s on
    # a project Pages site while working fine on localhost.
    bad = []
    for f in list(SITE.glob("*.html")) + list(SITE.glob("*.js")) + list(SITE.glob("*.css")):
        for m in re.finditer(r'(?:src|href)=["\'](/[^/"\']\S*?)["\']', f.read_text()):
            bad.append(f"{f.name}: {m.group(1)}")
    check(not bad, f"no root-absolute references{' — ' + str(bad) if bad else ''}")

    print(f"\n{passed} passed, {failed} failed\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
