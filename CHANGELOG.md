# Changelog

All notable changes to `ray9/twilio` and `ray9/twilio-spend-airlock`.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Semantic Versioning: a new *command* is a minor bump, a changed *input or
output schema* is a major bump, because the airlock and any dependent
workflow read those schemas.

## [1.0.0] — 2026-08-31

First release. Thirty-eight commands over the Twilio REST API from one
credential: twenty read-only, eighteen approval-gated, twelve flagged
irreversible — which in this module means the money is not recoverable.

### Added — the cost pipeline
- Every priced command resolves the destination country (free Lookup v2),
  prices it against Twilio's own Pricing API, computes the exact billable
  quantity, and only then compares against `expected_max_cost_usd`. A
  refusal is arithmetic, not an estimate.
- A ceiling that cannot be evaluated is a **refusal, never a pass** — an
  unpriceable destination is exactly the one worth stopping.
- `price_message` and `price_call` expose the whole calculation as read-only
  commands, so an approver sees the real figure before approving anything.

### Added — segment arithmetic
- `_segments()` implements the real GSM 03.38 rules: 160 characters in a
  single GSM-7 message, 153 per concatenated segment, and **70 / 67** once
  any character forces UCS-2. Escape-table characters (`^{}\[~]|€`) bill as
  two units; an astral emoji is two UTF-16 code units, not one.
- `expected_segments` refuses a message that would split differently from
  the one approved — the guard against a pasted curly quote tripling a cost.

### Added — toll-fraud controls
- `allowed_countries` on every command that reaches a phone.
- Error 21408 is glossed as a **geo-permission** setting rather than a bad
  number, because nothing in Twilio's own message says so.

### Added — reads
- `verify_credential` (reports `is_trial` and which credential is in use),
  `get_balance`, `get_usage`
- `price_message`, `price_call`, `lookup_number`
- `list_messages`, `get_message`, `list_calls`, `get_call`
- `list_recordings`, `get_recording`
- `list_phone_numbers`, `search_available_numbers` (with the monthly price,
  which the Twilio resource itself omits)
- `list_messaging_services`, `list_conversations`, `get_conversation`,
  `list_conversation_messages`, `list_studio_flows`, `get_studio_execution`

### Added — writes
- `send_sms`, `send_whatsapp`, `delete_message_record`
- `place_call`, `hangup_call`, `delete_recording`
- `buy_phone_number`, `release_phone_number`, `update_phone_number`
- `start_verification`, `check_verification`
- `create_conversation`, `send_conversation_message`,
  `add_conversation_participant`, `remove_conversation_participant`,
  `delete_conversation`
- `trigger_studio_flow`, `stop_studio_execution`

### Added — confirmation flags
- `confirm_recurring_charge` on `buy_phone_number`, the only command here
  that keeps charging after it runs
- `confirm_recording_consent` on a recorded call — all-party consent is a
  legal requirement in many jurisdictions
- `confirm_unrecoverable` on `release_phone_number` — the number returns to
  the pool and cannot be reclaimed
- `confirm_deletes_messages` on `delete_conversation`
- `confirm_may_spend_externally` on `trigger_studio_flow`

### Added — the honestly unenforceable limit
- `trigger_studio_flow` **refuses** `expected_max_cost_usd` rather than
  accepting a ceiling it cannot honour, and returns `cost_is_unbounded:
  true`. A Studio flow sends and calls inside Twilio, outside every guard
  in this module.

### Added — transport
- Form-encoded write bodies with repeated keys for arrays. Twilio returns
  JSON but accepts only `application/x-www-form-urlencoded` on writes, and a
  JSON body fails with a confusing 400 about missing parameters.
- API keys (`SK…`) preferred over the account auth token; half a key pair is
  refused rather than silently falling back.
- Credential redaction on every raised error; errors name the request path,
  never a full URL.
- Bounded 429 retry honouring `Retry-After`. **A 5xx is never retried** —
  there the message may already be queued, and a replay would bill twice.
- E.164 validated locally, since Twilio's own error for a missing `+` is a
  generic "not a valid phone number".

### Added — workflow
- `ray9/twilio-spend-airlock` — prices the message, reads the balance, and
  puts a **dollar figure** in front of a human rather than a command name,
  then sends carrying the priced ceiling and segment count.

### Known limits, stated rather than hidden
- WhatsApp is billed per 24-hour conversation, which the Pricing API does
  not expose; the ceiling is checked against the destination's SMS price as
  a floor and the receipt sets `cost_is_approximate: true`.
- Verify's per-attempt surcharge is likewise not exposed; only the
  underlying SMS or voice price is checked.
