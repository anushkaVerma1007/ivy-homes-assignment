"""
Ivy Homes data-pull script.

Run this LOCALLY (not in Claude's sandbox — solve.ivy.homes isn't reachable
from there). It pulls every retrievable record from /v1/listings, /v1/rentals,
/v1/projects and /v1/analytics/summary for your city, and writes them to JSON
files in the same folder. Also grabs /health once, just to see it.

Usage:
    pip install requests
    # fill in API_KEY and PASSWORD below
    python pull_data.py

Output files (all in ./data/):
    health.json
    login.json
    listings.json      -> list of every listing object
    rentals.json        -> list of every rental object
    projects.json        -> list of every project object
    analytics_summary.json
    meta.json           -> counts, pagination info, timing, any anomalies noticed while pulling
"""

import json
import os
import time
from datetime import datetime, timezone

import requests

BASE_URL = "https://solve.ivy.homes"

# ---- FILL THESE IN ----
API_KEY = "IVY26-45B2F83DE37A"     # your real key
LOGIN_EMAIL = "demo1@ivy.homes"     # any of the 3 demo accounts
LOGIN_PASSWORD = "c2d252ccce"
# ------------------------

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(OUT_DIR, exist_ok=True)

session = requests.Session()
# Documentation says the key goes as an `api_key` query param on every request,
# but /auth/login actually rejects that and wants an X-API-Key header instead
# (401 "missing X-API-Key header"). Sending it as a header on every request
# from the start, and keeping api_key as a query param too, so we can see
# whether the OTHER endpoints genuinely want the query param (as documented)
# or also secretly want the header. Don't assume either way — check meta.json.
session.headers.update({"X-API-Key": API_KEY})
meta = {
    "pulled_at_utc": datetime.now(timezone.utc).isoformat(),
    "notes": [
        "auth: /auth/login rejects the documented api_key query param and requires "
        "an X-API-Key header instead (401 'missing X-API-Key header'). Sending both "
        "header and query param on every request below to see which endpoints "
        "actually need which.",
    ],
}


def save(name, obj):
    path = os.path.join(OUT_DIR, name)
    with open(path, "w") as f:
        json.dump(obj, f, indent=2)
    print(f"  wrote {path}")


def note(msg):
    print(f"  [NOTE] {msg}")
    meta["notes"].append(msg)


def check_health():
    print("== /health ==")
    r = session.get(f"{BASE_URL}/health", timeout=15)
    print(f"  status={r.status_code}")
    try:
        body = r.json()
    except ValueError:
        body = r.text
    save("health.json", body)
    return body


def login():
    print("== POST /auth/login ==")
    r = session.post(
        f"{BASE_URL}/auth/login",
        json={"email": LOGIN_EMAIL, "password": LOGIN_PASSWORD},
        timeout=15,
    )
    print(f"  status={r.status_code}")
    body = r.json()
    save("login.json", body)
    print(f"  response body: {json.dumps(body, indent=2)}")
    if r.status_code != 200:
        raise SystemExit(f"Login failed: {body}")

    # Docs say the field is "token", but don't assume — try common alternates
    # and fail loudly with the actual body if none match, instead of a bare
    # KeyError, since a mismatched field name here is itself a finding.
    token = None
    token_field_used = None
    for candidate in ("token", "access_token", "auth_token", "jwt", "id_token"):
        if candidate in body:
            token = body[candidate]
            token_field_used = candidate
            break
    if token is None and isinstance(body.get("user"), dict):
        for candidate in ("token", "access_token"):
            if candidate in body["user"]:
                token = body["user"][candidate]
                token_field_used = f"user.{candidate}"
                break

    if token is None:
        note(f"/auth/login: 200 response body has NO recognizable token field. Full body keys: {list(body.keys())}")
        raise SystemExit(
            "Could not find a token field in the login response. "
            "Look at the printed response body above and tell Claude what the actual field is called."
        )

    if token_field_used != "token":
        note(f"/auth/login: token field is actually '{token_field_used}', not 'token' as documented")

    session.headers.update({"Authorization": f"Bearer {token}"})
    return body


def paginate_all(endpoint, limit=200, extra_params=None):
    """
    Pulls every page of a collection endpoint, following the documented
    {total, page, page_size, results} shape. Records everything odd it sees
    (page_size mismatches, total drifting between calls, duplicate ids
    across pages, non-200s) into meta['notes'] instead of silently coping,
    so we don't accidentally paper over a real discrepancy.
    """
    print(f"== GET {endpoint} ==")
    params = {"api_key": API_KEY, "page": 1, "limit": limit}
    if extra_params:
        params.update(extra_params)

    results = []
    seen_ids = set()
    page = 1
    total_reported = None
    id_field_candidates = ("listing_id", "project_id", "id")

    while True:
        params["page"] = page
        r = session.get(f"{BASE_URL}{endpoint}", params=params, timeout=30)
        if r.status_code != 200:
            note(f"{endpoint} page {page}: non-200 status {r.status_code}: {r.text[:300]}")
            break

        body = r.json()
        page_results = body.get("results", [])
        total = body.get("total")
        page_size = body.get("page_size")

        if total_reported is None:
            total_reported = total
            print(f"  reported total={total}")
        elif total != total_reported:
            note(f"{endpoint}: 'total' changed mid-pull, page {page}: was {total_reported}, now {total}")

        if page_size is not None and page_size != limit and len(page_results) == limit:
            note(f"{endpoint} page {page}: requested limit={limit} but page_size field says {page_size}")

        for rec in page_results:
            id_field = next((f for f in id_field_candidates if f in rec), None)
            if id_field:
                rid = rec[id_field]
                if rid in seen_ids:
                    note(f"{endpoint}: duplicate {id_field}={rid} seen across pages (page {page})")
                seen_ids.add(rid)

        results.extend(page_results)
        print(f"  page {page}: got {len(page_results)} records (running total {len(results)})")

        if not page_results:
            break
        if total is not None and len(results) >= total:
            break
        if len(page_results) < limit:
            # server returned a short page — should be the last one, but note it
            # in case there's still more (would mean total/pagination disagree)
            page += 1
            params["page"] = page
            r2 = session.get(f"{BASE_URL}{endpoint}", params=params, timeout=30)
            if r2.status_code == 200 and r2.json().get("results"):
                note(f"{endpoint}: short page at {page-1} was NOT the last page — pagination is odd")
                results.extend(r2.json().get("results", []))
                page += 1
                continue
            break

        page += 1
        time.sleep(0.05)  # be polite, we have 1200/min anyway

    print(f"  TOTAL pulled for {endpoint}: {len(results)} (server said total={total_reported})")
    if total_reported is not None and len(results) != total_reported:
        note(f"{endpoint}: pulled {len(results)} records but server's 'total' was {total_reported}")

    return results, total_reported


def diagnose_key_auth():
    """
    One-off check: does /v1/listings actually work with ONLY the documented
    api_key query param (no header), or does it also secretly need/prefer
    the X-API-Key header like /auth/login does? Doesn't touch session state.
    """
    print("== diagnosing api key auth on /v1/listings ==")
    r_query_only = requests.get(
        f"{BASE_URL}/v1/listings", params={"api_key": API_KEY, "page": 1, "limit": 1}, timeout=15
    )
    note(f"/v1/listings with ONLY query param api_key -> status {r_query_only.status_code}")

    r_header_only = requests.get(
        f"{BASE_URL}/v1/listings",
        params={"page": 1, "limit": 1},
        headers={"X-API-Key": API_KEY},
        timeout=15,
    )
    note(f"/v1/listings with ONLY X-API-Key header -> status {r_header_only.status_code}")


def main():
    check_health()
    diagnose_key_auth()
    login()

    listings, listings_total = paginate_all("/v1/listings")
    save("listings.json", listings)

    rentals, rentals_total = paginate_all("/v1/rentals")
    save("rentals.json", rentals)

    projects, projects_total = paginate_all("/v1/projects")
    save("projects.json", projects)

    print("== GET /v1/analytics/summary ==")
    r = session.get(f"{BASE_URL}/v1/analytics/summary", params={"api_key": API_KEY}, timeout=15)
    print(f"  status={r.status_code}")
    analytics = r.json() if r.status_code == 200 else {"error": r.status_code, "body": r.text}
    save("analytics_summary.json", analytics)

    meta.update({
        "listings_total_reported": listings_total,
        "listings_pulled": len(listings),
        "rentals_total_reported": rentals_total,
        "rentals_pulled": len(rentals),
        "projects_total_reported": projects_total,
        "projects_pulled": len(projects),
    })
    save("meta.json", meta)

    print("\nDone. Notes collected during the pull:")
    for n in meta["notes"]:
        print(" -", n)
    if not meta["notes"]:
        print(" (none — pagination behaved exactly as documented)")


if __name__ == "__main__":
    main()