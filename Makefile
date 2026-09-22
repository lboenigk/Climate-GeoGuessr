# The virtualenv lives OUTSIDE the project directory on purpose. This repo sits
# in OneDrive, and a venv is ~9,000 files: the sync client pegs a core trying to
# upload it and every read in the project starts stalling for tens of seconds.
# Override VENV if you move the project somewhere unsynced.
VENV ?= $(HOME)/.venvs/climate-guesser
PY := $(VENV)/bin/python

# Likewise, keep raw EPW files on local disk if the project is cloud-synced.
# EPW_RAW_DIR ?= $(HOME)/climate-epw

.PHONY: venv build build-png static site serve run clean reset-db check

venv:
	uv venv --python 3.12 $(VENV) 2>/dev/null || python3.12 -m venv $(VENV)
	$(PY) -m pip install -q --upgrade pip
	$(PY) -m pip install -q -r requirements.txt
	@echo "venv ready at $(VENV)"

# Bake charts for every EPW in epw_ingest/raw/ (or $$EPW_RAW_DIR). Safe to
# re-run: it updates the manifest in place and leaves other locations alone.
build:
	$(PY) -m epw_ingest.build_assets

build-png:
	$(PY) -m epw_ingest.build_assets --png

# Split assets/manifest.json into the public index + per-location answer files
# the static site reads. Cheap, stdlib-only, safe to re-run.
static:
	$(PY) -m epw_ingest.build_static

# Assemble _site/ — exactly what the Pages workflow publishes.
site: static
	$(PY) scripts/build_site.py

# Preview the published site the way GitHub will serve it. This is the one that
# matters before a deploy; `make run` exercises the retired FastAPI path.
serve: site
	@echo "http://localhost:8000/  (Ctrl-C to stop)"
	$(PY) -m http.server 8000 --directory _site

# The FastAPI server. No longer how the game is deployed — kept because it is
# still the only thing that exercises app/ — see README "Hosting".
run:
	$(PY) -m uvicorn app.main:app --reload --port 8000

check: site
	$(PY) scripts/smoke_test.py
	$(PY) scripts/static_check.py

# The answer key is rebuilt from assets/manifest.json on every startup, so
# dropping the DB only loses scores, never locations.
reset-db:
	rm -f game.db game.db-wal game.db-shm

clean:
	rm -rf assets/charts assets/hints assets/answers assets/index.json \
	       assets/manifest.json _site
