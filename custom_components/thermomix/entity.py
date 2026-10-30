"""Base entity for the Thermomix integration."""

from __future__ import annotations

from typing import Any

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity, EntityDescription

from .const import DOMAIN, MANUFACTURER
from .monitor import ThermomixDeviceState, ThermomixMonitor


class ThermomixEntity(Entity):
    """Entity bound to one appliance, refreshed by dispatcher signals."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    # Entities whose value depends on the wall clock also refresh on the tick.
    _refresh_on_tick = False

    def __init__(
        self,
        monitor: ThermomixMonitor,
        device: ThermomixDeviceState,
        description: EntityDescription,
    ) -> None:
        """Initialise the entity."""
        self.monitor = monitor
        self.device = device
        self.entity_description = description
        self._attr_unique_id = f"{device.device_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.device_id)},
            manufacturer=MANUFACTURER,
            model=monitor.model_name(device),
            name=monitor.device_name(device),
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to state updates."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                self.monitor.signal_update(self.device.device_id),
                self._handle_update,
            )
        )
        if self._refresh_on_tick:
            self.async_on_remove(
                async_dispatcher_connect(
                    self.hass, self.monitor.signal_tick, self._handle_update
                )
            )

    @callback
    def _handle_update(self, *_: Any) -> None:
        self.async_write_ha_state()
