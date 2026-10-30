"""Constants for the Thermomix integration."""

from datetime import timedelta

DOMAIN = "thermomix"
MANUFACTURER = "Vorwerk"

CONF_MOBILE_APP_ID = "mobile_app_id"
CONF_KNOWN_DEVICES = "known_devices"  # legacy, migrated to CONF_DEVICE_MAP
CONF_DEVICE_MAP = "device_map"
CONF_MODELS = "models"

STORAGE_VERSION = 1
STORAGE_KEY_FCM = f"{DOMAIN}.fcm"

SIGNAL_NEW_DEVICE = f"{DOMAIN}_new_device"
SIGNAL_UPDATE = f"{DOMAIN}_update"
SIGNAL_EVENT = f"{DOMAIN}_event"
SIGNAL_TICK = f"{DOMAIN}_tick"

EVENT_COOKING_UPDATE = f"{DOMAIN}_cooking_update"

# Cook states as exposed by the state sensor (lower-case push states + our own).
STATE_IDLE = "idle"
STATE_RUNNING = "running"
STATE_PAUSED = "paused"
STATE_DONE = "done"
STATE_ACKNOWLEDGED = "acknowledged"
STATE_STALE = "stale"
COOK_STATES = [
    STATE_IDLE,
    STATE_RUNNING,
    STATE_PAUSED,
    STATE_DONE,
    STATE_ACKNOWLEDGED,
    STATE_STALE,
]

EVENT_STARTED = "started"
EVENT_STEP_CHANGED = "step_changed"
EVENT_PAUSED = "paused"
EVENT_RESUMED = "resumed"
EVENT_DONE = "done"
EVENT_ACKNOWLEDGED = "acknowledged"
EVENT_STALE = "stale"
EVENT_MESSAGE = "message"
EVENT_CLEARED = "cleared"
EVENT_UPDATED = "updated"
EVENT_TYPES = [
    EVENT_STARTED,
    EVENT_STEP_CHANGED,
    EVENT_PAUSED,
    EVENT_RESUMED,
    EVENT_DONE,
    EVENT_ACKNOWLEDGED,
    EVENT_STALE,
    EVENT_MESSAGE,
    EVENT_CLEARED,
    EVENT_UPDATED,
]

# How often entities derived from wall-clock time (remaining time, connection)
# refresh while nothing is pushed.
TICK_INTERVAL = timedelta(seconds=30)
