import sys

import pytest

from quant_studio import QuantStudioError
from quant_studio.derivative_datasets import command
from quant_studio.free_derivative_datasets import collection_request, form
from quant_studio.research_web import editor_body
from quant_studio.settings import use_settings
from quant_studio.setup import empty_profile
from quant_studio.templates import load_template, render_template


def request(**kw):
    return collection_request(
        **(
            dict(
                name="Cboe",
                template="global-futures-free-sources",
                provider="cboe-vix",
                selection="2025-02-19",
                start="2025-01-08",
                end="2025-01-10",
                rights_note="personal research",
            )
            | kw
        )
    )


def test_fixed_command_and_frozen_request(tmp_path):
    profile = empty_profile()
    profile["python_by_repo"]["quant-futures-global"] = sys.executable
    req = request()
    with use_settings(profile):
        argv = command(req, tmp_path, tmp_path / "snapshot")
        assert argv[0] == sys.executable
        assert "free-fetch" in argv and "--selection" in argv
        assert not any("http" in value for value in argv)
        with pytest.raises(QuantStudioError, match="未知"):
            command(req | {"url": "https://evil.test"}, tmp_path, tmp_path / "snapshot")


@pytest.mark.parametrize(
    "kw",
    [
        {"provider": "evil"},
        {"selection": "../../x"},
        {"end": "2025-05-01"},
        {"tick": "NaN"},
        {"initial_margin": "1"},
        {"initial_margin": "1", "maintenance_margin": "2"},
        {"provider": "deribit"},
        {"source_mode": "snapshot"},
    ],
)
def test_bad_requests_never_enter_queue(kw):
    with pytest.raises(QuantStudioError):
        request(**kw)


def test_new_templates_preserve_original_defaults_and_fill_selected_contract():
    old = render_template(load_template("options-research")).config
    assert old["mode"] == "replay"
    fresh = render_template(load_template("options-free-sources")).config
    assert fresh["mode"] == "analysis"
    selected = {
        "id": "a" * 32,
        "name": "NSE sample",
        "path": "C:/input",
        "start": "2025-01-08",
        "end": "2025-01-10",
        "suggested_config": {"mode": "analysis", "legs": "NSE:NIFTY25JAN23500PE=1"},
    }
    page = editor_body(
        "options-free-sources",
        "csrf",
        datasets=[selected],
        selected_dataset=selected["id"],
    )
    assert "NSE:NIFTY25JAN23500PE=1" in page
    assert "DEMO:M-C100=1" not in page
    assert 'name="cfg:/legs"' in page
    assert "free-derivatives" in form("token")
