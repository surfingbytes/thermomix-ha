"""Binary sensor platform for the Thermomix integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import ThermomixEntity
from .monitor import ThermomixConfigEntry, ThermomixDeviceState, ThermomixMonitor

PARALLEL_UPDATES = 0


@dataclass(kw_only=True, frozen=True)
class ThermomixBinarySensorDescription(BinarySensorEntityDescription):
    """Describes a Thermomix binary sensor."""

    is_on_fn: Callable[[ThermomixMonitor, ThermomixDeviceState], bool]
    refresh_on_tick: bool = False


BINARY_SENSORS: tuple[ThermomixBinarySensorDescription, ...] = (
    ThermomixBinarySensorDescription(
        key="cooking",
        translation_key="cooking",
        device_class=BinarySensorDeviceClass.RUNNING,
        is_on_fn=lambda _monitor, device: device.is_active,
    ),
    ThermomixBinarySensorDescription(
        key="connected",
        translation_key="connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=lambda monitor, _device: monitor.is_connected,
        refresh_on_tick=True,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ThermomixConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up binary sensors for every known appliance, and for ones seen later."""
    monitor = entry.runtime_data

    @callback
    def _add_device(device: ThermomixDeviceState) -> None:
        async_add_entities(
            ThermomixBinarySensor(monitor, device, description)
            for description in BINARY_SENSORS
        )

    for device in monitor.devices.values():
        _add_device(device)
    entry.async_on_unload(
        async_dispatcher_connect(hass, monitor.signal_new_device, _add_device)
    )


class ThermomixBinarySensor(ThermomixEntity, BinarySensorEntity):
    """A binary sensor derived from the appliance cook state."""

    entity_description: ThermomixBinarySensorDescription

    def __init__(
        self,
        monitor: ThermomixMonitor,
        device: ThermomixDeviceState,
        description: ThermomixBinarySensorDescription,
    ) -> None:
        """Initialise the binary sensor."""
        super().__init__(monitor, device, description)
        self._refresh_on_tick = description.refresh_on_tick

    @property
    def is_on(self) -> bool:
        """Return the state."""
        return self.entity_description.is_on_fn(self.monitor, self.device)
