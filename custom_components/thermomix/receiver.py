"""Firebase Cloud Messaging receiver for Thermomix cook-state pushes.

The remote-monitoring backend has no endpoint that returns the current cook
state: the appliance pushes it to the Cookidoo mobile app as an FCM *data
message*. To observe it we obtain an FCM registration token of our own (via
``firebase-messaging``), register it with Cookidoo (``register_push_token``,
available in cookidoo-api >= 0.18.4) and decode every incoming message.

This mirrors ``cookidoo_api.remote_monitoring`` from cookidoo-api 0.19, which
the core Cookidoo integration's pinned release does not ship yet; keeping it
here lets both integrations share one installed library version.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import json
import logging
import re
from typing import Any

from aiohttp import ClientSession
from cookidoo_api import Cookidoo, cooking_activity_from_push
from cookidoo_api.types import CookidooCookingActivity
from firebase_messaging import FcmPushClient, FcmRegisterConfig

_LOGGER = logging.getLogger(__name__)

# Firebase project of the Cookidoo Android app (com.vorwerk.cookidoo). These are
# the public client identifiers shipped in the APK's google-services resources;
# appliance pushes are addressed to tokens registered under this project.
FCM_PROJECT_ID = "cookidoo-app"
FCM_APP_ID = "1:447648593759:android:ebfbf2b01378844b"
FCM_API_KEY = "AIzaSyCPyZm8EAdpVhWhNLFv3cOw_Kx4iNxR_E4"
FCM_SENDER_ID = "447648593759"

# Google's register3 endpoint rejects a fresh check-in with
# PHONE_REGISTRATION_ERROR more often than not; retry a few times.
FCM_CHECKIN_ATTEMPTS = 4
FCM_CHECKIN_RETRY_DELAY_S = 2

# The appliance flattens the cook state into the data map, but the app's push
# service also accepts it nested under these keys (object or JSON string).
PUSH_NESTED_PAYLOAD_KEYS = ("cookingActivity", "remoteMonitoringInfo")

PushCallback = Callable[[CookidooCookingActivity, dict[str, Any]], None]

# Temperature fields arrive as display text ("85°", "100 °C"); the library
# only parses plain numbers.
PUSH_TEMPERATURE_KEYS = ("primaryInfo", "secondaryInfo")
_DEGREE_SUFFIX = re.compile(r"\s*°\s*[CF]?\s*$")


def _strip_degrees(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the payload with the degree sign removed from temperatures."""
    cleaned = dict(payload)
    for key in PUSH_TEMPERATURE_KEYS:
        value = cleaned.get(key)
        if isinstance(value, str):
            cleaned[key] = _DEGREE_SUFFIX.sub("", value)
    return cleaned


def _b64_field(value: str) -> str:
    """Return a base64url field cut at its first parameter and padded.

    The crypto-key and encryption headers of Cookidoo pushes carry unpadded
    base64url (and may carry more ``;key=value`` parameters), which
    firebase-messaging 0.4.x decodes as-is and fails with "Incorrect padding",
    shutting the whole push client down.
    """
    value = value.split(";", 1)[0].strip()
    return value + "=" * (-len(value) % 4)


class _PaddedFcmPushClient(FcmPushClient):
    """FcmPushClient that tolerates unpadded crypto-key and salt values."""

    @staticmethod
    def _decrypt_raw_data(
        credentials: dict[str, dict[str, str]],
        crypto_key_str: str,
        salt_str: str,
        raw_data: bytes,
    ) -> bytes:
        return FcmPushClient._decrypt_raw_data(
            credentials, _b64_field(crypto_key_str), _b64_field(salt_str), raw_data
        )


def cook_state_payload(message: Any) -> dict[str, Any] | None:
    """Extract the cook-state mapping from a Firebase data message."""
    if not isinstance(message, dict):
        return None
    data = message.get("data", message)
    if not isinstance(data, dict):
        return None
    if "deviceId" in data:
        return data
    for key in PUSH_NESTED_PAYLOAD_KEYS:
        value = data.get(key)
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                return dict(json.loads(value))
            except (json.JSONDecodeError, TypeError, ValueError):
                _LOGGER.debug("Could not decode the %s push payload", key)
                return None
    return None


def token_from_credentials(credentials: Any) -> str | None:
    """Return the FCM registration token held by a credentials mapping."""
    try:
        token = credentials["fcm"]["registration"]["token"]
    except (KeyError, TypeError):
        return None
    return token if isinstance(token, str) else None


async def _close_register(client: FcmPushClient) -> None:
    """Close the session a rejected check-in leaves behind."""
    register = getattr(client, "register", None)
    if register is not None:
        await register.close()


class ThermomixPushReceiver:
    """Receives live cook state for the appliances of a Cookidoo account.

    The FCM credentials are worth persisting: reusing them keeps the same push
    token across restarts instead of leaving a new one registered every time.
    """

    def __init__(
        self,
        cookidoo: Cookidoo,
        on_push: PushCallback,
        *,
        mobile_app_id: str,
        session: ClientSession | None = None,
        credentials: dict[str, Any] | None = None,
        on_credentials: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        """Initialise the receiver."""
        self._cookidoo = cookidoo
        self._on_push = on_push
        self._mobile_app_id = mobile_app_id
        self._session = session
        self._credentials = credentials
        self._on_credentials = on_credentials
        self._client: FcmPushClient | None = None
        self._token: str | None = None
        self._registered_token: str | None = None
        self._reregister_task: asyncio.Task[None] | None = None

    @property
    def credentials(self) -> dict[str, Any] | None:
        """The current FCM credentials, for persistence."""
        return self._credentials

    @property
    def token(self) -> str | None:
        """The current FCM registration token. ``None`` until started."""
        return self._token

    @property
    def is_connected(self) -> bool:
        """Whether the receiver is logged in and able to receive pushes."""
        return self._client is not None and self._client.is_started()

    async def start(self) -> None:
        """Connect to FCM and register the token for cook-state pushes.

        Raises CookidooAuthException / CookidooRequestException /
        CookidooParseException from the registration, RuntimeError when the
        Firebase check-in keeps failing.
        """
        client = _PaddedFcmPushClient(
            self._handle_message,
            FcmRegisterConfig(
                project_id=FCM_PROJECT_ID,
                app_id=FCM_APP_ID,
                api_key=FCM_API_KEY,
                messaging_sender_id=FCM_SENDER_ID,
            ),
            self._credentials,
            self._handle_credentials,
            http_client_session=self._session,
        )
        self._token = await self._checkin(client)
        await client.start()
        # Only now is there something to shut down.
        self._client = client
        await self._register(self._token)

    async def _checkin(self, client: FcmPushClient) -> str:
        for attempt in range(1, FCM_CHECKIN_ATTEMPTS):
            try:
                return await client.checkin_or_register()
            except RuntimeError as exc:
                _LOGGER.debug("Firebase check-in attempt %s failed: %s", attempt, exc)
                await _close_register(client)
                await asyncio.sleep(FCM_CHECKIN_RETRY_DELAY_S)
        return await client.checkin_or_register()

    async def stop(self) -> None:
        """Stop listening and drop the push token registration."""
        if self._reregister_task is not None:
            self._reregister_task.cancel()
            self._reregister_task = None
        if self._registered_token is not None:
            try:
                await self._cookidoo.unregister_push_token(self._registered_token)
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Unregistering the push token failed", exc_info=True)
            self._registered_token = None
        if self._client is not None:
            await self._client.stop()
            self._client = None

    async def _register(self, token: str) -> None:
        await self._cookidoo.register_push_token(token, self._mobile_app_id)
        self._registered_token = token

    def _handle_message(
        self, message: dict[str, Any], persistent_id: str, context: Any = None
    ) -> None:
        payload = cook_state_payload(message)
        if payload is None:
            _LOGGER.debug("Ignoring a push without cook state: %s", sorted(message))
            return
        try:
            activity = cooking_activity_from_push(_strip_degrees(payload))
        except (ValueError, TypeError, KeyError):
            _LOGGER.warning("Could not decode a cook-state push: %s", payload)
            return
        self._on_push(activity, dict(payload))

    def _handle_credentials(self, credentials: dict[str, Any]) -> None:
        """Persist rotated FCM credentials and re-register a rotated token."""
        self._credentials = credentials
        if self._on_credentials is not None:
            self._on_credentials(credentials)
        token = token_from_credentials(credentials)
        if token is not None and token != self._registered_token:
            self._token = token
            self._reregister_task = asyncio.create_task(self._async_reregister(token))

    async def _async_reregister(self, token: str) -> None:
        try:
            await self._register(token)
        except Exception:  # noqa: BLE001
            _LOGGER.warning("Re-registering the rotated Thermomix push token failed")
            _LOGGER.debug("Re-registration traceback", exc_info=True)
