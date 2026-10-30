"""Tests for the vendored push receiver helpers."""

from __future__ import annotations

import json

from custom_components.thermomix.receiver import (
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
