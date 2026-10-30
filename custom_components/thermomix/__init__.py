"""The Thermomix integration.

Shows the live cooking state of a Thermomix (TM6/TM7) in Home Assistant by
receiving the same remote-monitoring pushes the Cookidoo app gets.
"""

from __future__ import annotations

import logging

from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant

from .helpers import cookidoo_from_config_entry
from .monitor import ThermomixConfigEntry, ThermomixMonitor

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.EVENT,
    Platform.SENSOR,
]

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ThermomixConfigEntry) -> bool:
    """Set up Thermomix from a config entry."""
    cookidoo = await cookidoo_from_config_entry(hass, entry)
    monitor = ThermomixMonitor(hass, entry, cookidoo)
    await monitor.async_setup()
    entry.runtime_data = monitor

    async def _async_on_hass_stop(_event: Event) -> None:
        await monitor.async_stop()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_on_hass_stop)
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ThermomixConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    await entry.runtime_data.async_stop()
    return unloaded
