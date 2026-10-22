from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def load_config() -> dict:
    with open(ROOT / "config" / "strategy.yaml") as f:
        return yaml.safe_load(f)
