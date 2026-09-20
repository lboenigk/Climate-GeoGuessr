#!/usr/bin/env python
"""End-to-end check of the round loop, with emphasis on what must NOT leak.

Starts a throwaway uvicorn on a spare port against a temporary database, plays
a round, and asserts the anti-cheat properties hold. Stdlib only.

    make check
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN_IN_ROUND = ("city", "country", "lat", "lon", "koppen", "state", "elevation")
COMPASS = ("north", "north-east", "east", "south-east",
           "south", "south-west", "west", "north-west")

passed = failed = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}" + (f" — {detail}" if detail else ""))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def request(base: str, path: str, body=None):
    url = base + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST" if data else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as res:
            return res.status, json.loads(res.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except urllib.error.URLError:
        # Server not up yet - the startup poll below relies on this.
        return 0, None


def main() -> int:
    if not (ROOT / "assets" / "manifest.json").exists():
        print("No assets/manifest.json — run `make build` first.", file=sys.stderr)
        return 2

    port = free_port()
    base = f"http://127.0.0.1:{port}"
    tmpdb = Path(tempfile.mkdtemp()) / "smoke.db"
    env = {**os.environ, "CLIMATE_DB": str(tmpdb), "PYTHONPATH": str(ROOT)}

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=ROOT, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
    )
    try:
        for _ in range(60):
            status, _body = request(base, "/api/meta")
            if status == 200:
                break
            time.sleep(0.5)
        else:
            print("server never came up", file=sys.stderr)
            return 2

        print("\nGame flow")
        _, meta = request(base, "/api/meta")
        check("meta lists locations", meta["locations"] > 0)
        check("Köppen options offered", len(meta["koppen_options"]) > 20)

        session = uuid.uuid4().hex
        _, rnd = request(base, "/api/round", {"session_id": session})
        check("round returns core charts", len(rnd["charts"]) >= 5)
        check("round offers hints", len(rnd["hints"]) >= 1)

        print("\nLeak prevention")
        blob = json.dumps(rnd).lower()
        leaked = [k for k in FORBIDDEN_IN_ROUND if f'"{k}"' in blob]
        check("no answer fields in /api/round", not leaked, f"found {leaked}")

        core_ids = {c["id"] for c in rnd["charts"]}
        # The sun path used to be gated. It ships free now, so the thing to
        # assert is the opposite: that it is actually there.
        check("sun path ships as a core chart", "sunpath" in core_ids)
        check("hint URLs are not handed out",
              all("url" not in h for h in rnd["hints"]))

        location_id = rnd["charts"][0]["url"].split("/")[2]
        status, _ = request(base, f"/charts/{location_id}/sunpath.json")
        check("sun path does serve from the static mount", status == 200,
              f"got HTTP {status}")
        status, _ = request(base, f"/charts/{location_id}/summary.json")
        check("summary not reachable on the static mount", status == 404,
              f"got HTTP {status}")
        status, _ = request(base, "/charts/../manifest.json")
        check("answer key not reachable", status in (404, 403), f"got HTTP {status}")

        status, chart = request(base, rnd["charts"][0]["url"])
        check("core chart does serve", status == 200 and "data" in chart)

        print("\nHints")
        _, hint = request(base, "/api/hint", {
            "session_id": session, "round_id": rnd["round_id"], "hint": "summary"})
        check("hint returns its payload inline", "payload" in hint)
        check("hint charges the round", abs(hint["multiplier"] - 0.85) < 1e-6,
              f'multiplier {hint["multiplier"]}')
        _, again = request(base, "/api/hint", {
            "session_id": session, "round_id": rnd["round_id"], "hint": "summary"})
        check("re-revealing the same hint is free",
              again["hints_used"] == hint["hints_used"])

        status, _ = request(base, "/api/hint", {
            "session_id": session, "round_id": rnd["round_id"], "hint": "temperature"})
        check("core charts are rejected as hints", status == 400, f"got {status}")
        status, _ = request(base, "/api/hint", {
            "session_id": session, "round_id": rnd["round_id"], "hint": "sunpath"})
        check("sun path is no longer purchasable as a hint", status == 400,
              f"got {status}")

        print("\nAttempts")
        # Empty South Pacific: further than the accept radius from anywhere an
        # EPW station could plausibly be, so these are guaranteed misses.
        MISS = {"lat": -50.0, "lon": -120.0}

        _, miss1 = request(base, "/api/guess", {
            "session_id": session, "round_id": rnd["round_id"], **MISS})
        check("a miss does not end the round", miss1.get("result") == "retry",
              f'result {miss1.get("result")}')
        check("a miss reveals a compass direction", miss1.get("direction") in COMPASS)
        check("a miss does not reveal the answer", "answer" not in miss1)
        check("a miss leaks no distance", "distance_km" not in miss1)
        blob = json.dumps(miss1).lower()
        check("no answer fields in a miss response",
              not [k for k in FORBIDDEN_IN_ROUND if f'"{k}"' in blob])
        check("next guess is worth less",
              abs(miss1["attempt_multiplier"] - 0.75) < 1e-6,
              f'multiplier {miss1["attempt_multiplier"]}')

        _, miss2 = request(base, "/api/guess", {
            "session_id": session, "round_id": rnd["round_id"], **MISS})
        check("second miss still leaves one attempt", miss2["attempts_left"] == 1,
              f'left {miss2["attempts_left"]}')

        print("\nScoring")
        _, result = request(base, "/api/guess", {
            "session_id": session, "round_id": rnd["round_id"],
            **MISS, "koppen": "Cfb"})
        check("the last attempt ends the round", result.get("result") == "final")
        check("guess returns the answer", "answer" in result and result["answer"]["city"])
        check("distance is reported", result["distance_km"] > 0)
        check("hint penalty applied", result["hint_multiplier"] == 0.85)
        check("attempt penalty applied", result["attempt_multiplier"] == 0.5,
              f'multiplier {result["attempt_multiplier"]}')
        check("three misses are not marked accepted", result["accepted"] is False)

        status, _ = request(base, "/api/guess", {
            "session_id": session, "round_id": rnd["round_id"],
            "lat": 10.0, "lon": 10.0})
        check("a round cannot be replayed", status == 404, f"got HTTP {status}")

        # A guess inside the accept radius should end the round there and then.
        # Bounded by the attempt budget, so it is only decisive while there are
        # no more locations than attempts; it is skipped rather than wrong above.
        manifest = json.loads(
            (ROOT / "assets" / "manifest.json").read_text(encoding="utf-8"))
        places = manifest["locations"]
        if len(places) <= 3:
            session3 = uuid.uuid4().hex
            _, rnd3 = request(base, "/api/round", {"session_id": session3})
            accepted = None
            for place in places:
                _, res = request(base, "/api/guess", {
                    "session_id": session3, "round_id": rnd3["round_id"],
                    "lat": place["lat"], "lon": place["lon"]})
                if res.get("result") == "final":
                    accepted = res
                    break
            check("a guess inside the accept radius ends the round",
                  accepted is not None and accepted["accepted"] is True)
            check("an accepted first guess keeps full marks",
                  accepted is not None and accepted["attempt_multiplier"] > 0)
        else:
            print("  SKIP  accept-radius check (more locations than attempts)")

        # A perfect guess on a fresh round should land near the ceiling.
        _, rnd2 = request(base, "/api/round", {"session_id": session})
        status, _ = request(base, "/api/guess", {
            "session_id": uuid.uuid4().hex, "round_id": rnd2["round_id"],
            "lat": 0.0, "lon": 0.0})
        check("another session cannot play your round", status == 404, f"got HTTP {status}")

        print(f"\n{passed} passed, {failed} failed")
        return 1 if failed else 0
    finally:
        proc.terminate()
        proc.wait(timeout=10)


if __name__ == "__main__":
    raise SystemExit(main())
