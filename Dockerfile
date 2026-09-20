# Assets are baked at image build time, so the running container never parses
# an EPW. Ship epw_ingest/raw/ in the build context (it is git-ignored, so copy
# the files in or mount them) or COPY a prebuilt assets/ directory instead.
FROM python:3.12-slim AS build

WORKDIR /src
RUN apt-get update && apt-get install -y --no-install-recommends \
        libexpat1 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY epw_ingest ./epw_ingest
RUN python -m epw_ingest.build_assets || echo "no EPWs in context; expecting prebuilt assets/"

# --- runtime -----------------------------------------------------------------
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 CLIMATE_DB=/data/game.db

COPY requirements.txt .
# The runtime never renders a chart, so it does not need ladybug or plotly.
RUN pip install --no-cache-dir fastapi==0.141.1 "uvicorn[standard]==0.53.0"

COPY app ./app
COPY web ./web
COPY epw_ingest/__init__.py epw_ingest/koppen.py epw_ingest/catalog.py ./epw_ingest/
COPY --from=build /src/assets ./assets

VOLUME /data
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
