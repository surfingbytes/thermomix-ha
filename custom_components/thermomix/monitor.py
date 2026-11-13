"""Receive live cook state from Thermomix appliances.

The Thermomix (TM6/TM7) pushes its remote-monitoring state to the Cookidoo
mobile app as Firebase Cloud Messaging data messages; there is no endpoint to
poll. This module registers its own push token with the Cookidoo IoT backend
and turns every message into per-appliance state, timers (stale/dismissal) and
events for the entity platforms.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from functools import partial
import logging
import time
from typing import Any

from cookidoo_api import (
    Cookidoo,
    CookidooAuthException,
    CookidooException,
    CookidooParseException,
    CookidooRequestException,
)
from cookidoo_api.types import CookidooCookingActivity

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_DEVICE_MAP,
    CONF_KNOWN_DEVICES,
    CONF_MOBILE_APP_ID,
    CONF_MODELS,
    EVENT_ACKNOWLEDGED,
    EVENT_CLEARED,
    EVENT_COOKING_UPDATE,
    EVENT_DONE,
    EVENT_MESSAGE,
    EVENT_PAUSED,
    EVENT_RESUMED,
    EVENT_STALE,
    EVENT_STARTED,
    EVENT_STEP_CHANGED,
    EVENT_UPDATED,
    SIGNAL_EVENT,
    SIGNAL_NEW_DEVICE,
    SIGNAL_TICK,
    SIGNAL_UPDATE,
    STATE_ACKNOWLEDGED,
    STATE_DONE,
    STATE_IDLE,
    STATE_PAUSED,
    STATE_RUNNING,
    STATE_STALE,
    STORAGE_KEY_FCM,
    STORAGE_VERSION,
    TICK_INTERVAL,
)
from .receiver import ThermomixPushReceiver

_LOGGER = logging.getLogger(__name__)

type ThermomixConfigEntry = ConfigEntry[ThermomixMonitor]


def parse_push_timestamp(value: object) -> datetime | None:
    """Parse a push timestamp (epoch milliseconds or ISO 8601) to UTC."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        # Cookidoo sends epoch seconds; accept milliseconds as well.
        number = int(text)
        return dt_util.utc_from_timestamp(number / 1000 if number > 1e12 else number)
    parsed = dt_util.parse_datetime(text)
    return dt_util.as_utc(parsed) if parsed is not None else None


def parse_step(step: str | None) -> tuple[int | None, int | None]:
    """Split a "5/9" step indicator into (current, total)."""
    if not step or "/" not in step:
        return None, None
    current, _, total = step.partition("/")
    try:
        return int(current.strip()), int(total.strip())
    except ValueError:
        return None, None


@dataclass
class ThermomixDeviceState:
    """Last known cook state of one appliance."""

    # Stable HA-side key (one slot per paired appliance); entity unique ids use it.
    device_id: str
    model: str | None = None
    # Opaque remote-monitoring id, known once Cookidoo lists it or a push arrives.
    rmi_device_id: str | None = None
    activity: CookidooCookingActivity | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    dismissal_at: datetime | None = None
    last_update: datetime | None = None
    # Set by the stale/dismissal timers once the pushed state has expired.
    override: str | None = None

    @property
    def visible(self) -> bool:
        """Whether the last activity is still current (not dismissed)."""
        return self.activity is not None and self.override != STATE_IDLE

    @property
    def state(self) -> str:
        """Cook state as shown by the state sensor."""
        if self.activity is None or self.override == STATE_IDLE:
            return STATE_IDLE
        if self.override is not None:
            return self.override
        if self.activity.state is None:
            return STATE_IDLE
        return self.activity.state.value.lower()

    @property
    def is_active(self) -> bool:
        """Whether a cook is running or paused."""
        return self.state in (STATE_RUNNING, STATE_PAUSED)

    @property
    def recipe_name(self) -> str | None:
        """Recipe title (or manual-mode label)."""
        return self.activity.recipe_name if self.visible and self.activity else None

    @property
    def recipe_id(self) -> str | None:
        """Cookidoo recipe id."""
        return self.activity.recipe_id if self.visible and self.activity else None

    @property
    def recipe_type(self) -> str | None:
        """Recipe type (VORWERK, CUSTOM, ...)."""
        return self.activity.recipe_type if self.visible and self.activity else None

    @property
    def step(self) -> str | None:
        """Guided-cooking step indicator, e.g. "5/9"."""
        return self.activity.step if self.visible and self.activity else None

    @property
    def step_number(self) -> int | None:
        """Current step number."""
        return parse_step(self.step)[0]

    @property
    def step_total(self) -> int | None:
        """Total number of steps."""
        return parse_step(self.step)[1]

    @property
    def finish_at(self) -> datetime | None:
        """When the running step is expected to finish."""
        if not self.is_active or self.activity is None:
            return None
        return self.activity.completed_at

    @property
    def remaining_seconds(self) -> int | None:
        """Seconds left in the running step, derived from the finish time."""
        if not self.is_active or self.activity is None:
            return None
        if self.activity.completed_at is None:
            return self.activity.remaining_seconds
        delta = self.activity.completed_at - dt_util.utcnow()
        return max(0, int(delta.total_seconds()))

    @property
    def is_time_estimated(self) -> bool:
        """Whether the remaining time is an estimate."""
        return bool(self.activity.is_time_estimated) if self.activity else False

    @property
    def current_temperature(self) -> float | None:
        """Current temperature, None when the appliance reports none."""
        if not self.visible or self.activity is None:
            return None
        return self.activity.current_temperature

    @property
    def target_temperature(self) -> float | None:
        """Target temperature."""
        if not self.visible or self.activity is None:
            return None
        return self.activity.target_temperature

    @property
    def message_title(self) -> str | None:
        """Title of the appliance message, if any."""
        return self.activity.message_title if self.visible and self.activity else None

    @property
    def message_body(self) -> str | None:
        """Body of the appliance message, if any."""
        return self.activity.message_body if self.visible and self.activity else None

    @property
    def message_criticality(self) -> str | None:
        """Criticality of the appliance message, if any."""
        if not self.visible or self.activity is None:
            return None
        return self.activity.message_criticality

    @property
    def stale_at(self) -> datetime | None:
        """When the pushed state should be considered stale."""
        return self.activity.stale_at if self.activity else None

    def as_event_data(self) -> dict[str, Any]:
        """Serialise the state for events and attributes."""
        return {
            "device_id": self.device_id,
            "model": self.model,
            "state": self.state,
            "cooking_activity_id": (
                self.activity.cooking_activity_id if self.activity else None
            ),
            "recipe_name": self.recipe_name,
            "recipe_id": self.recipe_id,
            "recipe_type": self.recipe_type,
            "step": self.step,
            "step_number": self.step_number,
            "step_total": self.step_total,
            "remaining_seconds": self.remaining_seconds,
            "is_time_estimated": self.is_time_estimated,
            "finish_at": self.finish_at.isoformat() if self.finish_at else None,
            "current_temperature": self.current_temperature,
            "target_temperature": self.target_temperature,
            "message_title": self.message_title,
            "message_body": self.message_body,
            "message_criticality": self.message_criticality,
        }


# Wait between two attempts to reconnect a dropped push receiver.
RECEIVER_RETRY_S = 300


class ThermomixMonitor:
    """Owns the push receiver and the state of every appliance on the account."""

    def __init__(
        self, hass: HomeAssistant, entry: ThermomixConfigEntry, cookidoo: Cookidoo
    ) -> None:
        """Initialise the monitor."""
        self.hass = hass
        self.entry = entry
        self.cookidoo = cookidoo
        self.devices: dict[str, ThermomixDeviceState] = {}
        self.models: list[str] = list(entry.data.get(CONF_MODELS, []))
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{STORAGE_KEY_FCM}.{entry.entry_id}"
        )
        self._receiver: ThermomixPushReceiver | None = None
        self._timers: dict[str, dict[str, CALLBACK_TYPE]] = {}
        self._ticker: CALLBACK_TYPE | None = None
        self._restarting = False
        self._last_restart: float | None = None

    # --- signals ---------------------------------------------------------

    @property
    def signal_new_device(self) -> str:
        """Dispatcher signal fired with a new ThermomixDeviceState."""
        return f"{SIGNAL_NEW_DEVICE}_{self.entry.entry_id}"

    @property
    def signal_tick(self) -> str:
        """Dispatcher signal fired periodically for time-derived entities."""
        return f"{SIGNAL_TICK}_{self.entry.entry_id}"

    @staticmethod
    def signal_update(device_id: str) -> str:
        """Dispatcher signal fired when a device's state changed."""
        return f"{SIGNAL_UPDATE}_{device_id}"

    @staticmethod
    def signal_event(device_id: str) -> str:
        """Dispatcher signal fired with (event_type, data) for a device."""
        return f"{SIGNAL_EVENT}_{device_id}"

    # --- naming ----------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        """Whether the push receiver is connected."""
        return self._receiver is not None and self._receiver.is_connected

    @property
    def push_token(self) -> str | None:
        """The registered FCM token (diagnostics)."""
        return self._receiver.token if self._receiver else None

    @staticmethod
    def model_name(device: ThermomixDeviceState) -> str:
        """Model shown on the device card."""
        return f"Thermomix {device.model}" if device.model else "Thermomix"

    def device_name(self, device: ThermomixDeviceState) -> str:
        """Name of the HA device for an appliance."""
        name = self.model_name(device)
        same = [d for d in self.devices.values() if self.model_name(d) == name]
        if len(same) <= 1:
            return name
        return f"{name} {same.index(device) + 1}"

    @property
    def _account_key(self) -> str:
        return self.entry.unique_id or self.entry.entry_id

    # --- lifecycle -------------------------------------------------------

    async def async_setup(self) -> None:
        """Log in, discover appliances and start receiving pushes."""
        await self._async_login()

        try:
            devices = await self.cookidoo.get_devices()
            # CookidooDevice.type is a ThermomixMachineType enum ("TM6", "TM7").
            self.models = [
                str(getattr(d.type, "value", d.type)) for d in devices if d.type
            ]
        except CookidooException as err:
            _LOGGER.debug("Could not list paired appliances: %s", err)

        # One device per paired appliance, available right away (idle) even
        # while the appliance is offline and Cookidoo lists no monitoring id.
        for index, model in enumerate(self.models):
            self._ensure_slot(f"{self._account_key}_{index}", model, notify=False)

        # Restore the remote-monitoring id -> slot mapping from earlier runs.
        # Older versions keyed devices by the remote-monitoring id itself.
        device_map: dict[str, str] = dict(self.entry.data.get(CONF_DEVICE_MAP, {}))
        for rmi_id in self.entry.data.get(CONF_KNOWN_DEVICES, []):
            device_map.setdefault(rmi_id, rmi_id)
        for rmi_id, slot in device_map.items():
            device, _ = self._ensure_slot(slot, None, notify=False)
            device.rmi_device_id = rmi_id

        try:
            monitored = await self.cookidoo.get_monitored_device_ids()
        except CookidooException as err:
            _LOGGER.warning("Could not list monitored appliances: %s", err)
            monitored = []
        _LOGGER.debug(
            "Paired appliances: %s, monitored now: %d", self.models, len(monitored)
        )
        for rmi_id in monitored:
            self._device_for_rmi(rmi_id, notify=False)
        self._persist_devices()

        await self._async_start_receiver()
        self._ticker = async_track_time_interval(self.hass, self._tick, TICK_INTERVAL)

    async def async_stop(self, *_: Any) -> None:
        """Stop receiving pushes and cancel all timers."""
        if self._ticker is not None:
            self._ticker()
            self._ticker = None
        for device_id in list(self._timers):
            self._cancel_device_timers(device_id)
        if self._receiver is not None:
            receiver, self._receiver = self._receiver, None
            try:
                await receiver.stop()
            except Exception:  # noqa: BLE001 - never fail an unload
                _LOGGER.debug("Stopping the push receiver failed", exc_info=True)

    async def _async_login(self) -> None:
        try:
            if self.cookidoo.auth_data is not None:
                try:
                    await self.cookidoo.get_user_info()
                except CookidooAuthException:
                    _LOGGER.debug("Stored tokens are no longer valid, logging in")
                else:
                    return
            await self.cookidoo.login()
        except CookidooAuthException as err:
            raise ConfigEntryAuthFailed(
                "Cookidoo rejected the credentials, re-authenticate"
            ) from err
        except (CookidooRequestException, CookidooParseException) as err:
            raise ConfigEntryNotReady(f"Could not log in to Cookidoo: {err}") from err

    async def _async_start_receiver(self) -> None:
        credentials = await self._store.async_load()
        receiver = ThermomixPushReceiver(
            self.cookidoo,
            self._handle_push,
            mobile_app_id=self.entry.data[CONF_MOBILE_APP_ID],
            session=async_create_clientsession(self.hass),
            credentials=credentials,
            on_credentials=self._save_credentials,
        )
        try:
            await receiver.start()
        except CookidooAuthException as err:
            await receiver.stop()
            raise ConfigEntryAuthFailed(
                "Cookidoo rejected the push-token registration"
            ) from err
        except (CookidooException, RuntimeError, OSError) as err:
            await receiver.stop()
            raise ConfigEntryNotReady(
                f"Could not start the Thermomix push receiver: {err}"
            ) from err
        self._receiver = receiver
        if receiver.credentials is not None and receiver.credentials != credentials:
            self._save_credentials(receiver.credentials)
        _LOGGER.debug("Push receiver started, token registered")

    @callback
    def _save_credentials(self, credentials: dict[str, Any]) -> None:
        """Persist FCM credentials so the same push token survives restarts."""
        self._store.async_delay_save(lambda: credentials, 10)

    # --- devices ---------------------------------------------------------

    def _ensure_slot(
        self, slot: str, model: str | None, notify: bool = True
    ) -> tuple[ThermomixDeviceState, bool]:
        if (device := self.devices.get(slot)) is not None:
            if model and not device.model:
                device.model = model
            return device, False
        device = ThermomixDeviceState(slot, model=model)
        self.devices[slot] = device
        if notify:
            async_dispatcher_send(self.hass, self.signal_new_device, device)
        return device, True

    def _device_for_rmi(
        self, rmi_id: str, notify: bool = True
    ) -> ThermomixDeviceState:
        """Return the device for a remote-monitoring id, assigning a slot.

        The remote-monitoring id is opaque and cannot be matched to a paired
        appliance, so an unknown id takes the first slot without one; with a
        single Thermomix on the account that is always correct.
        """
        for device in self.devices.values():
            if device.rmi_device_id == rmi_id:
                return device
        free = next(
            (d for d in self.devices.values() if d.rmi_device_id is None), None
        )
        if free is None:
            free, _ = self._ensure_slot(rmi_id, None, notify=notify)
        free.rmi_device_id = rmi_id
        if notify:
            self._persist_devices()
        return free

    def _persist_devices(self) -> None:
        device_map = {
            d.rmi_device_id: d.device_id
            for d in self.devices.values()
            if d.rmi_device_id is not None
        }
        data = self.entry.data
        if (
            device_map == data.get(CONF_DEVICE_MAP, {})
            and self.models == list(data.get(CONF_MODELS, []))
            and CONF_KNOWN_DEVICES not in data
        ):
            return
        new_data = {**data, CONF_DEVICE_MAP: device_map, CONF_MODELS: self.models}
        new_data.pop(CONF_KNOWN_DEVICES, None)
        self.hass.config_entries.async_update_entry(self.entry, data=new_data)

    # --- pushes ----------------------------------------------------------

    @callback
    def _handle_push(
        self, activity: CookidooCookingActivity, raw: dict[str, Any]
    ) -> None:
        """Apply a decoded push to the device state and notify listeners."""
        if not activity.device_id:
            _LOGGER.debug("Ignoring a cook-state push without deviceId: %s", raw)
            return
        _LOGGER.debug("Cook state push: %s", raw)

        device = self._device_for_rmi(activity.device_id)
        prev_state = device.state
        prev_step = device.step
        prev_message = device.message_body
        prev_activity_id = (
            device.activity.cooking_activity_id if device.activity else None
        )

        device.activity = activity
        device.raw = raw
        device.override = None
        device.last_update = dt_util.utcnow()
        device.dismissal_at = parse_push_timestamp(
            raw.get("dismissalDate", raw.get("dismissalTimestamp"))
        )
        if device.state == STATE_DONE and device.dismissal_at is None:
            # A finished cook without a dismissal date would otherwise linger
            # as stale forever (switching the appliance off sends nothing).
            device.dismissal_at = device.stale_at

        self._schedule_device_timers(device)
        async_dispatcher_send(self.hass, self.signal_update(device.device_id))

        for event_type in self._derive_events(
            device, prev_state, prev_step, prev_message, prev_activity_id
        ):
            self._emit(device, event_type)

        if device.state == STATE_ACKNOWLEDGED:
            # Confirmed on the appliance: the cook is over. These pushes carry
            # no dismissal date and no further push follows, so clear it now.
            self._cancel_device_timers(device.device_id)
            self._dismiss(device.device_id, device.last_update)

    @staticmethod
    def _derive_events(
        device: ThermomixDeviceState,
        prev_state: str,
        prev_step: str | None,
        prev_message: str | None,
        prev_activity_id: str | None,
    ) -> list[str]:
        state = device.state
        new_cook = (
            device.activity is not None
            and device.activity.cooking_activity_id != prev_activity_id
        )
        events: list[str] = []
        if state == STATE_RUNNING and (
            new_cook or prev_state not in (STATE_RUNNING, STATE_PAUSED)
        ):
            events.append(EVENT_STARTED)
        elif state == STATE_RUNNING and prev_state == STATE_PAUSED:
            events.append(EVENT_RESUMED)
        elif state == STATE_PAUSED and prev_state != STATE_PAUSED:
            events.append(EVENT_PAUSED)
        elif state == STATE_DONE and prev_state != STATE_DONE:
            events.append(EVENT_DONE)
        elif state == STATE_ACKNOWLEDGED and prev_state != STATE_ACKNOWLEDGED:
            events.append(EVENT_ACKNOWLEDGED)
        if (
            device.is_active
            and device.step
            and device.step != prev_step
            and EVENT_STARTED not in events
        ):
            events.append(EVENT_STEP_CHANGED)
        if device.message_body and device.message_body != prev_message:
            events.append(EVENT_MESSAGE)
        if not events:
            events.append(EVENT_UPDATED)
        return events

    def _emit(self, device: ThermomixDeviceState, event_type: str) -> None:
        data = device.as_event_data()
        async_dispatcher_send(
            self.hass, self.signal_event(device.device_id), event_type, data
        )
        self.hass.bus.async_fire(EVENT_COOKING_UPDATE, {"type": event_type, **data})

    # --- timers ----------------------------------------------------------

    def _cancel_device_timers(self, device_id: str) -> None:
        for cancel in self._timers.pop(device_id, {}).values():
            cancel()

    def _schedule_device_timers(self, device: ThermomixDeviceState) -> None:
        """Mirror the app: mark the state stale, then clear it, on schedule."""
        self._cancel_device_timers(device.device_id)
        if not device.visible:
            return
        now = dt_util.utcnow()
        timers: dict[str, CALLBACK_TYPE] = {}
        if device.stale_at is not None:
            delay = max(0.0, (device.stale_at - now).total_seconds())
            timers["stale"] = async_call_later(
                self.hass, delay, partial(self._mark_stale, device.device_id)
            )
        if device.dismissal_at is not None:
            delay = max(0.0, (device.dismissal_at - now).total_seconds())
            timers["dismiss"] = async_call_later(
                self.hass, delay, partial(self._dismiss, device.device_id)
            )
        self._timers[device.device_id] = timers

    @callback
    def _mark_stale(self, device_id: str, _now: datetime) -> None:
        device = self.devices.get(device_id)
        if device is None or not device.visible or device.state == STATE_STALE:
            return
        _LOGGER.debug("Cook state of %s is stale", device_id)
        device.override = STATE_STALE
        async_dispatcher_send(self.hass, self.signal_update(device_id))
        self._emit(device, EVENT_STALE)

    @callback
    def _dismiss(self, device_id: str, _now: datetime) -> None:
        device = self.devices.get(device_id)
        if device is None or not device.visible:
            return
        _LOGGER.debug("Cook state of %s dismissed", device_id)
        device.override = STATE_IDLE
        async_dispatcher_send(self.hass, self.signal_update(device_id))
        self._emit(device, EVENT_CLEARED)

    @callback
    def _tick(self, _now: datetime) -> None:
        async_dispatcher_send(self.hass, self.signal_tick)
        self._check_receiver()

    @callback
    def _check_receiver(self) -> None:
        """Restart the push receiver when Firebase dropped it."""
        if self._restarting or (
            self._receiver is not None and self._receiver.is_connected
        ):
            return
        if (
            self._last_restart is not None
            and time.monotonic() - self._last_restart < RECEIVER_RETRY_S
        ):
            return
        self._restarting = True
        self.entry.async_create_background_task(
            self.hass, self._async_restart_receiver(), "thermomix_restart_receiver"
        )

    async def _async_restart_receiver(self) -> None:
        _LOGGER.warning("Thermomix push connection lost, reconnecting")
        try:
            if self._receiver is not None:
                receiver, self._receiver = self._receiver, None
                try:
                    await receiver.stop()
                except Exception:  # noqa: BLE001
                    _LOGGER.debug("Stopping the old receiver failed", exc_info=True)
            await self._async_start_receiver()
        except (ConfigEntryAuthFailed, ConfigEntryNotReady) as err:
            _LOGGER.warning("Reconnecting the Thermomix push receiver failed: %s", err)
        finally:
            self._last_restart = time.monotonic()
            self._restarting = False
