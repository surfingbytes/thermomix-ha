"""Tests for the vendored push receiver helpers."""

from __future__ import annotations

import json

import base64
import os

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
import http_ece

from custom_components.thermomix.receiver import (
    _b64_field,
    _PaddedFcmPushClient,
    _strip_degrees,
    cook_state_payload,
    token_from_credentials,
)

from .conftest import PUSH_RUNNING


def test_cook_state_payload_shapes() -> None:
    """Flat, nested-object and nested-JSON-string payloads are all accepted."""
    assert cook_state_payload({"data": PUSH_RUNNING}) == PUSH_RUNNING
    assert cook_state_payload(PUSH_RUNNING) == PUSH_RUNNING
    assert cook_state_payload({"data": {"cookingActivity": PUSH_RUNNING}}) == PUSH_RUNNING
    assert (
        cook_state_payload({"data": {"remoteMonitoringInfo": json.dumps(PUSH_RUNNING)}})
        == PUSH_RUNNING
    )
    assert cook_state_payload({"data": {"unrelated": "x"}}) is None
    assert cook_state_payload({"data": {"cookingActivity": "{not json"}}) is None
    assert cook_state_payload("nope") is None


def test_token_from_credentials() -> None:
    """The FCM token is read from the credentials mapping when present."""
    assert token_from_credentials({"fcm": {"registration": {"token": "t"}}}) == "t"
    assert token_from_credentials({"fcm": {}}) is None
    assert token_from_credentials(None) is None


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode()


def test_b64_field() -> None:
    """Base64url fields are cut at their first parameter and padded."""
    assert _b64_field("YQ") == "YQ=="
    assert _b64_field("YWI") == "YWI="
    assert _b64_field("YWJj") == "YWJj"
    assert _b64_field("YQ==") == "YQ=="
    assert _b64_field("YWI;p256ecdsa=xyz") == "YWI="


def test_decrypt_unpadded_push() -> None:
    """A push whose crypto-key and salt come without padding is decrypted."""
    receiver_key = ec.generate_private_key(ec.SECP256R1())
    sender_key = ec.generate_private_key(ec.SECP256R1())
    secret = os.urandom(16)
    salt = os.urandom(16)
    der = receiver_key.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    credentials = {
        "keys": {"private": _b64(der).rstrip("="), "secret": _b64(secret).rstrip("=")}
    }
    public = serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    body = b'{"deviceId": "x", "state": "running"}'
    raw = http_ece.encrypt(
        body,
        salt=salt,
        private_key=sender_key,
        dh=receiver_key.public_key().public_bytes(*public),
        auth_secret=secret,
        version="aesgcm",
    )
    crypto_key = _b64(sender_key.public_key().public_bytes(*public)).rstrip("=")

    assert (
        _PaddedFcmPushClient._decrypt_raw_data(
            credentials, crypto_key, _b64(salt).rstrip("="), raw
        )
        == body
    )


def test_strip_degrees() -> None:
    """Temperatures shown with a degree sign are reduced to the number."""
    payload = {"primaryInfo": "85°", "secondaryInfo": "100 °C", "leadingText": "x°"}
    assert _strip_degrees(payload) == {
        "primaryInfo": "85",
        "secondaryInfo": "100",
        "leadingText": "x°",
    }
    assert _strip_degrees({"primaryInfo": "---"}) == {"primaryInfo": "---"}
    # The original payload is left untouched.
    assert payload["primaryInfo"] == "85°"
