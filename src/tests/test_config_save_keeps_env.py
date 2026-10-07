"""Saving config in the UI must not drop env overrides from the runtime config.

api_put_config used to copy the DB-only config onto the runtime config, so env
values (LLM key/model/base URL, LLM_EXTRA_BODY) vanished in the web process,
which also runs the jobs, until the next restart.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.config_store import hydrate_runtime_config_inplace
from app.routes.config_routes import config_bp
from app.runtime_config import config as runtime_config
from tests.test_env_var_authority import _create_default_settings


@pytest.fixture
def restore_runtime_config():
    saved = {k: getattr(runtime_config, k) for k in type(runtime_config).model_fields}
    yield
    for key, value in saved.items():
        setattr(runtime_config, key, value)


def test_saving_config_keeps_env_overrides(
    app: Any, monkeypatch: Any, restore_runtime_config: None
) -> None:
    monkeypatch.setenv("LLM_API_KEY", "env-key-123")
    monkeypatch.setenv("LLM_MODEL", "openai/env-model")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://env-llm.invalid/v1")
    monkeypatch.setenv(
        "LLM_EXTRA_BODY", '{"chat_template_kwargs": {"enable_thinking": false}}'
    )
    app.register_blueprint(config_bp)
    with app.app_context():
        _create_default_settings()
        hydrate_runtime_config_inplace()
        assert runtime_config.llm_model == "openai/env-model"

        resp = app.test_client().put("/api/config", json={"output": {"fade_ms": 1234}})
        assert resp.status_code == 200, resp.data

        # The saved DB value is live...
        assert runtime_config.output.fade_ms == 1234
        # ...and the env overrides survived the save.
        assert runtime_config.llm_api_key == "env-key-123"
        assert runtime_config.llm_model == "openai/env-model"
        assert runtime_config.openai_base_url == "http://env-llm.invalid/v1"
        assert runtime_config.llm_extra_body == {
            "chat_template_kwargs": {"enable_thinking": False}
        }
