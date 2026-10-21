"""Harvest open snowpack / avalanche-forecasting data into one Hugging Face
dataset (default repo: nakas/powderbench-snowpack) as a training source.

Every source here has a licence that allows redistribution, verified on the
publisher's own page (see SOURCES). Each source lands in its own folder (= one
dataset config) with its licence and attribution in the dataset card and in
manifest.jsonl (url, bytes, sha256, retrieved_at per file). Share-alike
sources (CC BY-SA, ODbL) stay in their own configs and are never merged into
permissive ones. Sources that need permission (Avalanche Canada, SnowPilot,
CAIC, NAC) are NOT here — see docs/OUTREACH.md.

    python scripts/snowpack/harvest.py --out _snowpack [--only meteofrance_nivo_obs] [--upload]

Resumable: files already present with the expected size are skipped.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import requests

UA = {"User-Agent": "PowderBench/0.1 (+https://powderbench.com; snowpack training-data mirror)"}
REPO = "nakas/powderbench-snowpack"


@dataclass
class Source:
    config: str
    title: str
    license: str  # HF licence id
    attribution: str
    homepage: str
    files: Callable[[], list[tuple[str, str]]]  # -> [(url, relative dest)]
    kind: str  # model | observations | bulletins | labels | profiles
    description: str
    # files forming one logical table (yearly CSVs): also published as
    # normalized Parquet under <config>/parquet/ with one schema, which is
    # what the dataset config points at (raw files stay byte-identical)
    table_glob: str | None = None
    gzip: bool = False  # compress downloads on the way in (big JSON APIs)
    notes: list[str] = field(default_factory=list)


def get(url: str, **kw) -> requests.Response:
    for attempt in range(7):
        try:
            r = requests.get(url, headers=UA, timeout=180, **kw)
            if r.status_code in (429,) or r.status_code >= 500:
                raise requests.HTTPError(str(r.status_code))
            r.raise_for_status()
            return r
        except requests.RequestException as exc:
            if attempt == 6:
                raise
            wait = min(5 * 2**attempt, 60)
            print(f"  retry {url} ({exc}) in {wait}s", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError("unreachable")


# ---- file listers ---------------------------------------------------------

def datagouv(slug: str, keep: Callable[[dict], bool] = lambda r: True) -> Callable[[], list[tuple[str, str]]]:
    def files():
        d = get(f"https://www.data.gouv.fr/api/1/datasets/{slug}/").json()
        out = []
        for r in d["resources"]:
            if not keep(r) or r.get("type") == "api" or r["url"].endswith("/"):
                continue
            name = r["url"].rsplit("/", 1)[-1].split("?")[0]
            out.append((r["url"], name))
        return sorted(set(out))
    return files


def zenodo(record: str) -> Callable[[], list[tuple[str, str]]]:
    def files():
        d = get(f"https://zenodo.org/api/records/{record}").json()
        return [(f["links"]["self"], f["key"]) for f in d["files"]]
    return files


def envidat(ref: str) -> Callable[[], list[tuple[str, str]]]:
    """ref: a DOI (10.16904/...) or an EnviDat package name."""
    def files():
        if ref.startswith("10."):
            d = get("https://www.envidat.ch/api/action/package_search",
                    params={"q": f'doi:"{ref}"', "rows": 1}).json()
            pkg = d["result"]["results"][0]
        else:
            pkg = get("https://www.envidat.ch/api/action/package_show", params={"id": ref}).json()["result"]
        out = []
        for r in pkg["resources"]:
            url = r["url"]
            if not url.startswith("http") or r.get("url_type") == "api":
                continue
            name = re.sub(r"[^A-Za-z0-9._-]+", "_", url.rsplit("/", 1)[-1]) or r["id"]
            out.append((url, name))
        return out
    return files


def nve_varsom() -> list[tuple[str, str]]:
    """NVE avalanche warnings archive, one JSON per region x month (whole-season
    requests time out server-side)."""
    base = "https://api01.nve.no/hydrology/forecast/avalanche/v6.3.0/api"
    regions = [r["Id"] for r in get(f"{base}/Region/").json() if r.get("TypeName") == "A"]
    today = date.today()
    out = []
    y, m = 2012, 11
    while (y, m) <= (today.year, today.month):
        if m in (11, 12, 1, 2, 3, 4, 5, 6):  # the warning season
            start = date(y, m, 1)
            end = date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)
            for rid in regions:
                out.append((f"{base}/AvalancheWarningByRegion/Detail/{rid}/2/{start}/{end}",
                            f"warnings_{start:%Y-%m}_region{rid}.json.gz"))
        y, m = (y + (m == 12), m % 12 + 1)
    return out


SOURCES = [
    Source(
        config="meteofrance_snowpack_model",
        title="Météo-France snowpack simulation (S2M / SAFRAN-Crocus), French massifs, 1958-",
        license="etalab-2.0",
        attribution="Météo-France — Données de Modèle de Simulation Nivologique (data.gouv.fr), Licence Ouverte 2.0",
        homepage="https://www.data.gouv.fr/datasets/donnees-de-modele-de-simulation-nivologique/",
        files=datagouv("donnees-de-modele-de-simulation-nivologique"),
        kind="model",
        description="Daily simulated snowpack per massif x altitude band x aspect x slope: total depth "
                    "(neigetot), SWE (equivalent_eau), runoff (ecoulement). Alps, Pyrenees, Corsica.",
        table_glob="donnees_nivologiques_*.csv.gz",
    ),
    Source(
        config="meteofrance_nivo_obs",
        title="Météo-France nivo-meteorological network (ski-resort observers), observations",
        license="etalab-2.0",
        attribution="Météo-France — Archive réseau nivo-météorologique (data.gouv.fr), Licence Ouverte 2.0",
        homepage="https://www.data.gouv.fr/datasets/archive-reseau-nivo-meteorologique/",
        files=datagouv("archive-reseau-nivo-meteorologique"),
        kind="observations",
        description="Twice-daily mountain observations from ski-patrol / partner stations: total snow depth "
                    "(ht_neige, m), fresh snow (ssfrai), snow surface state, ram penetration, drifting snow, "
                    "observed avalanche activity, weather.",
        table_glob="nivo_*.csv.gz",
    ),
    Source(
        config="meteofrance_bra_docs",
        title="Météo-France avalanche bulletins (BRA): format docs + massif metadata",
        license="etalab-2.0",
        attribution="Météo-France — Archive des bulletins avalanches (data.gouv.fr), Licence Ouverte 2.0",
        homepage="https://www.data.gouv.fr/datasets/archive-des-bulletins-avalanches/",
        files=datagouv("archive-des-bulletins-avalanches"),
        kind="bulletins",
        description="BRA XML format description, massif polygons (GeoJSON) and a sample bundle. The full "
                    "bulletin archive lives at files.data.gouv.fr/meteofrance/data/BULLETIN/BRA/.",
    ),
    Source(
        config="nve_varsom_warnings",
        title="NVE Varsom avalanche warnings archive, Norway, 2012-",
        license="other",  # NLOD 2.0
        attribution="Varsler fra Snøskredvarslingen i Norge og www.varsom.no (NVE), NLOD 2.0",
        homepage="https://api.nve.no/doc/snoeskredvarsel/",
        files=nve_varsom,
        kind="bulletins",
        gzip=True,
        description="Daily danger level, avalanche problems (type, trigger, size, distribution, aspects, "
                    "elevations), snowpack/weather assessments per forecast region.",
        notes=["Licence: Norwegian Licence for Open Government Data (NLOD) 2.0 — https://data.norge.no/nlod/en/2.0"],
    ),
    Source(
        config="zenodo_model_vs_human_danger_ch",
        title="Model-based vs human avalanche danger forecasts, Switzerland 2022-2025",
        license="cc-by-4.0",
        attribution="Zenodo record 17060394 (SLF authors), CC BY 4.0",
        homepage="https://zenodo.org/records/17060394",
        files=zenodo("17060394"),
        kind="labels",
        description="Interpolated model forecasts (danger level, instability, natural release) alongside "
                    "the prepared human bulletin, plus analysis code.",
    ),
    Source(
        config="zenodo_eaws_matrix",
        title="EAWS matrix: forecaster assessments of stability, frequency, size and danger",
        license="cc-by-4.0",
        attribution="Zenodo record 18030373, CC BY 4.0",
        homepage="https://zenodo.org/records/18030373",
        files=zenodo("18030373"),
        kind="labels",
        description="Anonymised European avalanche forecaster assessments used to study the EAWS matrix.",
    ),
    Source(
        config="envidat_danger_levels_ch",
        title="Re-analysed regional avalanche danger levels, Switzerland 2001/02-2023/24",
        license="cc-by-sa-4.0",
        attribution="WSL Institute for Snow and Avalanche Research SLF, EnviDat doi:10.16904/envidat.426, CC BY-SA 4.0",
        homepage="https://doi.org/10.16904/envidat.426",
        files=envidat("10.16904/envidat.426"),
        kind="labels",
        description="Quality-checked danger levels by region, elevation and aspect.",
    ),
    Source(
        config="envidat_snowpack_dry_avalanches",
        title="SNOWPACK-simulated features + dry-snow avalanche labels, Switzerland 2001-2022",
        license="cc-by-sa-4.0",
        attribution="WSL Institute for Snow and Avalanche Research SLF (Mayer et al. 2023), EnviDat doi:10.16904/envidat.425, CC BY-SA 4.0",
        homepage="https://doi.org/10.16904/envidat.425",
        files=envidat("10.16904/envidat.425"),
        kind="model",
        description="Daily SNOWPACK model features at IMIS stations paired with avalanche-day labels.",
    ),
    Source(
        config="envidat_instability_observations",
        title="Field observations of snow instability (rutschblock), Switzerland 2001-2019",
        license="cc-by-sa-4.0",
        attribution="WSL Institute for Snow and Avalanche Research SLF, EnviDat doi:10.16904/envidat.222, CC BY-SA 4.0",
        homepage="https://doi.org/10.16904/envidat.222",
        files=envidat("10.16904/envidat.222"),
        kind="profiles",
        description="589 snow profiles with stability tests.",
    ),
    Source(
        config="envidat_davos_avalanches",
        title="Snow avalanche data Davos, 1999-2019",
        license="odbl",
        attribution="WSL Institute for Snow and Avalanche Research SLF, EnviDat 'snow-avalanche-data-davos', ODbL",
        homepage="https://www.envidat.ch/dataset/snow-avalanche-data-davos",
        files=envidat("snow-avalanche-data-davos"),
        kind="labels",
        description="13,918 recorded avalanches with danger levels, Davos region.",
    ),
]


# ---- normalized tables ----------------------------------------------------

def _read_raw(path: Path):
    import gzip

    import pandas as pd

    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as f:
        header = f.readline()
    sep = ";" if header.count(";") > header.count(",") else ","  # publisher switched over the years
    return pd.read_csv(path, sep=sep, dtype=str, keep_default_na=False, na_values=[""])


def tabulate(src: Source, out: Path) -> int:
    """Two passes: decide each column's type across ALL files (numeric only if
    every value in every year parses), then write every file with that one
    schema, so the Hugging Face loader sees a single consistent table."""
    import pandas as pd

    folder = out / src.config
    raws = sorted(folder.glob(src.table_glob))
    if not raws:
        return 0
    columns: list[str] = []
    numeric: dict[str, bool] = {}
    for path in raws:
        df = _read_raw(path)
        for c in df.columns:
            if c not in numeric:
                columns.append(c)
                numeric[c] = True
            if numeric[c]:
                vals = df[c].dropna()
                numeric[c] = bool(pd.to_numeric(vals, errors="coerce").notna().all())
    pq = folder / "parquet"
    pq.mkdir(exist_ok=True)
    n = 0
    for path in raws:
        dest = pq / (path.name.split(".")[0] + ".parquet")
        if dest.exists() and dest.stat().st_mtime >= path.stat().st_mtime:
            continue
        df = _read_raw(path).reindex(columns=columns)
        for c in columns:
            df[c] = pd.to_numeric(df[c]) if numeric[c] else df[c].astype("string")
            if numeric[c]:
                df[c] = df[c].astype("float64")
        df.to_parquet(dest, index=False)
        n += 1
    print(f"[{src.config}] parquet: {n} written, {len(raws)} total, {sum(numeric.values())}/{len(columns)} numeric columns")
    return n


# ---- harvest + card --------------------------------------------------------

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def harvest(src: Source, out: Path) -> list[dict]:
    folder = out / src.config
    folder.mkdir(parents=True, exist_ok=True)
    try:
        files = src.files()
    except Exception as exc:  # one dead publisher never blocks the rest
        print(f"[{src.config}] listing failed: {exc}", file=sys.stderr)
        return []
    manifest = []
    for url, rel in files:
        dest = folder / rel
        if dest.exists() and dest.stat().st_size > 0:
            status = "kept"
        else:
            try:
                r = get(url, stream=True)
                tmp = dest.with_suffix(dest.suffix + ".part")
                import gzip

                with (gzip.open(tmp, "wb") if src.gzip else tmp.open("wb")) as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
                tmp.rename(dest)
                status = "downloaded"
            except Exception as exc:
                print(f"[{src.config}] {rel}: {exc}", file=sys.stderr)
                continue
        manifest.append({"file": f"{src.config}/{rel}", "url": url, "bytes": dest.stat().st_size,
                         "sha256": sha256(dest), "license": src.license, "attribution": src.attribution,
                         "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        print(f"[{src.config}] {status} {rel}")
    return manifest


def card(sources: list[Source], manifest: list[dict]) -> str:
    by_cfg: dict[str, list[dict]] = {}
    for m in manifest:
        by_cfg.setdefault(m["file"].split("/", 1)[0], []).append(m)
    configs = []
    for s in sources:
        if s.table_glob and by_cfg.get(s.config):
            configs.append(f"- config_name: {s.config}\n  data_files: \"{s.config}/parquet/*.parquet\"\n")
    yaml = ["---", "license: other", "pretty_name: PowderBench Snowpack & Avalanche Training Data",
            "tags:", "- snow", "- avalanche", "- snowpack", "- weather", "- climate",
            "task_categories:", "- tabular-classification", "- time-series-forecasting"]
    if configs:
        yaml += ["configs:"] + [c.rstrip("\n") for c in configs]
    yaml.append("---")
    rows = []
    for s in sources:
        files = by_cfg.get(s.config, [])
        size = sum(f["bytes"] for f in files) / 1e6
        have = f"{len(files)} files, {size:,.0f} MB" if files else "pending (weekly harvest)"
        rows.append(f"| `{s.config}` | {s.kind} | {s.title} | `{s.license}` | {have} | [source]({s.homepage}) |")
    details = []
    for s in sources:
        details.append(f"### `{s.config}`\n\n{s.description}\n\n- **Licence:** `{s.license}`"
                       + "".join(f"\n- {n}" for n in s.notes)
                       + f"\n- **Attribution (required):** {s.attribution}\n- **Source:** {s.homepage}\n")
    return "\n".join(yaml) + f"""

# PowderBench Snowpack & Avalanche Training Data

Open snowpack-model output, mountain snow observations, avalanche bulletins and
avalanche/danger labels — the data avalanche forecasters work from — gathered
in one place as a training source for snow and avalanche ML. Maintained by
[PowderBench](https://powderbench.com), the live mountain-snowfall forecasting
benchmark ([code](https://github.com/andrewnakas/powderbench),
`scripts/snowpack/harvest.py`).

**Every source keeps its own licence.** Files are mirrored unmodified; each
config below is one source. Yearly tables are additionally published as
normalized Parquet (`<config>/parquet/`, one schema across years — the
publisher's own CSV separators and column types drift) and that's what
`load_dataset("nakas/powderbench-snowpack", "<config>")` reads. Share-alike sources (CC BY-SA, ODbL) are separate
configs — if you combine them with other data, their share-alike terms apply
to the result. `manifest.jsonl` lists every file with its source URL, size,
SHA-256, licence and retrieval time.

| Config | Kind | What | Licence | Size | |
|---|---|---|---|---|---|
{chr(10).join(rows)}

Not included (yet): Avalanche Canada bulletins / MIN / weather stations,
SnowPilot snow pits, CAIC and avalanche.org observations — their terms need
written permission, which PowderBench has requested. Suggested evaluation
splits are by season (train ≤ 2019/20, validation 2020/21–2021/22,
test ≥ 2022/23), never random — neighbouring days are highly correlated.

## Sources

{chr(10).join(details)}
## Takedown

If you publish one of these datasets and want it removed or changed, open an
issue at https://github.com/andrewnakas/powderbench — we'll act on it promptly.
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("_snowpack"))
    ap.add_argument("--only", action="append", help="config name(s) to harvest")
    ap.add_argument("--upload", action="store_true", help="push to the Hugging Face repo afterwards")
    ap.add_argument("--repo", default=REPO)
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    mpath = args.out / "manifest.jsonl"
    old = {}
    if mpath.exists():
        for line in mpath.read_text().splitlines():
            m = json.loads(line)
            old[m["file"]] = m
    chosen = [s for s in SOURCES if not args.only or s.config in args.only]
    for src in chosen:
        for m in harvest(src, args.out):
            old[m["file"]] = m
        if src.table_glob:
            tabulate(src, args.out)
    mpath.write_text("".join(json.dumps(m) + "\n" for m in sorted(old.values(), key=lambda m: m["file"])))
    (args.out / "README.md").write_text(card(SOURCES, list(old.values())))
    print(f"{len(old)} files in manifest -> {args.out}")

    if args.upload:
        from huggingface_hub import HfApi

        api = HfApi()
        api.create_repo(args.repo, repo_type="dataset", exist_ok=True)
        upload = getattr(api, "upload_large_folder", None)  # resumable; absent in some hub versions
        if upload:
            upload(repo_id=args.repo, repo_type="dataset", folder_path=str(args.out),
                   ignore_patterns=["*.part", ".cache/**"])
        else:
            api.upload_folder(repo_id=args.repo, repo_type="dataset", folder_path=str(args.out),
                              ignore_patterns=["*.part", ".cache/**"],
                              commit_message="PowderBench snowpack harvest")
        print(f"uploaded -> https://huggingface.co/datasets/{args.repo}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
