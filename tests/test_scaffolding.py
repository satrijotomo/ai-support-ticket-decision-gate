import importlib
import json
import tomllib
from pathlib import Path

import azure.durable_functions as df
import pytest
from pydantic import ValidationError

from app.config import AppSettings


ROOT = Path(__file__).parents[1]


def test_required_phase_one_paths_exist() -> None:
    required_paths = {
        "function_app.py",
        "host.json",
        "local.settings.json.example",
        "requirements.txt",
        "pyproject.toml",
        "docker-compose.yml",
        ".gitignore",
        ".funcignore",
        "app/__init__.py",
        "app/config.py",
        "ui/.gitkeep",
        "data/.gitkeep",
        "sql/.gitkeep",
        "tests/__init__.py",
    }

    missing = sorted(path for path in required_paths if not (ROOT / path).is_file())

    assert missing == []


def test_function_app_exports_one_durable_app() -> None:
    module = importlib.import_module("function_app")
    durable_apps = [value for value in vars(module).values() if isinstance(value, df.DFApp)]

    assert durable_apps == [module.app]


def test_json_and_toml_configuration_are_valid() -> None:
    host = json.loads((ROOT / "host.json").read_text(encoding="utf-8"))
    local_settings = json.loads(
        (ROOT / "local.settings.json.example").read_text(encoding="utf-8")
    )
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert host["version"] == "2.0"
    assert local_settings["Values"]["AzureWebJobsStorage"] == "UseDevelopmentStorage=true"
    assert local_settings["Values"]["FUNCTIONS_WORKER_RUNTIME"] == "python"
    assert project["project"]["requires-python"] == ">=3.11,<3.12"


def test_settings_load_valid_environment_values() -> None:
    settings = AppSettings.from_environment(
        {
            "FOUNDRY_PROJECT_ENDPOINT": "https://example.services.ai.azure.com/api/projects/demo",
            "FOUNDRY_MOCK_MODE": "false",
            "APPROVAL_TIMEOUT_MINUTES": "60",
            "DATABASE_PATH": "./data/test.db",
        }
    )

    assert str(settings.foundry_project_endpoint).startswith("https://example.")
    assert settings.foundry_mock_mode is False
    assert settings.approval_timeout_minutes == 60
    assert settings.database_path == "./data/test.db"


def test_settings_allow_phase_one_without_foundry_resources() -> None:
    settings = AppSettings.from_environment({})

    assert settings.foundry_project_endpoint is None
    assert settings.foundry_mock_mode is True
    assert settings.triage_agent_name == "support-triage-agent"


@pytest.mark.parametrize("timeout", ["0", "-1", "not-a-number"])
def test_settings_reject_invalid_approval_timeout(timeout: str) -> None:
    with pytest.raises(ValidationError):
        AppSettings.from_environment({"APPROVAL_TIMEOUT_MINUTES": timeout})