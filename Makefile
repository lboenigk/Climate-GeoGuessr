# The virtualenv lives OUTSIDE the project directory on purpose. This repo sits
# in OneDrive, and a venv is ~9,000 files: the sync client pegs a core trying to
# upload it and every read in the project starts stalling for tens of seconds.
# Override VENV if you move the project somewhere unsynced.
VENV ?= $(HOME)/.venvs/climate-guesser
PY := $(VENV)/bin/python

# Likewise, keep raw EPW files on local disk if the project is cloud-synced.
# EPW_RAW_DIR ?= $(HOME)/climate-epw

.PHONY: venv build build-png run clean reset-db check

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

run:
	$(PY) -m uvicorn app.main:app --reload --port 8000

check:
	$(PY) scripts/smoke_test.py

# The answer key is rebuilt from assets/manifest.json on every startup, so
# dropping the DB only loses scores, never locations.
reset-db:
	rm -f game.db game.db-wal game.db-shm

clean:
	rm -rf assets/charts assets/hints assets/manifest.json
