/* Static backend.
 *
 * GitHub Pages runs no server, so app/main.py's four endpoints run here
 * instead, against the files emitted by `epw_ingest.build_static`. The
 * response shapes are identical to the FastAPI ones on purpose: the UI layer
 * in app.js does not know which one it is talking to.
 *
 * What is lost relative to the server: the answer key is reachable by the
 * browser, so it is reachable by a player with devtools. It is kept in
 * per-location files fetched only after a guess is committed, so cheating is
 * a deliberate act rather than something you trip over in the Network tab.
 *
 * Paths here are relative on purpose. A project Pages site is served from
 * /<repo>/, not /, so a leading slash would break every fetch.
 */
(() => {
  "use strict";

  let INDEX = null;                 // assets/index.json, loaded once
  const ROUNDS = new Map();         // round_id -> in-flight round state
  const ANSWERS = new Map();        // location id -> answer, cached per session
  const HISTORY_KEY = "climate-history";

  const json = async (url) => {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`${res.status} ${res.statusText} — ${url}`);
    return res.json();
  };

  /* --- persisted history ------------------------------------------------ */
  /* Stands in for the `rounds` table. Per-browser rather than per-session-id,
     which is what localStorage can honestly offer. */
  function loadHistory() {
    try { return JSON.parse(localStorage.getItem(HISTORY_KEY)) || []; }
    catch (_) { return []; }
  }

  function saveHistory(rows) {
    try { localStorage.setItem(HISTORY_KEY, JSON.stringify(rows.slice(0, 200))); }
    catch (_) { /* private mode: history is simply not kept */ }
  }

  function stats(rows) {
    const n = rows.length;
    const sum = (f) => rows.reduce((a, r) => a + (f(r) || 0), 0);
    return {
      rounds: n,
      total_score: sum((r) => r.score),
      average_distance_km: n ? Math.round((sum((r) => r.distance_km) / n) * 10) / 10 : 0,
      best_score: n ? Math.max(...rows.map((r) => r.score || 0)) : 0,
    };
  }

  /* --- geometry and scoring, ported from app/scoring.py ------------------ */
  const EARTH_RADIUS_KM = 6371.0088;
  const rad = (d) => (d * Math.PI) / 180;

  function haversineKm(lat1, lon1, lat2, lon2) {
    const p1 = rad(lat1), p2 = rad(lat2);
    const dp = p2 - p1, dl = rad(lon2 - lon1);
    const a = Math.sin(dp / 2) ** 2 +
              Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
    return 2 * EARTH_RADIUS_KM * Math.asin(Math.sqrt(a));
  }

  /* Eight compass points, and deliberately no distance: a direction narrows
     the search, a direction plus a magnitude solves it from three guesses. */
  function bearingTo(lat1, lon1, lat2, lon2) {
    const p1 = rad(lat1), p2 = rad(lat2), dl = rad(lon2 - lon1);
    const y = Math.sin(dl) * Math.cos(p2);
    const x = Math.cos(p1) * Math.sin(p2) - Math.sin(p1) * Math.cos(p2) * Math.cos(dl);
    const deg = ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360;
    return INDEX.compass_points[Math.floor(((deg + 22.5) % 360) / 45)];
  }

  /* Graded rather than binary, so Cfa vs Cfb (Atlanta vs London) still beats
     Cfa vs BWh (Atlanta vs Dubai). Mirrors koppen.similarity(). */
  function koppenSimilarity(a, b) {
    if (!a || !b) return 0;
    if (a === b) return 1;
    if (a.slice(0, 2) === b.slice(0, 2)) return 0.7;
    if (a[0] === b[0]) return 0.4;
    const pair = [a[0], b[0]].sort().join("");
    if (pair === "CD" || pair === "AC") return 0.15;
    return 0;
  }

  function attemptMultiplier(attemptsUsed) {
    const retries = Math.max(0, attemptsUsed - 1);
    return Math.max(0, 1 - INDEX.attempt_penalty * retries);
  }

  function hintMultiplier(hintsUsed) {
    return Math.max(INDEX.min_hint_multiplier,
                    1 - INDEX.hint_penalty * Math.max(0, hintsUsed));
  }

  function scoreRound(guess, answer, guessKoppen, hintsUsed, attemptsUsed) {
    const distance = haversineKm(guess.lat, guess.lon, answer.lat, answer.lon);
    const distanceFraction = Math.exp(-distance / INDEX.distance_scale_km);

    // The climate half only applies when both classes are known. When it does
    // not, distance takes the full weight rather than the round being
    // silently capped at 70%.
    const scoredClimate = Boolean(guessKoppen && answer.koppen);
    const climateFraction = scoredClimate
      ? koppenSimilarity(guessKoppen, answer.koppen) : 0;
    const distW = scoredClimate ? INDEX.distance_weight : 1;
    const climW = scoredClimate ? INDEX.climate_weight : 0;

    // Hints and retries are independent axes, so they compound.
    const hintMult = hintMultiplier(hintsUsed);
    const attemptMult = attemptMultiplier(attemptsUsed);
    const mult = hintMult * attemptMult;

    const distancePoints = INDEX.max_score * distW * distanceFraction * mult;
    const climatePoints = INDEX.max_score * climW * climateFraction * mult;

    return {
      score: Math.round(distancePoints + climatePoints),
      distance_km: Math.round(distance * 10) / 10,
      distance_points: Math.round(distancePoints),
      climate_points: Math.round(climatePoints),
      climate_similarity: Math.round(climateFraction * 100) / 100,
      hint_multiplier: Math.round(hintMult * 100) / 100,
      hints_used: hintsUsed,
      scored_climate: scoredClimate,
      attempt_multiplier: Math.round(attemptMult * 100) / 100,
      attempts_used: attemptsUsed,
    };
  }

  /* --- location pool ---------------------------------------------------- */
  function pickLocation() {
    const recent = new Set(
      loadHistory().slice(0, INDEX.recent_memory).map((r) => r.location_id)
    );
    const fresh = INDEX.locations.filter((l) => !recent.has(l.id));
    // Falls back to the full pool once the player has seen everything, which
    // matters a lot at 30 locations.
    const pool = fresh.length ? fresh : INDEX.locations;
    return pool[Math.floor(Math.random() * pool.length)];
  }

  async function answerFor(locationId) {
    if (!ANSWERS.has(locationId)) {
      ANSWERS.set(locationId, await json(`assets/answers/${locationId}.json`));
    }
    return ANSWERS.get(locationId);
  }

  /* --- the four endpoints ----------------------------------------------- */
  const HINT_CHARTS = () => INDEX.charts.filter((c) => c.tier !== "core");

  async function meta() {
    if (!INDEX) INDEX = await json("assets/index.json");
    return {
      locations: INDEX.locations.length,
      max_score: INDEX.max_score,
      max_attempts: INDEX.max_attempts,
      attempt_penalty: INDEX.attempt_penalty,
      accept_radius_km: INDEX.accept_radius_km,
      charts: INDEX.charts,
      koppen_options: INDEX.koppen_options,
    };
  }

  function newRound() {
    const location = pickLocation();
    const roundId = (crypto.randomUUID && crypto.randomUUID()) ||
                    String(Date.now() + Math.random());
    ROUNDS.set(roundId, {
      locationId: location.id, hintsShown: [], attempts: 0, finished: false,
    });

    const available = new Set(location.charts);
    return {
      round_id: roundId,
      charts: INDEX.charts
        .filter((c) => c.tier === "core" && available.has(c.id))
        .map((c) => ({
          id: c.id, label: c.label, blurb: c.blurb,
          url: `assets/charts/${location.id}/${c.id}.json`,
        })),
      hints: HINT_CHARTS()
        .filter((c) => available.has(c.id))
        .map((c) => ({ id: c.id, label: c.label, blurb: c.blurb,
                       cost: INDEX.hint_penalty }))
        .concat([{
          id: "summary",
          label: "Monthly data table",
          blurb: "The same information as the charts, as numbers.",
          cost: INDEX.hint_penalty,
        }]),
    };
  }

  async function revealHint(body) {
    const rnd = ROUNDS.get(body.round_id);
    if (!rnd || rnd.finished) throw new Error("round not found or already played");

    const payload = await json(`assets/hints/${rnd.locationId}/${body.hint}.json`);
    // Charge per distinct hint, so a double-click does not drain the score.
    if (!rnd.hintsShown.includes(body.hint)) rnd.hintsShown.push(body.hint);

    const spec = INDEX.charts.find((c) => c.id === body.hint);
    return {
      hint: body.hint,
      kind: body.hint === "summary" ? "summary" : "figure",
      label: spec ? spec.label : "Monthly data table",
      payload,
      hints_used: rnd.hintsShown.length,
      multiplier: hintMultiplier(rnd.hintsShown.length),
    };
  }

  async function submitGuess(body) {
    const rnd = ROUNDS.get(body.round_id);
    if (!rnd || rnd.finished) throw new Error("round not found or already played");

    const answer = await answerFor(rnd.locationId);
    rnd.attempts += 1;
    const distance = haversineKm(body.lat, body.lon, answer.lat, answer.lon);

    // A miss with attempts left keeps the round open and reveals only a
    // compass direction.
    if (distance > INDEX.accept_radius_km && rnd.attempts < INDEX.max_attempts) {
      return {
        result: "retry",
        accepted: false,
        direction: bearingTo(body.lat, body.lon, answer.lat, answer.lon),
        attempts_used: rnd.attempts,
        attempts_left: INDEX.max_attempts - rnd.attempts,
        attempt_multiplier: attemptMultiplier(rnd.attempts + 1),
      };
    }

    const breakdown = scoreRound(
      body, answer, body.koppen, rnd.hintsShown.length, rnd.attempts
    );
    rnd.finished = true;

    const rows = loadHistory();
    rows.unshift({
      location_id: rnd.locationId,
      score: breakdown.score,
      distance_km: breakdown.distance_km,
      finished_at: Date.now() / 1000,
      city: answer.city, country: answer.country, koppen: answer.koppen,
    });
    saveHistory(rows);

    return {
      result: "final",
      accepted: distance <= INDEX.accept_radius_km,
      attempts_left: INDEX.max_attempts - rnd.attempts,
      ...breakdown,
      answer,
      stats: stats(rows),
    };
  }

  /* Same call signature as the fetch wrapper it replaces, so app.js is
     unchanged apart from which function it calls. */
  window.climateApi = async function climateApi(path, body) {
    if (path === "/api/meta") return meta();
    if (!INDEX) await meta();
    if (path === "/api/round") return newRound();
    if (path === "/api/hint") return revealHint(body);
    if (path === "/api/guess") return submitGuess(body);
    if (path.startsWith("/api/history")) {
      const rows = loadHistory();
      return { stats: stats(rows), rounds: rows.slice(0, 10) };
    }
    throw new Error(`unknown call: ${path}`);
  };
})();
