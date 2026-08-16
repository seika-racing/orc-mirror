#!/usr/bin/env python3
"""ORC certificate mirror — publishes ORC polars as small static JSON files.

Fetches every ORC national authority's RMS file once per run (daily via
GitHub Actions), normalises the certificate polar into the shape the
Seika Racing app consumes, and writes:

  site/index.json(.gz)               global search index (one row per cert)
  site/certs/<RefNo>/<IssueDate>.json normalised polar + cert meta (never deleted)
  site/certs/<RefNo>/latest.json      copy of the newest revision
  site/meta.json                      run timestamp, per-country status, schema

Source: ORC Sailor Services public RMS endpoint
  https://data.orc.org/public/WPub.dll?action=DownRMS&CountryId=<CODE>&ext=json
Data (c) Offshore Racing Congress. Allowances are seconds per nautical
mile; boat speed in knots = 3600 / allowance.

Standard library only. Usage:
  python3 mirror.py --out site                     # all countries
  python3 mirror.py --out site --countries POR ESP # subset (dev)
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import os
import sys
import time
import urllib.error
import urllib.request

SCHEMA_VERSION = 1
BASE_URL = "https://data.orc.org/public/WPub.dll"
USER_AGENT = "seika-racing-orc-mirror/1.0 (+https://github.com/seika-racing/orc-mirror)"

# ORC member national authorities (IOC-style codes as used by WPub.dll).
# Codes that currently return no certificates are harmless (empty list).
COUNTRIES = [
    "ARG", "AUS", "AUT", "BEL", "BRA", "BUL", "CAN", "CHI", "CRO", "CYP",
    "CZE", "DEN", "ECU", "ESP", "EST", "FIN", "FRA", "GBR", "GER", "GRE",
    "HKG", "HUN", "IRL", "ISR", "ISV", "ITA", "JPN", "LAT", "LTU", "MLT",
    "MON", "NED", "NOR", "NZL", "PER", "POL", "POR", "ROU", "RSA", "RUS",
    "SLO", "SUI", "SWE", "TUR", "UKR", "URU", "USA",
]

# Reaching allowance keys -> true wind angle (degrees).
ANGLE_KEYS = [
    ("R52", 52), ("R60", 60), ("R75", 75), ("R90", 90),
    ("R110", 110), ("R120", 120), ("R135", 135), ("R150", 150),
]


def log(msg: str) -> None:
    print(f"[{dt.datetime.now(dt.timezone.utc).strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def fetch_country(code: str, retries: int = 3, timeout: int = 120) -> list[dict]:
    url = f"{BASE_URL}?action=DownRMS&CountryId={code}&ext=json"
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
            return parse_rms(raw)
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            last_err = e
            log(f"  {code}: attempt {attempt} failed: {e}")
            time.sleep(5 * attempt)
    raise RuntimeError(f"{code}: giving up after {retries} attempts: {last_err}")


def parse_rms(raw: bytes) -> list[dict]:
    """ORC serves UTF-8 with a BOM; the payload is {"rms": [...]}."""
    text = raw.decode("utf-8-sig")
    data = json.loads(text)
    rms = data.get("rms") if isinstance(data, dict) else data
    if not isinstance(rms, list):
        raise ValueError("unexpected RMS payload shape")
    return rms


# ---------------------------------------------------------------------------
# Normalise
# ---------------------------------------------------------------------------

def _kn(allowance_s_per_nm) -> float | None:
    try:
        v = float(allowance_s_per_nm)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    return round(3600.0 / v, 2)


def normalise(rec: dict) -> dict | None:
    """RMS record -> {'meta': {...}, 'polar': PolarModel-shaped dict} or None
    when the record has no usable allowances."""
    al = rec.get("Allowances") or {}
    wind_speeds = al.get("WindSpeeds")
    if not wind_speeds:
        return None
    n = len(wind_speeds)

    angles = []
    rows_by_angle = []
    for key, angle in ANGLE_KEYS:
        col = al.get(key)
        if not col or len(col) != n:
            continue
        angles.append(angle)
        rows_by_angle.append([_kn(x) for x in col])
    if not angles:
        return None

    # speedMatrix[windIndex][angleIndex]
    speed_matrix = [
        [rows_by_angle[a][w] for a in range(len(angles))] for w in range(n)
    ]
    if any(v is None for row in speed_matrix for v in row):
        return None

    beat = al.get("Beat") or []
    run = al.get("Run") or []
    beat_ang = al.get("BeatAngle") or []
    gybe_ang = al.get("GybeAngle") or []
    if not (len(beat) == len(run) == len(beat_ang) == len(gybe_ang) == n):
        return None

    polar = {
        "windSpeeds": [float(w) for w in wind_speeds],
        "angles": [float(a) for a in angles],
        "speedMatrix": speed_matrix,
        "beatAngles": [round(float(x), 1) for x in beat_ang],
        "beatVMG": [_kn(x) for x in beat],
        "gybeAngles": [round(float(x), 1) for x in gybe_ang],
        "runVMG": [_kn(x) for x in run],
    }
    if any(v is None for v in polar["beatVMG"] + polar["runVMG"]):
        return None

    meta = {
        "refNo": rec.get("RefNo"),
        "certNo": rec.get("CertNo"),
        "yachtName": rec.get("YachtName"),
        "sailNo": rec.get("SailNo"),
        "country": rec.get("NatAuth"),
        "class": rec.get("Class"),
        "builder": rec.get("Builder"),
        "designer": rec.get("Designer"),
        "club": rec.get("Club"),
        "certType": rec.get("C_Type"),
        "family": rec.get("Family"),
        "division": rec.get("Division"),
        "issueDate": rec.get("IssueDate"),
        "gph": rec.get("GPH"),
        "cdl": rec.get("CDL"),
        "loa": rec.get("LOA"),
    }
    return {"schema": SCHEMA_VERSION, "meta": meta, "polar": polar}


def index_row(meta: dict) -> dict:
    return {
        "refNo": meta["refNo"],
        "yachtName": meta.get("yachtName"),
        "sailNo": meta.get("sailNo"),
        "country": meta.get("country"),
        "class": meta.get("class"),
        "certNo": meta.get("certNo"),
        "issueDate": meta.get("issueDate"),
    }


# ---------------------------------------------------------------------------
# Publish
# ---------------------------------------------------------------------------

def _safe_name(s: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_.:" else "_" for ch in s)


def write_json(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def run(out_dir: str, countries: list[str], keep_existing_index: bool) -> int:
    started = dt.datetime.now(dt.timezone.utc)
    certs_dir = os.path.join(out_dir, "certs")
    os.makedirs(certs_dir, exist_ok=True)

    # Existing index rows are kept for countries that fail this run, so a
    # transient ORC outage never blanks the search index.
    existing: dict[str, dict] = {}
    idx_path = os.path.join(out_dir, "index.json")
    if keep_existing_index and os.path.exists(idx_path):
        try:
            for row in json.load(open(idx_path, encoding="utf-8")).get("boats", []):
                existing[row["refNo"]] = row
        except Exception as e:  # noqa: BLE001
            log(f"could not read existing index: {e}")

    status: dict[str, dict] = {}
    rows: dict[str, dict] = {}
    written = skipped = 0

    for code in countries:
        try:
            recs = fetch_country(code)
        except Exception as e:  # noqa: BLE001
            log(f"{code}: FAILED ({e}) — keeping previous index rows")
            status[code] = {"ok": False, "error": str(e)[:200]}
            for ref, row in existing.items():
                if row.get("country") == code:
                    rows[ref] = row
            continue

        n_ok = 0
        for rec in recs:
            norm = normalise(rec)
            if norm is None:
                skipped += 1
                continue
            meta = norm["meta"]
            ref = meta.get("refNo")
            issue = meta.get("issueDate")
            if not ref or not issue:
                skipped += 1
                continue
            rev_path = os.path.join(certs_dir, _safe_name(ref), _safe_name(issue) + ".json")
            if not os.path.exists(rev_path):
                write_json(rev_path, norm)
                written += 1
            latest_path = os.path.join(certs_dir, _safe_name(ref), "latest.json")
            write_json(latest_path, norm)
            rows[ref] = index_row(meta)
            n_ok += 1
        status[code] = {"ok": True, "certificates": n_ok}
        log(f"{code}: {n_ok} certificates")

    boats = sorted(rows.values(), key=lambda r: (r.get("country") or "", r.get("yachtName") or ""))
    index = {
        "schema": SCHEMA_VERSION,
        "generatedAt": started.isoformat(),
        "count": len(boats),
        "boats": boats,
    }
    write_json(idx_path, index)
    with gzip.open(idx_path + ".gz", "wb", compresslevel=9) as gz:
        gz.write(json.dumps(index, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    meta = {
        "schema": SCHEMA_VERSION,
        "generatedAt": started.isoformat(),
        "durationSeconds": round((dt.datetime.now(dt.timezone.utc) - started).total_seconds(), 1),
        "countries": status,
        "certificatesIndexed": len(boats),
        "revisionsWritten": written,
        "recordsSkipped": skipped,
        "source": "ORC Sailor Services (data.orc.org) — Data (c) Offshore Racing Congress",
    }
    write_json(os.path.join(out_dir, "meta.json"), meta)
    # Pages needs a .nojekyll so underscore/dot paths are served verbatim.
    open(os.path.join(out_dir, ".nojekyll"), "w").close()

    failed = [c for c, s in status.items() if not s["ok"]]
    log(f"done: {len(boats)} boats indexed, {written} new revisions, "
        f"{skipped} skipped, failed countries: {failed or 'none'}")
    # Fail the job only if every country failed (ORC down) — partial is fine.
    return 1 if failed and len(failed) == len(countries) else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="site", help="output directory (default: site)")
    ap.add_argument("--countries", nargs="*", default=None, help="subset of country codes")
    ap.add_argument("--fresh", action="store_true", help="ignore existing index rows on failure")
    args = ap.parse_args()
    return run(args.out, args.countries or COUNTRIES, keep_existing_index=not args.fresh)


if __name__ == "__main__":
    sys.exit(main())
