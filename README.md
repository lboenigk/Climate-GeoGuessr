# Climate GeoGuessr

A guessing game built on EPW weather files. You get a year of climate data for
somewhere on Earth — psychrometric chart, temperature and humidity heat maps,
radiation, wind rose — and you drop a pin on a map. Scoring blends how close you
got with whether you named the right Köppen climate class.

Charts are generated locally with `ladybug` + `ladybug-charts` (the same library
the CBE Clima Tool was built on), not scraped or iframed from anyone's server.

---

## Quickstart

```bash
make venv                       # Python 3.12 venv + pinned deps
# drop .epw files into epw_ingest/raw/   (see the README in that folder)
make build                      # bake charts -> assets/
make serve                      # build _site/ and preview on :8000
make check                      # end-to-end test, including the leak checks
```

Three EnergyPlus sample files (Boston, Singapore, Lisbon) are already in
`epw_ingest/raw/` so the pipeline runs before you add your own. Replace them.

---

## How it fits together

```
epw_ingest/raw/*.epw          you supply these
        │
        │  make build   (offline, seconds per city, never at request time)
        ▼
assets/charts/<opaque id>/    core charts, served statically
assets/hints/<opaque id>/     numeric summary, served only via API
assets/manifest.json          THE ANSWER KEY — outside every static mount
        │
        │  loaded into SQLite on startup
        ▼
app/main.py                   FastAPI: /api/round, /api/hint, /api/guess
web/                          Plotly.js charts + MapLibre guess map
```

| File | Role |
|---|---|
| `epw_ingest/catalog.py` | Which charts exist, and the fixed axis ranges. No heavy imports — the runtime needs this but not ladybug. |
| `epw_ingest/charts.py` | The actual ladybug/Plotly renderers. Build-time only. |
| `epw_ingest/sanitize.py` | Strips location identifiers out of figures, then re-checks. |
| `epw_ingest/koppen.py` | Köppen-Geiger classification + the similarity metric used in scoring. |
| `epw_ingest/build_assets.py` | The offline pipeline. |
| `app/scoring.py` | Distance + climate-class blend. |
| `app/db.py` | SQLite: answer key, in-flight rounds, scores. |

### Why precompute

EPW files are 8,760-row hourly datasets. Parsing one and rendering seven Plotly
figures takes several seconds — fine once per city, unacceptable per round. The
build bakes figure JSON to disk; the server only ever hands out URLs, and the
browser does the rendering. The runtime image doesn't even install ladybug.

---

## Not leaking the answer

This is the part that will break if you're careless, and it breaks silently.

**Metadata in the figures.** EPW headers embed the city, state, country, WMO
station number and coordinates, and ladybug threads those into titles, legend
names and hovertemplates. `sanitize.py` walks every string in the serialized
figure and blanks any that mention the location, then `audit()` re-checks the
cleaned figure and **the build fails hard** if anything survives. Both passes
match on word boundaries against strings only — an earlier version scanned the
raw JSON and matched latitude `42` against hour-of-day tick labels.

**Guessable URLs.** Asset folders are `sha1(filename)[:12]`, not
`USA_MA_Boston`. Players open the Network tab.

**Hint bypass.** A hint is worth score to reveal. The server kept hint assets
in `assets/hints/`, outside the static mount, and returned them inline only
after charging the round. On Pages that directory is published like any other,
so the charge is enforced in `engine.js` and is honour-system — the same trade
as the answer key above.

The sun path used to be gated this way too. It isn't any more — see *Attempts*
below — and it now ships as an ordinary core chart.

**The answer key.** Under the retired FastAPI server this was airtight:
`assets/manifest.json` sat one level above the mounted `assets/charts/`
directory, so no URL reached it, and the answer crossed the wire only in the
response to `POST /api/guess`.

On GitHub Pages it cannot be. A static host has no code to withhold anything,
so the answer key must be reachable by the browser. What survives is the
*shape* of the guarantee: answers are split per-location under
`assets/answers/<id>.json` and fetched only once a guess is committed, so
nothing in the page load or in a round's traffic names a place. A player who
opens devtools, reads the opaque id off a chart URL and fetches that path by
hand can still read the answer. That is a deliberate trade for free, always-on
hosting — see *Hosting*. Everything else in this section still holds, and
matters more now, not less: the charts themselves are the only thing standing
between a player and the answer.

**Fixed axis ranges.** Every chart is locked to the same global ranges
(`catalog.py`). Auto-scaled axes would leak the answer outright — a colorbar
topping out at 45 °C versus 20 °C tells you the answer before you read the
chart — and would make rounds incomparable. Change a range and rebuild
everything.

`make check` asserts all of the above.

---

## Scoring

```
score = MAX * (0.70 · e^(−km/1500) + 0.30 · koppen_similarity)
            · hint_multiplier · attempt_multiplier
```

Pure distance is the wrong metric here. Lisbon and San Francisco have
near-identical psychrometric charts; so do Chicago and Boston. A player who
reads "warm-summer Mediterranean" correctly and picks the wrong continent has
understood the climate, and the score should say so. Köppen similarity is
graded, not binary: exact match 1.0, same first two letters 0.7, same group 0.4.

Naming a climate class is optional. If the player skips it — or the location has
no class — distance takes the **full** weight rather than the round being
silently capped at 70%.

Each hint costs 15%, floored at 40%, and is charged per distinct hint so a
reload doesn't drain the score.

### Attempts

A round is three guesses, not one. A guess further than `ACCEPT_RADIUS_KM`
(1000 km) from the answer comes back as a miss worth `−25%`, and the round
stays open; land inside the radius, or burn all three, and it resolves.

A miss returns **a compass direction and nothing else**. Not the distance —
three guesses with distances attached trilaterate the answer outright, and a
player who can solve a round that way never opens a chart. A direction narrows
a hemisphere; a direction plus a magnitude ends the game.

1000 km is wide for the same reason `DISTANCE_SCALE_KM` is. Reading
"hot-summer continental, maritime influence" off the charts and landing on New
York instead of Boston is a correct read of the climate, and the radius should
say so.

Hints and attempts compound (`hint_multiplier · attempt_multiplier`) rather
than sharing a floor: they are independent decisions and each carries its own
price. Three attempts and a hint is still worth 42% of the round.

Tune all of it in `app/config.py`.

---

## Curation

`make build` reads every `.epw` in `epw_ingest/raw/` and writes
`epw_ingest/curation.suggested.csv` listing them all. Fill in the columns you
care about and copy it over `curation.csv`:

| Column | Effect |
|---|---|
| `koppen` | Overrides the derived class. Usually required — see below. |
| `difficulty` | 1–5. Reserved for difficulty tiers. |
| `display_name` | Fixes names like `SINGAPORE` or `Boston Logan IntL Arpt`. |
| `enabled` | `false` drops a location from rotation without deleting the file. |

**You will almost always have to fill in `koppen` by hand.** Köppen needs
monthly rainfall, and the EPW liquid-precipitation field is blank in most
EnergyPlus TMY3/IWEC files — all three bundled samples have it 83–100% missing.
Climate.OneBuilding's `TMYx` files carry it more often. When precipitation is
usable the classifier runs automatically and is accurate (it gets Boston `Dfa`,
Singapore `Af`, Phoenix `BWh`, Lisbon `Csa`); when it isn't, the build tells you
which files need a manual entry.

The build also warns when fewer than a quarter of locations are southern
hemisphere. Take that seriously: EPW coverage is heavily US/Europe/Japan, and if
you inherit that bias players learn to always guess north and the game dies.

---

## Hosting

The game is deployed as a **static site on GitHub Pages**. There is no server
in production: `web/engine.js` runs the round loop in the browser against the
baked assets, implementing the same four calls `app/main.py` used to serve.

```bash
make build     # only when EPW files change - bakes charts with ladybug
make serve     # preview _site/ exactly as Pages will serve it
git push       # .github/workflows/pages.yml deploys on every push to main
```

`make build` is the slow, dependency-heavy step and stays local; its output
(`assets/charts/`, `assets/hints/`, `assets/manifest.json`) is committed. CI
only runs `build_static.py` and `build_site.py`, which are stdlib-only, so the
workflow needs no `pip install`.

**One-time setup:** Settings -> Pages -> Source: **GitHub Actions**.

### What the static build changes

`build_static.py` splits `assets/manifest.json` into two published pieces:

| file | holds | fetched |
| --- | --- | --- |
| `assets/index.json` | opaque ids, chart lists, game constants | at boot |
| `assets/answers/<id>.json` | city, coordinates, Köppen class | after a guess is committed |

`manifest.json` itself is never published. Nothing in the page load names a
place, so the Network tab still shows nothing during a round — but this is now
a convention, not a guarantee. See *Not leaking the answer*.

### Paths must stay relative

A project Pages site is served from `/<repo>/`, not `/`. Every asset reference
in `web/` is relative for that reason; a leading slash 404s in production while
working fine against `localhost:8000`. `make serve` reproduces the root-served
case — to check the subpath case, serve `_site/` from inside a directory named
after the repo.

### The retired server

`app/`, `Dockerfile` and `fly.toml` still work and still pass `make check`.
They are what you would go back to if you wanted the answer key off the client
again — `engine.js` deliberately keeps the FastAPI response shapes, so
restoring `app.js`'s `fetch` wrapper is the whole change. `make run` still
starts it, but it does not serve `assets/`, so use `make serve` to preview.

---

## Known constraints

- **This repo lives inside OneDrive, and that broke the build once already.**
  Files-On-Demand evicted the 1.5 MB EPW files to the cloud; the next read
  blocked for ~20 seconds and then failed with an I/O error, with the build
  process just sitting there. Two fixes, either is fine:
  right-click `epw_ingest/raw/` → **Always keep on this device**, or keep the
  raw files on local disk and point the build at them:

  ```bash
  EPW_RAW_DIR=~/climate-epw make build
  ```

  The same can happen to `assets/` at serving time, so mark that one too if you
  keep the project here. `build_assets.py` now fails with an explicit message
  naming this cause rather than hanging.
- **pandas is pinned below 2.2.** `ladybug-charts` 1.19.4 calls `pd.date_range(freq="H")`,
  which pandas 3 removed. Unpinning breaks every chart with
  `ValueError: Invalid frequency: H`.
- **The basemap is OpenFreeMap** (`positron`, or `dark` to match the system
  theme) — keyless OSM vector tiles with country borders, state and provincial
  borders, and city labels. `demotiles` stays wired in as the outage fallback.

  This reverses an earlier decision to keep the map deliberately blank, on the
  theory that a labelled basemap lets players read place names instead of the
  charts. It doesn't: a basemap has no idea which city the round is, so there
  is nothing on it to read off. What a blank globe actually did was make
  players hunt for a coastline to click after they had already worked out the
  answer, which punished the reading rather than rewarding it.

  `tuneBasemap()` in `web/app.js` adjusts the stock style in two ways that
  matter. `boundary_3` (state and provincial lines) ships with `minzoom: 8`,
  well past anything this game is played at, so it is lowered to 3 — without
  that, sub-national borders never render. `label_town` is removed, because
  towns and villages crowd out the cities at high zoom.

  If you swap the style, both adjustments are keyed to OpenMapTiles layer
  names (`openmaptiles` source, `place` and `boundary` source-layers) and will
  no-op on a style that names things differently.
- **`ladybug-charts` is AGPL-3.0**, and the CBE Clima Tool it derives from is
  MIT. AGPL's network clause means serving a web app that links it obliges you
  to offer source to users. This repo's build links it; the runtime image does
  not ship it. That distinction is not legal advice — if you're publishing this,
  publish the source and be done with it. If you ever need it closed, drop
  `ladybug-charts` and draw the psychrometric chart yourself with `psychrolib`
  + Plotly.
- If you write this up, cite Betti, Tartarini & Schiavon (2023), *Building
  Simulation* for Clima.

---

## Accessibility

Charts alone are unusable for screen-reader and low-vision players, so every
round has a numeric equivalent: `summary.json` carries monthly means, minima,
maxima, RH, radiation, wind and degree-days, rendered as a real `<table>` with
row and column headers. It doubles as the "monthly data table" hint — the
compliance path and the good game-design path turned out to be the same thing.

Misses are announced, not just shown: the compass direction is the only
feedback between attempts, so it lives in a `role="alert"` region rather than
being conveyed by colour and position alone.

Guessing is keyboard-reachable without the map via the "Enter coordinates"
fields and the climate-class `<select>`. Chart tabs are a proper ARIA tablist.

Still open: the ladybug colorsets aren't all colorblind-safe. A viridis/cividis
toggle would need a `colors=` argument threaded through `charts.py` and a
rebuild.
