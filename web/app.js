/* Climate GeoGuessr - round loop.
 *
 * The client deliberately knows nothing it shouldn't: it holds a round_id and
 * a list of chart URLs, and finds out where the round was only in the response
 * to POST /api/guess.
 */
(() => {
  "use strict";

  const MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];

  const el = (id) => document.getElementById(id);
  const state = {
    sessionId: null,
    round: null,
    charts: [],
    figures: new Map(),   // chart id -> figure JSON, cached for the round
    activeChart: null,
    guess: null,
    map: null,
    guessMarker: null,
    totalScore: 0,
    roundNo: 0,
    submitting: false,
    attempt: 1,          // 1-based, the attempt about to be submitted
    maxAttempts: 3,      // replaced from /api/meta
    attemptPenalty: 0.25,
  };

  /* --- session ---------------------------------------------------------- */
  function sessionId() {
    let id = null;
    try { id = localStorage.getItem("climate-session"); } catch (_) { /* private mode */ }
    if (!id) {
      id = (crypto.randomUUID && crypto.randomUUID()) ||
           String(Date.now()) + Math.random().toString(16).slice(2);
      try { localStorage.setItem("climate-session", id); } catch (_) { /* ignore */ }
    }
    return id;
  }

  async function api(path, body) {
    const res = await fetch(path, {
      method: body ? "POST" : "GET",
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      throw new Error(detail.detail || `${res.status} ${res.statusText}`);
    }
    return res.json();
  }

  /* --- map -------------------------------------------------------------- */
  // OpenFreeMap: keyless, OSM-derived vector tiles, country and state borders
  // and city labels already in the style. Both variants carry their own OSM
  // attribution, which MapLibre renders from the style's sources.
  //
  // This replaces the deliberately blank demotiles basemap. A labelled map
  // does not leak the answer — a basemap has no idea which city the round is —
  // it only lets a player place a pin on the city they have already reasoned
  // their way to, instead of guessing at a coastline on an empty globe.
  const BASEMAP = {
    light: "https://tiles.openfreemap.org/styles/positron",
    dark: "https://tiles.openfreemap.org/styles/dark",
  };
  // Kept as the offline/outage fallback: no labels, but always something.
  const BASEMAP_FALLBACK = "https://demotiles.maplibre.org/style.json";

  function initMap() {
    const dark = window.matchMedia?.("(prefers-color-scheme: dark)").matches;
    const map = new maplibregl.Map({
      container: "map",
      style: dark ? BASEMAP.dark : BASEMAP.light,
      center: [0, 20],
      zoom: 0.6,
      attributionControl: { compact: true },
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.on("click", (e) => setGuess(e.lngLat.lat, e.lngLat.lng));
    map.on("load", () => tuneBasemap(map));

    // If the tile host is unreachable the map is otherwise a blank box, so
    // drop back to the keyless demo style rather than showing nothing.
    setTimeout(() => {
      if (!map.isStyleLoaded()) {
        try { map.setStyle(BASEMAP_FALLBACK); } catch (_) { /* nothing better to do */ }
      }
    }, 8000);

    state.map = map;
  }

  /* Adjust the stock style for this game: state borders visible at the zooms
     the game is actually played at, and place labels cut back to real cities. */
  function tuneBasemap(map) {
    const has = (id) => !!map.getLayer(id);
    try {
      // admin_level 3-6 ships with minzoom 8 — far past this game's range, so
      // provincial and state lines would never appear at all.
      if (has("boundary_3")) map.setLayerZoomRange("boundary_3", 3, 24);

      // Towns and villages are noise here, and at high zoom they crowd out the
      // cities. "Major cities" is the useful granularity.
      if (has("label_town")) map.removeLayer("label_town");

      // Label layers are text only. A dot makes a city somewhere you can aim
      // at, which is the point of putting them on the map.
      if (!has("city-dot") && map.getSource("openmaptiles")) {
        map.addLayer({
          id: "city-dot",
          type: "circle",
          source: "openmaptiles",
          "source-layer": "place",
          minzoom: 2,
          filter: ["==", ["get", "class"], "city"],
          paint: {
            "circle-radius": ["interpolate", ["linear"], ["zoom"], 2, 1.6, 6, 3.2, 10, 4.5],
            "circle-color": ["case", ["==", ["get", "capital"], 2], "#e2553d", "#7c8794"],
            "circle-stroke-width": 0.8,
            "circle-stroke-color": "rgba(255,255,255,.75)",
          },
        }, has("label_city") ? "label_city" : undefined);
      }
    } catch (err) {
      // A style revision upstream should degrade to a plain map, not a
      // broken round.
      console.warn("basemap tuning skipped:", err);
    }
  }

  function setGuess(lat, lon) {
    if (state.submitting) return;
    state.guess = { lat, lon };
    if (!state.guessMarker) {
      state.guessMarker = new maplibregl.Marker({ color: "#1f6feb", draggable: true })
        .setLngLat([lon, lat])
        .addTo(state.map);
      state.guessMarker.on("dragend", () => {
        const p = state.guessMarker.getLngLat();
        state.guess = { lat: p.lat, lon: p.lng };
        el("submit-guess").disabled = false;
        renderReadout();
      });
    } else {
      state.guessMarker.setLngLat([lon, lat]);
    }
    el("submit-guess").disabled = false;
    renderReadout();
  }

  function renderReadout() {
    const g = state.guess;
    el("guess-readout").textContent = g
      ? `Guess: ${g.lat.toFixed(2)}°, ${g.lon.toFixed(2)}°`
      : "Click the map to drop a pin.";
  }

  function clearMapAnswer() {
    const map = state.map;
    ["answer-line", "answer-point"].forEach((id) => {
      if (map.getLayer(id)) map.removeLayer(id);
    });
    ["answer-line-src", "answer-point-src"].forEach((id) => {
      if (map.getSource(id)) map.removeSource(id);
    });
    if (state.guessMarker) { state.guessMarker.remove(); state.guessMarker = null; }
  }

  function showAnswerOnMap(answer, guess) {
    const map = state.map;
    // Unwrap the answer's longitude to whichever side of the antimeridian is
    // actually nearer the guess, so the arc drawn is the short way round.
    let lon = answer.lon;
    if (Math.abs(lon - guess.lon) > 180) lon += guess.lon > lon ? 360 : -360;

    map.addSource("answer-line-src", {
      type: "geojson",
      data: { type: "Feature", geometry: {
        type: "LineString", coordinates: [[guess.lon, guess.lat], [lon, answer.lat]] } },
    });
    map.addLayer({
      id: "answer-line", type: "line", source: "answer-line-src",
      paint: { "line-color": "#e2553d", "line-width": 2, "line-dasharray": [2, 1.5] },
    });
    map.addSource("answer-point-src", {
      type: "geojson",
      data: { type: "Feature", geometry: { type: "Point", coordinates: [lon, answer.lat] } },
    });
    map.addLayer({
      id: "answer-point", type: "circle", source: "answer-point-src",
      paint: {
        "circle-radius": 7, "circle-color": "#e2553d",
        "circle-stroke-width": 2, "circle-stroke-color": "#fff",
      },
    });

    const bounds = new maplibregl.LngLatBounds([guess.lon, guess.lat], [guess.lon, guess.lat]);
    bounds.extend([lon, answer.lat]);
    map.fitBounds(bounds, { padding: 60, maxZoom: 5, duration: 700 });
  }

  /* --- charts ----------------------------------------------------------- */
  const PLOT_CONFIG = { responsive: true, displaylogo: false,
                        modeBarButtonsToRemove: ["select2d", "lasso2d"] };

  async function showChart(chartId) {
    const chart = state.charts.find((c) => c.id === chartId);
    if (!chart) return;
    state.activeChart = chartId;

    for (const btn of el("chart-tabs").children) {
      btn.setAttribute("aria-selected", String(btn.dataset.chart === chartId));
    }
    el("chart-blurb").textContent = chart.blurb || "";

    let fig = state.figures.get(chartId);
    if (!fig) {
      el("chart-loading").hidden = false;
      fig = await (await fetch(chart.url)).json();
      state.figures.set(chartId, fig);
    }
    // Let the CSS box decide the size rather than whatever ladybug baked in.
    const layout = Object.assign({}, fig.layout, {
      autosize: true, height: null, width: null,
      margin: { l: 60, r: 30, t: 20, b: 50 },
    });
    await Plotly.react(el("chart"), fig.data, layout, PLOT_CONFIG);
    el("chart-loading").hidden = true;
  }

  function buildTabs() {
    const tabs = el("chart-tabs");
    tabs.innerHTML = "";
    state.charts.forEach((chart) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.role = "tab";
      btn.dataset.chart = chart.id;
      btn.textContent = chart.label;
      btn.setAttribute("aria-selected", "false");
      btn.addEventListener("click", () => showChart(chart.id));
      tabs.appendChild(btn);
    });
  }

  /* --- hints ------------------------------------------------------------ */
  function buildHints(hints) {
    const box = el("hint-buttons");
    box.innerHTML = "";
    hints.forEach((hint) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "ghost";
      btn.textContent = `${hint.label} (−${Math.round(hint.cost * 100)}%)`;
      btn.title = hint.blurb || "";
      btn.addEventListener("click", () => requestHint(hint, btn));
      box.appendChild(btn);
    });
  }

  async function requestHint(hint, btn) {
    btn.disabled = true;
    try {
      const res = await api("/api/hint", {
        session_id: state.sessionId,
        round_id: state.round.round_id,
        hint: hint.id,
      });
      btn.classList.add("spent");
      el("multiplier-box").hidden = false;
      el("multiplier").textContent = "×" + res.multiplier.toFixed(2);
      openHint(res);
    } catch (err) {
      btn.disabled = false;
      alert("Could not load hint: " + err.message);
    }
  }

  function openHint(res) {
    el("hint-h").textContent = res.label;
    const body = el("hint-body");
    body.innerHTML = "";

    if (res.kind === "summary") {
      body.appendChild(summaryTable(res.payload));
    } else {
      const div = document.createElement("div");
      div.style.height = "520px";
      body.appendChild(div);
      const layout = Object.assign({}, res.payload.layout, {
        autosize: true, height: null, width: null,
      });
      Plotly.newPlot(div, res.payload.data, layout, PLOT_CONFIG);
    }
    el("hint-modal").hidden = false;
    el("hint-close").focus();
  }

  /* The numeric view of a round. It doubles as the screen-reader alternative
   * to the charts, which are otherwise unusable without sight. */
  function summaryTable(summary) {
    const wrap = document.createElement("div");
    const rows = [
      ["Mean temp (°C)", summary.monthly.temp_mean_c],
      ["Min temp (°C)", summary.monthly.temp_min_c],
      ["Max temp (°C)", summary.monthly.temp_max_c],
      ["Mean RH (%)", summary.monthly.rh_mean_pct],
      ["Radiation (kWh/m²)", summary.monthly.radiation_kwh_m2],
      ["Mean wind (m/s)", summary.monthly.wind_mean_ms],
    ];
    if (summary.monthly.precip_mm) rows.push(["Precipitation (mm)", summary.monthly.precip_mm]);

    const table = document.createElement("table");
    table.className = "summary";
    table.innerHTML =
      "<caption class='sr-only'>Monthly climate summary</caption><thead><tr><th scope='col'>Measure</th>" +
      MONTHS.map((m) => `<th scope="col">${m}</th>`).join("") + "</tr></thead><tbody>" +
      rows.map(([label, values]) =>
        `<tr><th scope="row">${label}</th>` +
        values.map((v) => `<td>${v}</td>`).join("") + "</tr>").join("") +
      "</tbody>";

    const div = document.createElement("div");
    div.className = "table-wrap";
    div.appendChild(table);
    wrap.appendChild(div);

    const a = summary.annual;
    const annual = document.createElement("p");
    annual.className = "annual";
    annual.textContent =
      `Annual: mean ${a.temp_mean_c}°C, range ${a.temp_min_c}–${a.temp_max_c}°C ` +
      `(${a.temp_range_c}°C spread), mean RH ${a.rh_mean_pct}%, ` +
      `${a.radiation_kwh_m2} kWh/m² total radiation, ` +
      `${a.hdd18} heating and ${a.cdd18} cooling degree-days (base 18°C)` +
      (a.precip_mm ? `, ${a.precip_mm} mm precipitation.` : ".");
    wrap.appendChild(annual);
    return wrap;
  }

  /* --- round loop -------------------------------------------------------- */
  function renderAttempts() {
    const worth = Math.max(0, 1 - state.attemptPenalty * (state.attempt - 1));
    el("attempt-n").textContent = String(state.attempt);
    el("attempt-max").textContent = String(state.maxAttempts);
    el("attempt-mult").textContent = Math.round(worth * 100) + "%";
  }

  function showMiss(res) {
    el("miss").innerHTML =
      `Not there. The answer lies <span class="miss-dir">${res.direction}</span> of your pin.` +
      `<span class="miss-cost">Move the pin to guess again. ${res.attempts_left} ` +
      `${res.attempts_left === 1 ? "attempt" : "attempts"} left — the next guess is ` +
      `worth ${Math.round(res.attempt_multiplier * 100)}%.</span>`;
    el("miss").hidden = false;
  }

  async function startRound() {
    state.submitting = false;
    state.guess = null;
    state.figures.clear();
    state.roundNo += 1;
    clearMapAnswer();
    renderReadout();

    el("result").hidden = true;
    el("submit-guess").disabled = true;
    el("multiplier-box").hidden = true;
    el("multiplier").textContent = "×1.00";
    el("round-no").textContent = String(state.roundNo);
    el("chart-loading").hidden = false;
    el("koppen-select").value = "";
    el("miss").hidden = true;
    state.attempt = 1;
    renderAttempts();

    state.round = await api("/api/round", { session_id: state.sessionId });
    state.charts = state.round.charts;
    buildTabs();
    buildHints(state.round.hints);
    state.map.easeTo({ center: [0, 20], zoom: 0.6, duration: 400 });
    await showChart(state.charts[0].id);
  }

  async function submitGuess() {
    if (!state.guess || state.submitting) return;
    state.submitting = true;
    el("submit-guess").disabled = true;

    let res;
    try {
      res = await api("/api/guess", {
        session_id: state.sessionId,
        round_id: state.round.round_id,
        lat: state.guess.lat,
        lon: state.guess.lon,
        koppen: el("koppen-select").value || null,
      });
    } catch (err) {
      state.submitting = false;
      el("submit-guess").disabled = false;
      alert("Could not submit guess: " + err.message);
      return;
    }

    // A miss with attempts left keeps the round open: no answer comes back,
    // the pin stays put so the player can adjust it, and only the direction
    // and the new multiplier are revealed.
    if (res.result === "retry") {
      state.attempt = res.attempts_used + 1;
      state.submitting = false;
      // Left disabled on purpose. setGuess() and the marker's dragend re-arm
      // it, so the next attempt costs a deliberate move of the pin rather than
      // a second click landing on the same coordinates.
      el("submit-guess").disabled = true;
      renderAttempts();
      showMiss(res);
      return;
    }

    el("miss").hidden = true;
    state.totalScore = res.stats.total_score;
    el("total-score").textContent = String(state.totalScore);
    el("last-score").textContent = String(res.score);
    showAnswerOnMap(res.answer, state.guess);
    showResult(res);
  }

  function showResult(res) {
    const a = res.answer;
    const place = [a.city, a.state, a.country].filter(Boolean).join(", ");
    el("result-h").textContent = res.accepted
      ? `Got it — ${res.distance_km.toLocaleString()} km away`
      : `${res.distance_km.toLocaleString()} km away`;
    el("answer-line").textContent =
      place + (a.koppen ? ` — ${a.koppen}${a.koppen_description ? " · " + a.koppen_description : ""}` : "");
    el("result-score").textContent = String(res.score);

    const items = [
      ["Distance", `${res.distance_points} pts`],
      res.scored_climate
        ? ["Climate class match", `${res.climate_points} pts (${Math.round(res.climate_similarity * 100)}%)`]
        : ["Climate class", "not scored — distance took full weight"],
    ];
    if (res.hints_used > 0) {
      items.push([`Hints used (${res.hints_used})`, `×${res.hint_multiplier.toFixed(2)}`]);
    }
    if (res.attempts_used > 1) {
      items.push([`Attempts (${res.attempts_used})`, `×${res.attempt_multiplier.toFixed(2)}`]);
    }
    el("result-breakdown").innerHTML = items
      .map(([k, v]) => `<li><span>${k}</span><span>${v}</span></li>`).join("");

    el("result").hidden = false;
    el("next-round").focus();
  }

  /* --- boot -------------------------------------------------------------- */
  async function boot() {
    state.sessionId = sessionId();
    let meta;
    try {
      meta = await api("/api/meta");
    } catch (err) {
      el("boot-msg").textContent = "Could not reach the server: " + err.message;
      return;
    }

    if (!meta.locations) {
      el("boot-msg").textContent =
        "No locations built yet. Drop EPW files in epw_ingest/raw/ and run " +
        "`python -m epw_ingest.build_assets`.";
      return;
    }

    state.maxAttempts = meta.max_attempts ?? state.maxAttempts;
    state.attemptPenalty = meta.attempt_penalty ?? state.attemptPenalty;
    renderAttempts();

    const select = el("koppen-select");
    meta.koppen_options.forEach((opt) => {
      const o = document.createElement("option");
      o.value = opt.code;
      o.textContent = `${opt.code} — ${opt.label}`;
      select.appendChild(o);
    });

    el("boot-msg").textContent =
      `${meta.locations} locations loaded. Read the charts, drop a pin, name the climate.`;
    el("start-btn").hidden = false;
    el("start-btn").focus();

    el("start-btn").addEventListener("click", async () => {
      el("boot").hidden = true;
      el("game").hidden = false;
      initMap();
      await startRound();
    });
  }

  el("next-round").addEventListener("click", () => startRound());
  el("submit-guess").addEventListener("click", submitGuess);
  el("hint-close").addEventListener("click", () => { el("hint-modal").hidden = true; });
  el("set-coords").addEventListener("click", () => {
    const lat = parseFloat(el("lat-input").value);
    const lon = parseFloat(el("lon-input").value);
    if (Number.isFinite(lat) && Number.isFinite(lon)) {
      setGuess(lat, lon);
      state.map.easeTo({ center: [lon, lat], zoom: 3 });
    }
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !el("hint-modal").hidden) el("hint-modal").hidden = true;
  });

  boot();
})();
