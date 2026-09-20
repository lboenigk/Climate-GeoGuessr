"""Strip every trace of the answer out of a Plotly figure before it ships.

This is the highest-risk part of the build. EPW headers embed the city, state,
country, WMO station number and coordinates, and ladybug threads those through
titles, legend names, hovertemplates and axis labels. A player with DevTools
open will find any one of them.

Two passes, deliberately redundant:

  sanitize_figure()  walks the serialized figure and blanks any *string* that
                     mentions the location.
  audit()            re-checks the cleaned figure and the build fails on a hit,
                     so a chart type added later can't quietly bypass the first
                     pass.

Both operate on strings only. An earlier version scanned the whole JSON blob,
which matched latitude "42" against hour-of-day tick labels and state code "MA"
inside the word "normal" - everything was a false positive, and scrubbing on
substrings would have destroyed legitimate axis titles.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Pattern

# Layout keys that are pure chrome and never carry puzzle information.
_ALWAYS_CLEAR = ("title", "annotations")

# Keys whose string values are styling, never prose. Scanning them is all
# downside: "Arial" or "#c0ffee" can collide with a token and get blanked.
_SKIP_KEY_SUFFIXES = ("color", "colorscale", "colorway", "family", "colorbar")
_SKIP_KEYS = {"template", "type", "mode", "xref", "yref", "xaxis", "yaxis",
              "orientation", "side", "anchor", "ticks", "showlegend"}

# Words that appear in EPW header fields but identify nothing.
_GENERIC = {
    "data", "source", "unknown", "none", "null", "airport", "intl", "int",
    "ap", "tmy", "tmy2", "tmy3", "tmyx", "iwec", "rmy", "cwec", "igdg", "itmy",
    "wmo", "region", "custom", "user", "format", "period", "weather", "file",
    "station", "the", "and", "for", "new", "san", "city", "national",
}

_NAME_ATTRS = ("city", "state", "country", "station_id")
_COORD_ATTRS = ("latitude", "longitude", "elevation")

_HEX_OR_RGB = re.compile(r"^(#[0-9a-fA-F]{3,8}|rgba?\(|hsla?\()")
_REDACTED = ""


def _name_patterns(location) -> List[Pattern]:
    """Word-boundary patterns for every identifying word in the header."""
    words: set[str] = set()
    for attr in _NAME_ATTRS:
        raw = getattr(location, attr, None)
        if raw is None:
            continue
        raw = str(raw).strip()
        if not raw or raw.lower() in ("-", "n/a") or raw.lower() in _GENERIC:
            continue
        words.add(raw)
        # Headers are full of "Boston-Logan.Intl.AP" compounds; the individual
        # words are what a player would actually recognize.
        for part in re.split(r"[\s\-_.,/()]+", raw):
            if len(part) >= 2 and part.lower() not in _GENERIC:
                words.add(part)

    return [
        re.compile(r"(?<![0-9A-Za-z])" + re.escape(w) + r"(?![0-9A-Za-z])", re.I)
        for w in sorted(words, key=len, reverse=True)
    ]


def _coord_patterns(location) -> List[Pattern]:
    """Patterns for coordinates in decimal form.

    Only decimal forms. A bare integer like "42" is indistinguishable from an
    axis tick, so matching it produces nothing but false alarms - and a chart
    that printed the latitude would print it with a decimal anyway.
    """
    out: List[Pattern] = []
    for attr in _COORD_ATTRS:
        value = getattr(location, attr, None)
        if value is None:
            continue
        magnitude = abs(float(value))
        forms = {f"{magnitude:.1f}", f"{magnitude:.2f}", f"{magnitude:.3f}"}
        for form in forms:
            out.append(
                re.compile(r"(?<![0-9])" + re.escape(form) + r"(?![0-9])")
            )
    return out


def location_patterns(location) -> List[Pattern]:
    return _name_patterns(location) + _coord_patterns(location)


def _is_styling(key: str) -> bool:
    lowered = key.lower()
    return lowered in _SKIP_KEYS or lowered.endswith(_SKIP_KEY_SUFFIXES)


def _hits(text: str, patterns: List[Pattern]) -> List[str]:
    if not text or _HEX_OR_RGB.match(text):
        return []
    return [p.pattern for p in patterns if p.search(text)]


def _walk(node: Any, patterns: List[Pattern], scrub: bool, found: List[str]) -> Any:
    if isinstance(node, str):
        hit = _hits(node, patterns)
        if hit:
            found.append(node)
            return _REDACTED if scrub else node
        return node
    if isinstance(node, list):
        return [_walk(v, patterns, scrub, found) for v in node]
    if isinstance(node, dict):
        out: Dict[str, Any] = {}
        for key, value in node.items():
            if key in _ALWAYS_CLEAR:
                out[key] = None if key == "title" else []
                continue
            if _is_styling(key):
                out[key] = value
                continue
            out[key] = _walk(value, patterns, scrub, found)
        return out
    return node


def sanitize_figure(fig, location) -> dict:
    """Return a Plotly figure as a dict with all location identifiers removed."""
    fig.update_layout(title=None, annotations=[])

    # Round-trip through Plotly's own encoder so numpy arrays and datetimes
    # become plain JSON before we walk them.
    raw = json.loads(fig.to_json())
    clean = _walk(raw, location_patterns(location), scrub=True, found=[])

    # meta / uirevision occasionally carry the source filename.
    layout = clean.get("layout")
    if isinstance(layout, dict):
        layout.pop("meta", None)
        layout.pop("uirevision", None)
    return clean


def audit(figure_dict: dict, location) -> List[str]:
    """Report any string in the cleaned figure that still names the location."""
    found: List[str] = []
    _walk(figure_dict, location_patterns(location), scrub=False, found=found)
    return found
