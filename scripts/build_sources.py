"""Render docs/SOURCES.md from data/sources.yaml (the site reads sources.json,
written by build-site).

    python scripts/build_sources.py
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ROLE = {"truth": "Ground truth", "baseline": "Reference forecasts", "metadata": "Metadata",
        "training": "Training & history"}


def main() -> None:
    src = yaml.safe_load((ROOT / "data" / "sources.yaml").read_text())["sources"]
    out = ["# Data sources and attribution", "",
           "PowderBench is built on open data. Every source below permits the use we make of it; "
           "please keep these attributions when you reuse PowderBench data. Generated from "
           "`data/sources.yaml` — edit that file, then run `python scripts/build_sources.py`.", ""]
    for role, title in ROLE.items():
        rows = [s for s in src if s["role"] == role]
        if not rows:
            continue
        out += [f"## {title}", "", "| Source | Provider | Licence | Attribution |", "|---|---|---|---|"]
        for s in rows:
            out.append(f"| [{s['name']}]({s['url']}) | {s['provider']} | {s['licence']} | {s['attribution']} |")
        out.append("")
    out += ["Takedown or correction requests: open an issue on GitHub — we act on them promptly.", ""]
    (ROOT / "docs" / "SOURCES.md").write_text("\n".join(out))
    print("wrote docs/SOURCES.md")


if __name__ == "__main__":
    main()
