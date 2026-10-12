import os
from pathlib import Path
from unittest.mock import patch

import manage


def test_manage_defaults_to_local_settings(monkeypatch):
    monkeypatch.delenv("DJANGO_SETTINGS_MODULE", raising=False)

    with patch("django.core.management.execute_from_command_line"):
        manage.main()

    assert os.environ["DJANGO_SETTINGS_MODULE"] == "config.settings.local"


def test_manage_preserves_explicit_settings_module(monkeypatch):
    monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "config.settings.test")

    with patch("django.core.management.execute_from_command_line"):
        manage.main()

    assert os.environ["DJANGO_SETTINGS_MODULE"] == "config.settings.test"


def test_makefile_exports_local_settings_module_by_default():
    makefile = Path("Makefile").read_text()

    assert "DJANGO_SETTINGS_MODULE ?= config.settings.local" in makefile
    assert "export DJANGO_SETTINGS_MODULE" in makefile
