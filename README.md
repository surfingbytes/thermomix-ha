# Thermomix for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![Validate](https://github.com/surfingbytes/thermomix-ha/actions/workflows/validate.yml/badge.svg)](https://github.com/surfingbytes/thermomix-ha/actions/workflows/validate.yml)
[![GitHub release](https://img.shields.io/github/v/release/surfingbytes/thermomix-ha?include_prereleases)](https://github.com/surfingbytes/thermomix-ha/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Live cooking state of a Thermomix® TM7 (and TM6) in Home Assistant: current
recipe, guided-cooking step (`5/9`), remaining time, temperatures, appliance
messages and an event entity for automations ("step changed", "done", ...).

## Installation

### HACS (recommended)

1. Add this repository to HACS as a custom repository (category *Integration*):

   [![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=surfingbytes&repository=thermomix-ha&category=integration)

2. Install **Thermomix** from HACS and restart Home Assistant.
3. Add the integration and sign in with your Cookidoo® account:

   [![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=thermomix)

### Manual

Copy `custom_components/thermomix` into `config/custom_components/` of your
Home Assistant installation, restart, then add the integration as above.

## Requirements

* Thermomix TM7 (or TM6) linked to your Cookidoo account, online, with
  **Settings → Remote monitoring** enabled on the appliance.
* An active Cookidoo subscription (Vorwerk gates remote monitoring on it).
* Home Assistant 2026.10 or newer.

The core *Cookidoo* integration (shopping list, meal plan) can stay installed;
both share the same `cookidoo-api` release.

## How it works

The Thermomix has no local API for its cook state. With *Remote monitoring*
enabled, the appliance pushes its state through the Cookidoo cloud to the
Cookidoo mobile app as Firebase Cloud Messaging data messages. This
integration registers its own push token with the same backend (via the
unofficial [`cookidoo-api`](https://github.com/miaucl/cookidoo-api) library
for login and token registration, and `firebase-messaging` for the push
connection) and receives exactly what the app's notifications are built from.
The Firebase project identifiers in `receiver.py` are the public ones from the
Cookidoo app's `google-services` resources.

The payload shape was confirmed against the Cookidoo Android app 26.9.14
(`com.cookidoo.android.remotemonitoring.PushNotificationService`):

| push key            | meaning                                   | entity                   |
|---------------------|-------------------------------------------|--------------------------|
| `state`             | running / paused / done / acknowledged    | `sensor.*_cooking_state` |
| `leadingText`       | recipe title                              | `sensor.*_recipe`        |
| `trailingText`      | step indicator, e.g. `5/9`                | `sensor.*_current_step`  |
| `completedDate`     | expected finish of the running step       | `sensor.*_step_finish_time`, `sensor.*_remaining_time` |
| `primaryInfo`       | current temperature (`---` when none)     | `sensor.*_current_temperature` |
| `secondaryInfo`     | target temperature                        | `sensor.*_target_temperature` |
| `messageTitle/Body` | appliance message                         | `sensor.*_message`       |
| `staleDate`         | when the state is no longer trustworthy   | state becomes `stale`    |
| `dismissalDate`     | when the app drops the notification       | state becomes `idle`     |

Vorwerk only pushes steps that run three minutes or longer, plus start,
pause, finish and messages, so very short steps may not show up.

## Entities

Per appliance:

* `sensor` Cooking state (`idle`, `running`, `paused`, `done`, `acknowledged`, `stale`)
* `sensor` Recipe (attributes: `recipe_id`, `recipe_type`)
* `sensor` Current step (attributes: `step_number`, `step_total`)
* `sensor` Remaining time, Step finish time
* `sensor` Current temperature, Target temperature
* `sensor` Message (attributes: `title`, `body`, `criticality`)
* `binary_sensor` Cooking, Push connection (diagnostic)
* `event` Cooking event with types `started`, `step_changed`, `paused`,
  `resumed`, `done`, `acknowledged`, `message`, `stale`, `cleared`, `updated`

Every transition is also fired on the event bus as `thermomix_cooking_update`
with `type` and the full state as data.

## Example automation

```yaml
triggers:
  - trigger: state
    entity_id: event.thermomix_tm7_cooking_event
conditions:
  - condition: template
    value_template: "{{ trigger.to_state.attributes.event_type == 'done' }}"
actions:
  - action: notify.notify
    data:
      message: >
        {{ trigger.to_state.attributes.recipe_name }}: step
        {{ trigger.to_state.attributes.step }} finished
```

## Development

```bash
docker run --rm -v "$PWD":/workspace -w /workspace \
  ghcr.io/home-assistant/home-assistant:stable sh -c \
  "pip install -q --no-deps pytest-homeassistant-custom-component && \
   pip install -q -r requirements_test.txt && python -m pytest -q"
```

## Disclaimer

Thermomix® and Cookidoo® are trademarks of Vorwerk. This project is not
affiliated with or endorsed by Vorwerk and relies on an unofficial API that may
change at any time.
