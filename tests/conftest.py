"""Fixtures for the Thermomix tests."""

from __future__ import annotations

from collections.abc import Generator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.thermomix.const import CONF_MOBILE_APP_ID, DOMAIN

DEVICE_ID = "22e920b2d6184cec6c854cd005d6aa8fb851d7e783478b50f361ac8d1ab97bfe"

# A real remote-monitoring push payload (Firebase data message; values are strings).
PUSH_RUNNING: dict[str, Any] = {
    "deviceId": DEVICE_ID,
    "id": "986954277",
    "cookingActivityId": "210c27aa-de31-4565-aad1-4f10e2c79deb",
    "state": "running",
    "recipeId": "r54743",
    "recipeType": "vorwerk",
    "leadingText": "Purè di patate",
    "trailingText": "5/9",
    "primaryInfo": "---",
    "secondaryInfo": "95",
    "separator": "/",
    "isTimeEstimated": "false",
    "iconId": "manual-values",
    "completedDate": "2026-08-28T13:37:48Z",
    "staleDate": "1787924895000",
    "dismissalDate": "1787924388000",
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable loading custom integrations in all tests."""


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """A configured Thermomix account."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Thermomix",
        unique_id="user-1",
        data={
            "email": "test@example.com",
            "password": "secret",
            "country": "CZ",
            "language": "cs-CZ",
            "token": {},
            CONF_MOBILE_APP_ID: "11111111-2222-3333-4444-555555555555",
        },
    )


@pytest.fixture
def mock_cookidoo() -> Generator[MagicMock]:
    """Patch the Cookidoo client with a logged-in fake."""
    with (
        patch("custom_components.thermomix.helpers.Cookidoo") as cls,
        patch(
            "custom_components.thermomix.helpers.get_localization_options",
            AsyncMock(return_value=[SimpleNamespace(language="cs-CZ")]),
        ),
    ):
        api = cls.return_value
        api.auth_data = None
        api.login = AsyncMock()
        api.get_user_info = AsyncMock(return_value=SimpleNamespace(id="user-1"))
        api.get_devices = AsyncMock(return_value=[SimpleNamespace(type="TM7")])
        api.get_monitored_device_ids = AsyncMock(return_value=[DEVICE_ID])
        api.register_push_token = AsyncMock()
        api.unregister_push_token = AsyncMock()
        yield api


@pytest.fixture
def mock_receiver() -> Generator[MagicMock]:
    """Patch the push receiver so no Firebase connection is made."""
    with patch("custom_components.thermomix.monitor.ThermomixPushReceiver") as cls:
        receiver = cls.return_value
        receiver.start = AsyncMock()
        receiver.stop = AsyncMock()
        receiver.is_connected = True
        receiver.credentials = {"fcm": {"registration": {"token": "fcm-token"}}}
        receiver.token = "fcm-token"
        yield cls
