"""Assemble _site/ — exactly what gets published to GitHub Pages.

Used by both `make site` and the Pages workflow, so what you preview locally
is byte-for-byte what deploys.

Deliberately excluded:
  epw_ingest/raw/  ~47 MB of source EPW files the browser never reads. They
                   stay in the repo so anyone can rebuild; they do not need to
                   be on the CDN.
  assets/manifest.json  the full answer key in one file. The site reads the
                   split index.json + answers/ instead.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "_site"

# (source, destination relative to _site)
TREES = [
    (ROOT / "web", ""),
    (ROOT / "assets" / "charts", "assets/charts"),
    (ROOT / "assets" / "answers", "assets/answers"),
    (ROOT / "assets" / "hints", "assets/hints"),
]
FILES = [(ROOT / "assets" / "index.json", "assets/index.json")]


def build() -> Path:
    missing = [p for p, _ in TREES + FILES if not p.exists()]
    if missing:
        names = ", ".join(str(p.relative_to(ROOT)) for p in missing)
        sys.exit(f"missing: {names}\nRun `make build && make static` first.")

    if SITE.exists():
        shutil.rmtree(SITE)
    SITE.mkdir()

    for src, dest in TREES:
        shutil.copytree(src, SITE / dest if dest else SITE, dirs_exist_ok=True)
    for src, dest in FILES:
        target = SITE / dest
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)

    # Without this, Pages runs the tree through Jekyll, which skips files and
    # directories whose names begin with an underscore.
    (SITE / ".nojekyll").touch()

    total = sum(f.stat().st_size for f in SITE.rglob("*") if f.is_file())
    count = sum(1 for f in SITE.rglob("*") if f.is_file())
    print(f"[site] {count} files, {total / 1e6:.1f} MB -> _site/")
    return SITE


if __name__ == "__main__":
    build()
