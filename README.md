# CALL-E Dify Plugin

CALL-E tools for Dify workflows and agents.

Maintainer: J.Jason ([@JJasonSun](https://github.com/JJasonSun)).

Source repository: https://github.com/CALLE-AI/call-e-dify-plugin

This plugin exposes safe outbound phone-call tools for CALL-E:

- `create_call` previews or creates one outbound CALL-E call.
- `get_call` fetches one CALL-E call result by call ID.
- `create_and_wait` previews or creates one outbound CALL-E call, then waits for a final result or timeout.
- `list_goals` and `get_goal` read published CALL-E Goals without starting a call.
- `create_goal_run`, `get_goal_run`, and `create_goal_run_and_wait` execute and monitor one phone-specific published Goal.

Phone calls are real-world side effects. Live calls require both `dry_run=false` and `confirm_live_call=true`.

## Requirements

- Python 3.12
- Dify Plugin CLI
- CALL-E API key ([create one in the CALL-E dashboard](https://dashboard.heycall-e.com/account/api-keys))

Install the Dify Plugin CLI by following the official Dify plugin documentation.
For request and response details, see the [CALL-E API reference](https://docs.heycall-e.com/api-reference).

## Provider Credentials

Configure these provider credentials in Dify:

| Credential | Required | Description |
| --- | --- | --- |
| `api_key` | Yes | CALL-E API key. [Create a key](https://dashboard.heycall-e.com/account/api-keys); it is stored as a secret input in Dify. |
| `base_url` | Yes | CALL-E API base URL. Defaults to `https://api.heycall-e.com`. Do not include `/v1` or `/v2`. |

When credentials are saved, the provider performs a non-mutating authentication probe:

```text
GET /v2/calls/call_dify_credential_probe_00000000000000000000000000000000
```

The provider accepts `200` or the documented `404 not_found` response as an authenticated probe. It rejects `401 unauthorized`, `403 forbidden`, redirects, and other errors. The probe does not create a call. The client does not follow redirects with credentials.

## Tools

### create_call

Creates one CALL-E call only when both live-call controls are set:

- `dry_run=false`
- `confirm_live_call=true`

Otherwise it returns a preview with the phone number masked.

Both Call creation tools require a business-stable `idempotency_key` of 1-255 printable ASCII characters, such as `order-8472-confirmation-attempt-1`. Save it with the original inputs before invoking the tool. Retrying the same key and unchanged inputs recovers the same Call; a new key means a new call. The plugin does not generate keys or retry creation automatically.

The plugin sends one top-level `phone` to `POST /v2/calls` with a closed, flat scalar `result_schema`. The built-in schema requests `call_completed`, `outcome`, `summary`, and `next_action`, with optional observation fields. Include missing-answer rules in the task consistent with these fields. Leave `region` and `locale` blank for API inference, and keep them blank on replay even when the response includes inferred values.

`live_call_created=true` reports an accepted resource, including an exact replay. It does not prove a new dial or a successful connection. HTTP creation errors return `error.code`, `error.message`, `error.details`, and the supplied key for recovery.

### get_call

Fetches one result from `GET /v2/calls/{id}`. The parsed response includes `result`, `error`, `call_outcome`, `result_status`, and the top-level `transcript`. `summary` comes from `result.summary`; `structured_result` remains an alias for `result`, including `null` and `{}`.

Call tools return the JSON response and emit named workflow variables for their declared outputs, so later Dify nodes can select `call_id` and `result` without parsing text. The default `json` output remains available for the complete response and recovery details.

For existing Dify variable mappings, this plugin keeps `call_id` as the **API resource ID** used by `get_call`, also available as `id`. `billing_call_id` contains the API response's telephone `call_id`, which can be null. Use that field for Billing correlation, not for API reads. The redacted `raw_call_result` preserves the API field names.

### create_and_wait

Creates one call, then polls while `result_status=pending`. The waiter stops for `available`, `unavailable`, and `not_applicable`. A completed telephone execution can still have a pending result. A final unavailable result can have both `result=null` and `error=null`; it must not trigger an automatic redial.

A wait timeout returns `timed_out=true`, the same `call_id`, and the supplied key. A polling error returns `accepted_resource` with the last saved ID; use `get_call` to resume. A network failure during creation can leave acceptance uncertain: retry only the original key and unchanged inputs. An expired Dify host execution also does not cancel the call. Set the host's plugin execution timeout to cover the requested wait, or use `create_call` followed by `get_call` in later workflow steps.

### Goal tools

`list_goals` and `get_goal` read the published Goal catalog. They do not create a call. Pass the previous page's `next_cursor` unchanged as `after` for the next page.

`create_goal_run` runs one published Goal for one E.164 recipient. The Dify input remains `phone_number`; the client sends it as `phone` to `POST /v1/goals/{goal_id}/runs`. Optional `variables_json` must contain a flat object of finite strings, numbers, or booleans matching the published Goal's input schema. Voice settings and result schemas belong to the published Goal. Save and reuse the same `idempotency_key` and inputs after a network failure.

`create_goal_run_and_wait` uses the same `result_status` readiness rule as Calls and returns the transcript independently of result availability. A timeout does not cancel the Run: use `get_goal_run` with the returned `goal_run_id`. Polling errors retain the accepted resource; uncertain creation requires an exact replay with the original key.

Goal Run tools follow the same live-call boundary as Call tools: live execution requires both `dry_run=false` and `confirm_live_call=true`.

## Upgrade from 0.1.x

Version 0.2.0 moves new Calls to v2. Before upgrading a workflow:

1. Add a durable `idempotency_key` to `create_call` and `create_and_wait`; do not derive it from each retry or workflow execution ID.
2. Check stored `region` and `locale` values. Older nodes may still have explicit `US` and `en-US`; remove them only when you want inference for **new** requests. Preserve original inputs for retries.
3. Replace dependencies on recipient/attempt status, completion confidence, or evidence fields. V2 does not expose these fields. Read business fields from `result`, telephone outcomes from `call_outcome`, and readiness from `result_status`.
4. Rename `list_goals.cursor` to `after`. Goal endpoints remain `/v1/goals/...`.
5. Keep historical v1 Call IDs with the original plugin/API interface. Reading an old ID through v2 does not migrate it. Do not create a replacement because an old ID cannot be read.

In Dify, bind **Idempotency Key** to an upstream field saved with the call request, such as `call_request_id`. The value can be a UUID generated once by your upstream application; it does not have to be an order number or something a user types. Save it before invoking CALL-E and keep it when retrying the node or rerunning the same business event. Generate a new value only for an intentionally new call.

For order 8472's first confirmation call, a saved value such as `order-8472-confirmation-attempt-1` works. Supply that same value and unchanged call inputs on retries. Use `order-8472-confirmation-attempt-2` only after deciding to place a second call. Do not use one fixed key for all calls or generate a fresh UUID on each workflow execution. Missing or invalid keys stop locally without sending a CALL-E request.

Dify provides [plugin KV storage](https://docs.dify.ai/en/develop-plugin/features-and-specs/plugin-types/persistent-storage-kv), but its tool context does not provide a durable logical-call identifier across separate Workflow runs. A plugin session ID changes per invocation, and an app or conversation ID can cover multiple calls. Storage alone therefore cannot tell the plugin which saved key to reuse. This version requires the upstream identifier rather than guessing from the phone number and task. It does not store or generate keys in plugin KV storage.

See the [migration guide](https://docs.heycall-e.com/migration), [Calls contract](https://docs.heycall-e.com/calls), and [Goal Runs contract](https://docs.heycall-e.com/goal-runs).

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
