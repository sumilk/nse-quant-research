"""Strategy models. A model is a module that defines:

    SIGNAL_TEST                "cross_sectional" (rank IC across assets) or "time_series"
                               (does an asset's own signal predict its own excess return?)
    Spec                       frozen dataclass with a `.name` property; one per trial
    grid(cfg) -> list[Spec]    every trial in the pre-registered grid (the primary must be in it)
    primary(cfg) -> Spec       the pre-registered specification
    signal(levels, returns, spec, cfg) -> DataFrame
                               sessions x assets score, reading only data <= each session
    signal_key(spec) -> str    groups trials that share a signal (signal tests run once per key)
    weights(signal_row, eligible_row, spec, cfg) -> Series
                               target weights from one decision date's score row

Register a new model in MODELS below and point a strategy's config `model:` at it.
"""

from importlib import import_module
from types import ModuleType

MODELS = {
    "xsec_momentum": "strategylab.models.xsec_momentum",
    "ts_trend": "strategylab.models.ts_trend",
}


def load_model(name: str) -> ModuleType:
    if name not in MODELS:
        raise SystemExit(f"Unknown model '{name}'. Registered: {', '.join(MODELS)}")
    return import_module(MODELS[name])
