"""Diagnostics for the Thermomix integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, CONF_TOKEN
from homeassistant.core import HomeAssistant

from .const import CONF_KNOWN_DEVICES, CONF_MOBILE_APP_ID
from .monitor import ThermomixConfigEntry

TO_REDACT = {CONF_EMAIL, CONF_PASSWORD, CONF_TOKEN, CONF_MOBILE_APP_ID, CONF_KNOWN_DEVICES}
RAW_TO_REDACT = {"deviceId"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ThermomixConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    monitor = entry.runtime_data
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "connected": monitor.is_connected,
        "push_token_registered": monitor.push_token is not None,
        "models": monitor.models,
        "devices": [
            {
                "state": device.state,
                "last_update": device.last_update,
                "dismissal_at": device.dismissal_at,
                "data": async_redact_data(device.as_event_data(), RAW_TO_REDACT),
                "raw": async_redact_data(device.raw, RAW_TO_REDACT),
            }
            for device in monitor.devices.values()
        ],
    }
