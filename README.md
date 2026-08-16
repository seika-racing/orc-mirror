# orc-mirror

Daily static mirror of ORC certificate polars, published to GitHub Pages for the
[Seika Racing](https://github.com/seika-racing/sailrace_app) app.

Data © [Offshore Racing Congress](https://www.orc.org) — sourced from the public
ORC Sailor Services RMS endpoint (`data.orc.org`). This mirror exists so that
thousands of phones fetch ~1 KB per boat from a CDN instead of ~6 MB per country
from ORC's server, and so that every certificate revision stays available for
analysing past races against the polar valid on race day.

## Published files

| Path | Contents |
|------|----------|
| `index.json` / `index.json.gz` | `{schema, generatedAt, count, boats:[{refNo, yachtName, sailNo, country, class, certNo, issueDate}]}` |
| `certs/<RefNo>/<IssueDate>.json` | `{schema, meta:{...}, polar:{windSpeeds, angles, speedMatrix[wind][angle], beatAngles, beatVMG, gybeAngles, runVMG}}` — never deleted |
| `certs/<RefNo>/latest.json` | newest revision |
| `meta.json` | run timestamp, per-country status, counts |

Speeds are **knots** (ORC allowances are s/NM; `kn = 3600 / allowance`).
`speedMatrix[i][j]` is boat speed at `windSpeeds[i]` and `angles[j]`.

Base URL: `https://seika-racing.github.io/orc-mirror/`

## Run locally

```
python3 mirror.py --out site --countries POR ESP
python3 -m unittest -q tests/test_mirror.py
```

## Schedule

`.github/workflows/mirror.yml` runs daily at 03:00 UTC and on manual dispatch,
appending to the `gh-pages` branch (`keep_files: true`). A country that fails keeps
its previous index rows; the job only fails if every country fails.
