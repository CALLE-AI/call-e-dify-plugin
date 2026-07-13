# CALL-E Dify Plugin

CALL-E tools for Dify workflows and agents.

Source repository: https://github.com/CALLE-AI/call-e-dify-plugin

This plugin exposes safe outbound phone-call tools for CALL-E:

- `create_call` previews or creates one outbound CALL-E call.
- `get_call` fetches one CALL-E call result by call ID.
- `create_and_wait` previews or creates one outbound CALL-E call, then polls until the call reaches a terminal status or timeout.

Phone calls are real-world side effects. Live calls require both `dry_run=false` and `confirm_live_call=true`.

## Requirements

- Python 3.12
- Dify Plugin CLI
- CALL-E API key ([create one in the CALL-E dashboard](https://dashboard.heycall-e.com/account/api-keys))

Install the Dify Plugin CLI by following the official Dify plugin documentation.
For request and response details, see the [CALL-E API reference](https://docs.heycall-e.com/#/api-reference).

## Provider Credentials

Configure these provider credentials in Dify:

| Credential | Required | Description |
| --- | --- | --- |
| `api_key` | Yes | CALL-E API key. [Create a key](https://dashboard.heycall-e.com/account/api-keys); it is stored as a secret input in Dify. |
| `base_url` | Yes | CALL-E API base URL. Defaults to `https://api.heycall-e.com`. Do not include `/v1`. |

When credentials are saved, the provider performs a non-mutating authentication probe:

```text
GET /v1/calls/call_dify_credential_probe_00000000000000000000000000000000
```

The CALL-E API documents `GET /v1/calls/{call_id}` and the stable error code `not_found`. A documented `404 not_found` response means the API key was accepted and the probe call ID does not exist, so no call is created. `401 unauthorized` and `403 forbidden` are reported as credential configuration errors.

## Tools

### create_call

Creates one CALL-E call only when both live-call controls are set:

- `dry_run=false`
- `confirm_live_call=true`

Otherwise it returns a preview with the phone number masked.

### get_call

Fetches one call result from `GET /v1/calls/{call_id}` and returns masked, parsed status fields.

### create_and_wait

Creates one call, then polls until a terminal status or timeout. This is the most convenient tool for Dify workflows that need one final result object.

## Safety Defaults

- Requires E.164 destination phone numbers, for example `+15555550123`.
- Rejects common placeholder numbers for live calls.
- Masks phone numbers in text and JSON responses.
- Does not create recurring schedules.
- Does not expose API keys in logs or responses.
- Defaults to `dry_run=true`.

For recurring workflows, keep recurrence in the host scheduler. CALL-E should receive exactly one call request per scheduled run.

## Local Development

Create a virtual environment and install dependencies:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

Run the local checks:

```bash
python -m pytest
python -m compileall provider tools utils main.py
```

Run the plugin locally:

```bash
python main.py
```

## Package

From the parent directory:

```bash
dify plugin package ./call-e-dify-plugin
```

The package command creates a `.difypkg` file that can be uploaded to Dify or attached to a GitHub Release.

## Release

1. Update `version` in `manifest.yaml` and `pyproject.toml`.
2. Run tests and compile checks.
3. Package with `dify plugin package ./call-e-dify-plugin`.
4. Create a GitHub Release.
5. Upload the `.difypkg` as a release asset.

## Live Call Boundary

Use live-call tools only for numbers you own or are explicitly authorized to call. Do not use this plugin for emergency services, deception, harassment, or hidden recurring outreach. For medical, legal, financial, or emergency contexts, keep the call task bounded to administrative or informational workflows and avoid professional advice or urgent decision-making.
