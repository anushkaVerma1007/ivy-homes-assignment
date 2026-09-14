"""
RUN THIS NOW against your already-pulled data/ folder.
    cd scripts
    python answer_questions.py

Prints all 10 answers ready to paste into submission.json.
Heuristics for Q2, Q4, Q9, Q10 are best-effort given time pressure — read the
printed reasoning, sanity check the counts aren't wildly off, adjust the
thresholds inline if something looks clearly wrong, and MOVE ON. A shipped
approximate answer beats a perfect one you never submitted.
"""
import json
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

def load(name):
    with open(os.path.join(DATA, name)) as f:
        return json.load(f)

listings = load("listings.json")
rentals = load("rentals.json")
projects = load("projects.json")

# dedupe everything by id (in case an earlier pull had pagination repeats)
def dedupe(records, id_field):
    seen = {}
    for r in records:
        seen[r.get(id_field)] = r  # last write wins, fine for our purposes
    return list(seen.values())

listings = dedupe(listings, "listing_id")
rentals = dedupe(rentals, "listing_id")
projects = dedupe(projects, "project_id")

print(f"listings: {len(listings)}  rentals: {len(rentals)}  projects: {len(projects)}")
print()

answers = {}

# Q1 total_listing_records
answers["total_listing_records"] = len(listings)

# Q2 unique_properties — dedupe by (apartment_name, locality, carpet_area, floor) as a proxy
# for "same physical property listed more than once"
def prop_key(l):
    return (
        (l.get("apartment_name") or "").strip().lower(),
        (l.get("locality") or "").strip().lower(),
        l.get("floor"),
        l.get("carpet_area"),
        l.get("bedroom"),
    )
unique_props = {prop_key(l) for l in listings}
answers["unique_properties"] = len(unique_props)

# Q3 active_listings
answers["active_listings"] = sum(1 for l in listings if l.get("is_live") is True)

# Q4 corrupt_listing_ids — physically impossible records
corrupt = []
for l in listings:
    reasons = []
    floor, total_floors = l.get("floor"), l.get("total_floors")
    carpet, sba = l.get("carpet_area"), l.get("super_built_up_area")
    price, bedroom, bathroom = l.get("price"), l.get("bedroom"), l.get("bathroom")
    lat, lon = l.get("latitude"), l.get("longitude")

    if floor is not None and total_floors is not None and floor > total_floors:
        reasons.append("floor>total_floors")
    if carpet is not None and sba is not None and carpet > sba:
        reasons.append("carpet_area>super_built_up_area")
    if price is not None and price <= 0:
        reasons.append("price<=0")
    if carpet is not None and carpet <= 0:
        reasons.append("carpet_area<=0")
    if bedroom is not None and bedroom <= 0:
        reasons.append("bedroom<=0")
    if bathroom is not None and bedroom is not None and bathroom > bedroom + 3:
        reasons.append("bathroom wildly exceeds bedroom")
    if lat is not None and lon is not None and not (17.5 <= lat <= 19.5 and 73.0 <= lon <= 75.0):
        reasons.append("lat/lon outside Pune region")

    if reasons:
        corrupt.append((l["listing_id"], reasons))

print(f"Q4 candidates ({len(corrupt)}):")
for lid, reasons in corrupt[:30]:
    print(f"  {lid}: {reasons}")
answers["corrupt_listing_ids"] = sorted([lid for lid, _ in corrupt])

# Q5 total_monthly_rent in Baner
baner_rentals = [r for r in rentals if (r.get("locality") or "").strip().lower() == "baner"]
answers["total_monthly_rent"] = sum(r.get("price", 0) for r in baner_rentals)
print(f"\nQ5: {len(baner_rentals)} rentals in Baner, total monthly rent = {answers['total_monthly_rent']}")

# Q9 fake_listing_ids — heuristic: contact number reused across an unusually high
# number of DIFFERENT listings/apartments (bait accounts), or near-duplicate
# description text reused verbatim across many different apartment_names
contact_counts = defaultdict(list)
for l in listings:
    c = l.get("posted_by_contact")
    if c:
        contact_counts[c].append(l["listing_id"])

FAKE_CONTACT_THRESHOLD = 5  # tune if this looks wrong for your data
fake_ids = set()
for contact, ids in contact_counts.items():
    if len(ids) >= FAKE_CONTACT_THRESHOLD:
        fake_ids.update(ids)

print(f"\nQ9 candidates via repeated contact number (threshold={FAKE_CONTACT_THRESHOLD}): {len(fake_ids)}")
for contact, ids in sorted(contact_counts.items(), key=lambda x: -len(x[1]))[:10]:
    if len(ids) >= FAKE_CONTACT_THRESHOLD:
        print(f"  {contact}: {len(ids)} listings -> {ids[:5]}...")
answers["fake_listing_ids"] = sorted(fake_ids)

# Q6 avg_price_per_sqft_2bhk — is_live true, bedroom==2, excluding Q4 and Q9 ids
excluded = set(answers["corrupt_listing_ids"]) | set(answers["fake_listing_ids"])
eligible = [
    l for l in listings
    if l.get("is_live") is True
    and l.get("bedroom") == 2
    and l["listing_id"] not in excluded
    and l.get("carpet_area")
]
if eligible:
    avg_psf = sum(l["price"] / l["carpet_area"] for l in eligible) / len(eligible)
else:
    avg_psf = 0.0
answers["avg_price_per_sqft_2bhk"] = round(avg_psf, 2)
print(f"\nQ6: {len(eligible)} eligible 2BHK live listings, avg price/sqft = {answers['avg_price_per_sqft_2bhk']}")

# Q7 costliest_project
if projects:
    top = max(projects, key=lambda p: p.get("price_max", 0))
    answers["costliest_project"] = {"project_id": top["project_id"], "price_max_inr": top["price_max"]}
else:
    answers["costliest_project"] = {"project_id": "", "price_max_inr": 0}
print(f"\nQ7: {answers['costliest_project']}")

# Q8 listings_last_7_days — REFERENCE = 2026-09-10T00:00:00+05:30
REFERENCE = datetime(2026, 9, 10, 0, 0, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))
WINDOW_START = REFERENCE - timedelta(days=7)

def parse_ts(s):
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None

count_7d = 0
for l in listings:
    dt = parse_ts(l.get("posted_at"))
    if dt and WINDOW_START <= dt < REFERENCE:
        count_7d += 1
answers["listings_last_7_days"] = count_7d
print(f"\nQ8: window [{WINDOW_START.isoformat()}, {REFERENCE.isoformat()}) -> {count_7d} listings")

# Q10 projects_with_wrong_listing_count
listings_per_project = defaultdict(int)
for l in listings:
    pid = l.get("project_id")
    if pid:
        listings_per_project[pid] += 1

wrong = 0
mismatches = []
for p in projects:
    pid = p.get("project_id")
    reported = p.get("total_listings")
    actual = listings_per_project.get(pid, 0)
    if reported != actual:
        wrong += 1
        mismatches.append((pid, reported, actual))
answers["projects_with_wrong_listing_count"] = wrong
print(f"\nQ10: {wrong} projects with mismatched counts (showing up to 10):")
for pid, reported, actual in mismatches[:10]:
    print(f"  {pid}: reported={reported} actual={actual}")

print("\n\n================ PASTE THIS INTO submission.json['answers'] ================\n")
print(json.dumps(answers, indent=2))