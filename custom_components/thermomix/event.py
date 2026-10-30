"""Event platform for the Thermomix integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity, EventEntityDescription
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import EVENT_TYPES
from .entity import ThermomixEntity
from .monitor import ThermomixConfigEntry, ThermomixDeviceState

PARALLEL_UPDATES = 0

EVENT_DESCRIPTION = EventEntityDescription(
    key="cooking",
    translation_key="cooking",
    event_types=EVENT_TYPES,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ThermomixConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one event entity per appliance."""
    monitor = entry.runtime_data

    @callback
    def _add_device(device: ThermomixDeviceState) -> None:
        async_add_entities([ThermomixCookingEvent(monitor, device, EVENT_DESCRIPTION)])

    for device in monitor.devices.values():
        _add_device(device)
    entry.async_on_unload(
        async_dispatcher_connect(hass, monitor.signal_new_device, _add_device)
    )


class ThermomixCookingEvent(ThermomixEntity, EventEntity):
    """Fires for every cooking transition (started, step changed, done, ...)."""

    entity_description: EventEntityDescription

    async def async_added_to_hass(self) -> None:
        """Subscribe to the appliance's events."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                self.monitor.signal_event(self.device.device_id),
                self._handle_event,
            )
        )

    @callback
    def _handle_event(self, event_type: str, data: dict[str, Any]) -> None:
        self._trigger_event(event_type, data)
        self.async_write_ha_state()
