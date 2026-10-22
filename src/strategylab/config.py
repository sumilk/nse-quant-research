"""Locate strategies and their files.

Each strategy lives in strategies/<id>/ with config.yaml (single source of truth),
HYPOTHESIS.md (pre-registration) and results/ (generated). Market data is shared
across strategies in data/raw.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
STRATEGIES = ROOT / "strategies"
RAW = ROOT / "data" / "raw"


def list_strategies() -> list[str]:
    return sorted(p.name for p in STRATEGIES.iterdir() if (p / "config.yaml").exists())


def strategy_dir(strategy_id: str) -> Path:
    d = STRATEGIES / strategy_id
    if not (d / "config.yaml").exists():
        raise SystemExit(f"Unknown strategy '{strategy_id}'. Available: {', '.join(list_strategies())}")
    return d


def results_dir(strategy_id: str) -> Path:
    return strategy_dir(strategy_id) / "results"


def load_config(strategy_id: str) -> dict:
    with open(strategy_dir(strategy_id) / "config.yaml") as f:
        cfg = yaml.safe_load(f)
    if cfg.get("strategy_id") != strategy_id:
        raise SystemExit(f"{strategy_id}/config.yaml declares strategy_id={cfg.get('strategy_id')!r}")
    return cfg
