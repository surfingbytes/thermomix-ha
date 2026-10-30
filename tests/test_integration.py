"""Tests for the Thermomix integration setup and push handling."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import MagicMock

from cookidoo_api import cooking_activity_from_push
from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import Event, HomeAssistant

from custom_components.thermomix.const import EVENT_COOKING_UPDATE
from custom_components.thermomix.monitor import parse_push_timestamp, parse_step

from .conftest import DEVICE_ID, PUSH_RUNNING


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


def _push(mock_receiver: MagicMock, payload: dict[str, Any]) -> None:
    """Feed a payload through the callback the monitor gave the receiver."""
    on_push = mock_receiver.call_args.args[1]
    on_push(cooking_activity_from_push(payload), dict(payload))


def test_parse_helpers() -> None:
    """Steps and timestamps are parsed from the wire formats."""
    assert parse_step("5/9") == (5, 9)
    assert parse_step(None) == (None, None)
    assert parse_step("garbage") == (None, None)
    assert parse_push_timestamp("1787924895000").isoformat() == "2026-08-28T13:48:15+00:00"
    assert parse_push_timestamp("2026-08-28T13:37:48Z").isoformat() == "2026-08-28T13:37:48+00:00"
    assert parse_push_timestamp(None) is None


async def test_setup_creates_idle_entities(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_cookidoo: MagicMock,
    mock_receiver: MagicMock,
) -> None:
    """Known appliances get entities right away, idle until a push arrives."""
    await _setup(hass, config_entry)

    mock_cookidoo.login.assert_awaited_once()
    mock_receiver.return_value.start.assert_awaited_once()
    assert mock_receiver.call_args.kwargs["mobile_app_id"] == (
        "11111111-2222-3333-4444-555555555555"
    )

    assert hass.states.get("sensor.thermomix_tm7_cooking_state").state == "idle"
    assert hass.states.get("sensor.thermomix_tm7_current_step").state == "unknown"
    assert hass.states.get("binary_sensor.thermomix_tm7_cooking").state == "off"
    assert hass.states.get("binary_sensor.thermomix_tm7_push_connection").state == "on"
    assert config_entry.data["device_map"] == {DEVICE_ID: "user-1_0"}
    assert config_entry.data["models"] == ["TM7"]

    await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
    mock_receiver.return_value.stop.assert_awaited_once()
    assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_push_updates_entities_and_fires_events(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_cookidoo: MagicMock,
    mock_receiver: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A cook-state push updates sensors, the event entity and the bus."""
    freezer.move_to("2026-08-28T13:30:00Z")
    await _setup(hass, config_entry)

    events: list[Event] = []
    hass.bus.async_listen(EVENT_COOKING_UPDATE, events.append)

    _push(mock_receiver, PUSH_RUNNING)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.thermomix_tm7_cooking_state").state == "running"
    assert hass.states.get("sensor.thermomix_tm7_recipe").state == "Purè di patate"
    step = hass.states.get("sensor.thermomix_tm7_current_step")
    assert step.state == "5/9"
    assert step.attributes["step_number"] == 5
    assert step.attributes["step_total"] == 9
    assert hass.states.get("sensor.thermomix_tm7_current_temperature").state == "unknown"
    assert hass.states.get("sensor.thermomix_tm7_target_temperature").state == "95.0"
    assert hass.states.get("sensor.thermomix_tm7_step_finish_time").state == (
        "2026-08-28T13:37:48+00:00"
    )
    # 7 min 48 s left, shown in minutes by the suggested unit
    remaining = hass.states.get("sensor.thermomix_tm7_remaining_time")
    assert remaining.attributes["unit_of_measurement"] == "min"
    assert float(remaining.state) == pytest.approx(7.8)
    assert hass.states.get("binary_sensor.thermomix_tm7_cooking").state == "on"

    event = hass.states.get("event.thermomix_tm7_cooking_event")
    assert event.attributes["event_type"] == "started"
    assert event.attributes["step"] == "5/9"
    assert [e.data["type"] for e in events] == ["started"]

    # Next step of the same cook
    _push(mock_receiver, {**PUSH_RUNNING, "trailingText": "6/9"})
    await hass.async_block_till_done()
    assert hass.states.get("sensor.thermomix_tm7_current_step").state == "6/9"
    assert hass.states.get("event.thermomix_tm7_cooking_event").attributes[
        "event_type"
    ] == "step_changed"

    # Pause, resume, done
    _push(mock_receiver, {**PUSH_RUNNING, "trailingText": "6/9", "state": "paused"})
    await hass.async_block_till_done()
    assert hass.states.get("sensor.thermomix_tm7_cooking_state").state == "paused"
    _push(mock_receiver, {**PUSH_RUNNING, "trailingText": "6/9", "state": "running"})
    await hass.async_block_till_done()
    _push(mock_receiver, {**PUSH_RUNNING, "trailingText": "6/9", "state": "done"})
    await hass.async_block_till_done()
    assert hass.states.get("sensor.thermomix_tm7_cooking_state").state == "done"
    assert hass.states.get("binary_sensor.thermomix_tm7_cooking").state == "off"
    assert hass.states.get("sensor.thermomix_tm7_remaining_time").state == "unknown"
    assert [e.data["type"] for e in events] == [
        "started",
        "step_changed",
        "paused",
        "resumed",
        "done",
    ]


async def test_stale_and_dismissal_timers(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_cookidoo: MagicMock,
    mock_receiver: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Without further pushes the state goes stale, then idle, like in the app."""
    freezer.move_to("2026-08-28T13:30:00Z")
    await _setup(hass, config_entry)

    # Dismissal far in the future so the stale timer fires first.
    _push(mock_receiver, {**PUSH_RUNNING, "dismissalDate": "1787925500000"})
    await hass.async_block_till_done()
    assert hass.states.get("sensor.thermomix_tm7_cooking_state").state == "running"

    # staleDate = 13:48:15Z
    freezer.move_to("2026-08-28T13:48:20Z")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.thermomix_tm7_cooking_state").state == "stale"
    assert hass.states.get("sensor.thermomix_tm7_recipe").state == "Purè di patate"
    assert hass.states.get("event.thermomix_tm7_cooking_event").attributes[
        "event_type"
    ] == "stale"

    # dismissalDate = 13:58:20Z
    freezer.move_to("2026-08-28T13:58:30Z")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.thermomix_tm7_cooking_state").state == "idle"
    assert hass.states.get("sensor.thermomix_tm7_recipe").state == "unknown"
    assert hass.states.get("sensor.thermomix_tm7_current_step").state == "unknown"


async def test_offline_appliance_has_device_before_first_push(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_cookidoo: MagicMock,
    mock_receiver: MagicMock,
) -> None:
    """A paired appliance that is offline at startup still gets a device."""
    mock_cookidoo.get_monitored_device_ids.return_value = []
    await _setup(hass, config_entry)
    assert hass.states.get("sensor.thermomix_tm7_cooking_state").state == "idle"
    assert config_entry.data["device_map"] == {}

    # Its first push is attached to that device, not a new one.
    _push(mock_receiver, PUSH_RUNNING)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.thermomix_tm7_cooking_state").state == "running"
    assert config_entry.data["device_map"] == {DEVICE_ID: "user-1_0"}
    assert len(hass.states.async_entity_ids("sensor")) == 9


async def test_mapping_survives_restart(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_cookidoo: MagicMock,
    mock_receiver: MagicMock,
) -> None:
    """A stored mapping keeps the push on the same device after a reload."""
    mock_cookidoo.get_monitored_device_ids.return_value = []
    await _setup(hass, config_entry)
    _push(mock_receiver, PUSH_RUNNING)
    await hass.async_block_till_done()

    assert await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()
    _push(mock_receiver, {**PUSH_RUNNING, "trailingText": "7/9"})
    await hass.async_block_till_done()
    assert hass.states.get("sensor.thermomix_tm7_current_step").state == "7/9"
    assert config_entry.data["device_map"] == {DEVICE_ID: "user-1_0"}


async def test_unlisted_appliance_gets_device_on_first_push(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_cookidoo: MagicMock,
    mock_receiver: MagicMock,
) -> None:
    """Pushes from an appliance Cookidoo did not list still create a device."""
    mock_cookidoo.get_devices.return_value = []
    mock_cookidoo.get_monitored_device_ids.return_value = []
    await _setup(hass, config_entry)
    assert hass.states.get("sensor.thermomix_cooking_state") is None

    _push(mock_receiver, PUSH_RUNNING)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.thermomix_cooking_state").state == "running"
    assert config_entry.data["device_map"] == {DEVICE_ID: DEVICE_ID}


async def test_legacy_known_devices_are_migrated(
    hass: HomeAssistant,
    mock_cookidoo: MagicMock,
    mock_receiver: MagicMock,
    config_entry: MockConfigEntry,
) -> None:
    """Entries from 0.1.0 keep their remote-monitoring keyed device."""
    mock_cookidoo.get_devices.return_value = []
    config_entry = MockConfigEntry(
        domain=config_entry.domain,
        unique_id=config_entry.unique_id,
        data={**config_entry.data, "known_devices": [DEVICE_ID]},
    )
    await _setup(hass, config_entry)
    assert "known_devices" not in config_entry.data
    assert config_entry.data["device_map"] == {DEVICE_ID: DEVICE_ID}
    assert hass.states.get("sensor.thermomix_cooking_state").state == "idle"


async def test_remaining_time_ticks(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_cookidoo: MagicMock,
    mock_receiver: MagicMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Remaining time counts down without new pushes."""
    freezer.move_to("2026-08-28T13:30:00Z")
    await _setup(hass, config_entry)
    _push(mock_receiver, {**PUSH_RUNNING, "staleDate": "1787925500000"})
    await hass.async_block_till_done()
    before = float(hass.states.get("sensor.thermomix_tm7_remaining_time").state)

    freezer.tick(timedelta(seconds=60))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    after = float(hass.states.get("sensor.thermomix_tm7_remaining_time").state)
    assert before - after == pytest.approx(1.0)
