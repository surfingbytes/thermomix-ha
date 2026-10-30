"""Sensor platform for the Thermomix integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import EntityCategory, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from .const import COOK_STATES
from .entity import ThermomixEntity
from .monitor import ThermomixConfigEntry, ThermomixDeviceState, ThermomixMonitor

PARALLEL_UPDATES = 0


@dataclass(kw_only=True, frozen=True)
class ThermomixSensorDescription(SensorEntityDescription):
    """Describes a Thermomix sensor."""

    value_fn: Callable[[ThermomixDeviceState], StateType | datetime]
    attributes_fn: Callable[[ThermomixDeviceState], dict[str, Any]] | None = None
    refresh_on_tick: bool = False


SENSORS: tuple[ThermomixSensorDescription, ...] = (
    ThermomixSensorDescription(
        key="state",
        translation_key="state",
        device_class=SensorDeviceClass.ENUM,
        options=COOK_STATES,
        value_fn=lambda d: d.state,
        attributes_fn=lambda d: {
            "cooking_activity_id": (
                d.activity.cooking_activity_id if d.activity else None
            ),
            "stale_at": d.stale_at,
            "dismissal_at": d.dismissal_at,
        },
    ),
    ThermomixSensorDescription(
        key="recipe",
        translation_key="recipe",
        value_fn=lambda d: d.recipe_name,
        attributes_fn=lambda d: {
            "recipe_id": d.recipe_id,
            "recipe_type": d.recipe_type,
        },
    ),
    ThermomixSensorDescription(
        key="step",
        translation_key="step",
        value_fn=lambda d: d.step,
        attributes_fn=lambda d: {
            "step_number": d.step_number,
            "step_total": d.step_total,
        },
    ),
    ThermomixSensorDescription(
        key="remaining_time",
        translation_key="remaining_time",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_unit_of_measurement=UnitOfTime.MINUTES,
        value_fn=lambda d: d.remaining_seconds,
        attributes_fn=lambda d: {"is_time_estimated": d.is_time_estimated},
        refresh_on_tick=True,
    ),
    ThermomixSensorDescription(
        key="finish_time",
        translation_key="finish_time",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda d: d.finish_at,
    ),
    ThermomixSensorDescription(
        key="current_temperature",
        translation_key="current_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda d: d.current_temperature,
    ),
    ThermomixSensorDescription(
        key="target_temperature",
        translation_key="target_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda d: d.target_temperature,
    ),
    ThermomixSensorDescription(
        key="message",
        translation_key="message",
        value_fn=lambda d: d.message_body or d.message_title,
        attributes_fn=lambda d: {
            "title": d.message_title,
            "body": d.message_body,
            "criticality": d.message_criticality,
        },
    ),
    ThermomixSensorDescription(
        key="last_update",
        translation_key="last_update",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.last_update,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ThermomixConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors for every known appliance, and for ones seen later."""
    monitor = entry.runtime_data

    @callback
    def _add_device(device: ThermomixDeviceState) -> None:
        async_add_entities(
            ThermomixSensor(monitor, device, description) for description in SENSORS
        )

    for device in monitor.devices.values():
        _add_device(device)
    entry.async_on_unload(
        async_dispatcher_connect(hass, monitor.signal_new_device, _add_device)
    )


class ThermomixSensor(ThermomixEntity, SensorEntity):
    """A sensor derived from the appliance cook state."""

    entity_description: ThermomixSensorDescription

    def __init__(
        self,
        monitor: ThermomixMonitor,
        device: ThermomixDeviceState,
        description: ThermomixSensorDescription,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(monitor, device, description)
        self._refresh_on_tick = description.refresh_on_tick

    @property
    def native_value(self) -> StateType | datetime:
        """Return the sensor value."""
        return self.entity_description.value_fn(self.device)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra attributes."""
        if self.entity_description.attributes_fn is None:
            return None
        return self.entity_description.attributes_fn(self.device)
