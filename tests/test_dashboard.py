"""Headless smoke test: every dashboard view renders without an exception."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from strategylab.config import list_strategies, results_dir

APP = str(Path(__file__).resolve().parents[1] / "src" / "strategylab" / "app" / "dashboard.py")
RESEARCHED = [s for s in list_strategies() if (results_dir(s) / "artifacts" / "dev" / "stats.json").exists()]


def test_compare_view_renders():
    at = AppTest.from_file(APP, default_timeout=120).run()
    assert not at.exception, [e.message for e in at.exception]


@pytest.mark.skipif(not RESEARCHED, reason="no strategy has research artifacts yet")
@pytest.mark.parametrize("strategy_id", RESEARCHED)
def test_strategy_views_render(strategy_id):
    at = AppTest.from_file(APP, default_timeout=120).run()
    at.sidebar.selectbox[0].set_value(strategy_id).run()
    assert not at.exception, [e.message for e in at.exception]
    assert len(at.tabs) == 9
    at.sidebar.toggle[0].set_value(True).run()  # after-tax view
    assert not at.exception, [e.message for e in at.exception]
