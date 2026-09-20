# Drop your `.epw` files here

One file per location, straight from the source — no renaming needed. The build
script reads the header for city/country/lat/lon and derives an opaque asset id
from the filename, so `USA_MA_Boston-Logan.Intl.AP.725090_TMY3.epw` is fine.

## Where to get them

- **EnergyPlus**: https://energyplus.net/weather (download links are `.zip`; unzip
  and keep the `.epw`)
- **Climate.OneBuilding.org**: https://climate.onebuilding.org (better global
  coverage, especially Africa / South America / Central Asia)

## Picking the first 20

Aim for spread, not for cities you like. The game is unplayable if every round is
a temperate northern-hemisphere city, and players will learn to just guess Europe.
A reasonable starting 20:

| Köppen | Suggested | Why it's a distinct signature |
|---|---|---|
| Af  | Singapore; Belém, BR | Flat 8760-hour temperature, RH pinned high, no seasons |
| Am  | Mumbai, IN | Monsoon: sharp wet-season humidity step |
| Aw  | Darwin, AU | Tropical but strongly seasonal — and southern hemisphere |
| BWh | Phoenix, US; Dubai, AE | Enormous diurnal swing, very low humidity ratio |
| BSk | Denver, US | Arid + cold winters, high elevation |
| Csa | Lisbon, PT; Los Angeles, US | Dry-summer signature, mild winter |
| Csb | San Francisco, US | Cool dry summer — near-identical to Lisbon, a good trap |
| Cfa | Atlanta, US; Tokyo, JP | Humid subtropical, hot muggy summer |
| Cfb | London, GB; Auckland, NZ | Mild year-round, narrow temperature band |
| Dfa | Chicago, US | Big annual swing, cold winter |
| Dfb | Stockholm, SE | Cold winter, short mild summer, extreme day-length range |
| Dfc | Fairbanks, US | Subarctic, near-polar sun path |
| ET  | Reykjavík, IS | Cold, damp, almost no summer |
| Cwb | Bogotá, CO | Tropical highland — equatorial sun path, cool temperatures |
| BWk | Ürümqi, CN | Cold desert, strongly continental |

Southern-hemisphere entries matter more than they look: they flip the sun path and
invert the temperature curve, which is the single most learnable tell in the game.

## If this folder is inside OneDrive or Dropbox

Right-click it and choose **Always keep on this device**. Cloud storage evicts
files it thinks are cold, and an evicted EPW makes the build stall for ~20
seconds and then fail. Alternatively keep the raw files anywhere local:

```bash
EPW_RAW_DIR=~/climate-epw make build
```

## After uploading

```bash
make build        # or: python -m epw_ingest.build_assets
```

The script prints a per-file report and tells you which locations need a manual
Köppen entry in `epw_ingest/curation.csv` (EPW precipitation fields are often blank).
