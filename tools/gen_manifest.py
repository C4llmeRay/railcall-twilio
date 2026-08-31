# -*- coding: utf-8 -*-
"""Generate railcall-twilio/module/module.json from a compact spec table.

Kept as a generator rather than hand-written JSON so the risk/mode/preview
flags stay mechanically consistent across 38 commands — the airlock reads
these, and one command silently missing `receipt_required` is not a typo you
catch by eye.

Every output_schema field below was checked against the documented response
of the Twilio resource the command calls. Nothing is promised that Twilio
does not actually return, either directly or from a lookup the handler
already makes.

WHAT MAKES THIS MODULE DIFFERENT FROM THE OTHERS. Slack and Telegram can
embarrass you. Twilio can bill you. Every send, every call, every
verification and every phone number is a real charge against a real balance,
and the range is enormous — a US SMS segment is a fraction of a cent while
some international destinations are over thirty cents, and premium-rate
ranges exist specifically to extract money from systems that send without
checking.

So `risk` here tracks MONEY as well as reach, and the irreversible flag means
"you cannot get the money back", not merely "you cannot unsend it":

  send_sms / send_whatsapp    irreversible — delivered and billed. There is
                              no unsend and no refund.
  place_call                  irreversible — billed per minute from connect.
  start_verification          irreversible — sends a real SMS or voice call
                              at Verify's own per-attempt price.
  buy_phone_number            irreversible — a RECURRING monthly charge that
                              continues until someone releases it. The only
                              command here that keeps costing after it runs.
  release_phone_number        irreversible — the number goes back to the pool
                              and cannot be reclaimed. Anything routed to it
                              stops working permanently.
  delete_message_record /
  delete_recording            irreversible — destroys the audit trail for a
                              message or call that really happened.
  trigger_studio_flow         irreversible — a Studio flow can send messages
                              and place calls of its own, OUTSIDE every guard
                              in this module. It is the one command whose
                              cost this module cannot bound.

  hangup_call, check_verification, update_phone_number and the conversation
  reads/removals are genuinely undoable and are NOT flagged, so the flag
  keeps meaning something.
"""
import json
import collections

R = "read_only"
W = "write_requires_approval"

C = []


def cmd(name, title, risk, mode, inputs, outputs, irreversible=False):
    C.append((name, title, risk, mode, irreversible, inputs, outputs))


def s(req=False):
    return ("string", req)


def n(req=False):
    return ("number", req)


def b(req=False):
    return ("boolean", req)


def a(req=False):
    return ("array", req)


def o(req=False):
    return ("object", req)


# ── reads ──────────────────────────────────────────────────────────────────
cmd("verify_credential", "Verify Twilio auth and show account status", "low", R,
    {},
    {"valid": "boolean", "account_sid": "string", "friendly_name": "string",
     "status": "string", "type": "string", "is_trial": "boolean",
     "auth_method": "string", "date_created": "string"})

cmd("get_balance", "Read the account's remaining balance", "low", R,
    {},
    {"account_sid": "string", "balance": "number", "currency": "string",
     "as_of": "string"})

cmd("get_usage", "Read spend to date by category", "low", R,
    {"category": s(), "start_date": s(), "end_date": s(), "limit": n()},
    {"records": "array", "count": "number", "total_price": "number",
     "currency": "string", "period": "string"})

cmd("price_message", "Price an SMS before sending it", "low", R,
    {"to": s(True), "body": s(), "from_number": s()},
    {"to": "string", "country": "string", "segments": "number",
     "encoding": "string", "characters": "number",
     "price_per_segment": "number", "estimated_cost": "number",
     "currency": "string", "is_high_cost": "boolean",
     "cheapest_carrier_price": "number"})

cmd("price_call", "Price an outbound call before placing it", "low", R,
    {"to": s(True), "minutes": n()},
    {"to": "string", "country": "string", "price_per_minute": "number",
     "estimated_cost": "number", "currency": "string",
     "is_high_cost": "boolean", "minutes": "number"})

cmd("lookup_number", "Validate a number and read its carrier and type", "low", R,
    {"phone_number": s(True), "include_carrier": b()},
    {"phone_number": "string", "valid": "boolean", "country_code": "string",
     "national_format": "string", "carrier_name": "string",
     "line_type": "string", "is_mobile": "boolean", "billed_lookup": "boolean"})

cmd("list_messages", "List sent and received messages", "low", R,
    {"to": s(), "from_number": s(), "date_sent_after": s(),
     "date_sent_before": s(), "limit": n()},
    {"messages": "array", "count": "number", "total_price": "number",
     "currency": "string", "next_page": "string"})

cmd("get_message", "Read one message and its delivery status", "low", R,
    {"message_sid": s(True)},
    {"message_sid": "string", "to": "string", "from": "string",
     "body": "string", "body_sha256": "string", "status": "string",
     "error_code": "number", "error_message": "string",
     "num_segments": "number", "price": "number", "currency": "string",
     "date_sent": "string"})

cmd("list_calls", "List placed and received calls", "low", R,
    {"to": s(), "from_number": s(), "status": s(), "start_time_after": s(),
     "limit": n()},
    {"calls": "array", "count": "number", "total_price": "number",
     "total_minutes": "number", "currency": "string"})

cmd("get_call", "Read one call's status, duration and cost", "low", R,
    {"call_sid": s(True)},
    {"call_sid": "string", "to": "string", "from": "string",
     "status": "string", "duration_seconds": "number", "price": "number",
     "currency": "string", "direction": "string", "answered_by": "string",
     "start_time": "string", "end_time": "string"})

cmd("list_recordings", "List call recordings", "low", R,
    {"call_sid": s(), "date_created_after": s(), "limit": n()},
    {"recordings": "array", "count": "number", "total_duration": "number"})

cmd("get_recording", "Read one recording's metadata and media URL", "low", R,
    {"recording_sid": s(True)},
    {"recording_sid": "string", "call_sid": "string",
     "duration_seconds": "number", "channels": "number", "status": "string",
     "media_url": "string", "url_requires_auth": "boolean",
     "date_created": "string"})

cmd("list_phone_numbers", "List the numbers this account owns", "low", R,
    {"phone_number": s(), "friendly_name": s(), "limit": n()},
    {"phone_numbers": "array", "count": "number",
     "estimated_monthly_cost": "number", "currency": "string"})

cmd("search_available_numbers", "Search numbers available to buy", "low", R,
    {"country": s(True), "number_type": s(), "area_code": s(),
     "contains": s(), "sms_enabled": b(), "voice_enabled": b(), "limit": n()},
    {"available": "array", "count": "number", "country": "string",
     "monthly_price": "number", "currency": "string"})

cmd("list_messaging_services", "List Messaging Services", "low", R,
    {"limit": n()},
    {"services": "array", "count": "number"})

cmd("list_conversations", "List Conversations", "low", R,
    {"state": s(), "limit": n()},
    {"conversations": "array", "count": "number"})

cmd("get_conversation", "Read one Conversation and its participants", "low", R,
    {"conversation_sid": s(True)},
    {"conversation_sid": "string", "friendly_name": "string",
     "state": "string", "participant_count": "number",
     "participants": "array", "date_created": "string"})

cmd("list_conversation_messages", "Read messages in a Conversation", "low", R,
    {"conversation_sid": s(True), "limit": n()},
    {"conversation_sid": "string", "messages": "array", "count": "number"})

cmd("list_studio_flows", "List Studio flows", "low", R,
    {"limit": n()},
    {"flows": "array", "count": "number"})

cmd("get_studio_execution", "Read one Studio flow execution", "low", R,
    {"flow_sid": s(True), "execution_sid": s(True)},
    {"flow_sid": "string", "execution_sid": "string", "status": "string",
     "contact_channel_address": "string", "date_created": "string",
     "date_updated": "string"})

# ── messaging ──────────────────────────────────────────────────────────────
cmd("send_sms", "Send an SMS or MMS", "high", W,
    {"to": s(True), "body": s(True), "from_number": s(),
     "messaging_service_sid": s(), "media_urls": a(),
     "expected_max_cost_usd": n(), "expected_segments": n(),
     "allowed_countries": a(), "status_callback": s()},
    {"message_sid": "string", "to": "string", "from": "string",
     "body": "string", "body_sha256": "string", "status": "string",
     "num_segments": "number", "encoding": "string",
     "estimated_cost": "number", "price_per_segment": "number",
     "currency": "string", "country": "string"},
    irreversible=True)

cmd("send_whatsapp", "Send a WhatsApp message", "high", W,
    {"to": s(True), "body": s(True), "from_number": s(),
     "media_urls": a(), "content_sid": s(), "content_variables": o(),
     "expected_max_cost_usd": n(), "allowed_countries": a()},
    {"message_sid": "string", "to": "string", "from": "string",
     "body": "string", "body_sha256": "string", "status": "string",
     "estimated_cost": "number", "currency": "string", "country": "string",
     "used_template": "boolean"},
    irreversible=True)

cmd("delete_message_record", "Delete a message from Twilio's logs", "high", W,
    {"message_sid": s(True), "expected_body_sha256": s()},
    {"message_sid": "string", "deleted": "boolean", "was_to": "string",
     "was_body_sha256": "string", "was_price": "number"},
    irreversible=True)

# ── voice ──────────────────────────────────────────────────────────────────
cmd("place_call", "Place an outbound call", "high", W,
    {"to": s(True), "from_number": s(), "twiml": s(), "url": s(),
     "expected_max_cost_usd": n(), "expected_minutes": n(),
     "allowed_countries": a(), "record": b(),
     "confirm_recording_consent": b(), "timeout_seconds": n(),
     "machine_detection": s()},
    {"call_sid": "string", "to": "string", "from": "string",
     "status": "string", "price_per_minute": "number",
     "estimated_cost": "number", "currency": "string", "country": "string",
     "recording_enabled": "boolean"},
    irreversible=True)

cmd("hangup_call", "End an in-progress call", "medium", W,
    {"call_sid": s(True)},
    {"call_sid": "string", "status": "string", "was_status": "string",
     "duration_seconds": "number", "price": "number"})

cmd("delete_recording", "Delete a call recording permanently", "high", W,
    {"recording_sid": s(True), "expected_call_sid": s()},
    {"recording_sid": "string", "deleted": "boolean", "was_call_sid": "string",
     "was_duration_seconds": "number"},
    irreversible=True)

# ── phone numbers ──────────────────────────────────────────────────────────
cmd("buy_phone_number", "Buy a phone number (recurring monthly charge)", "high", W,
    {"phone_number": s(), "country": s(), "area_code": s(),
     "friendly_name": s(), "sms_url": s(), "voice_url": s(),
     "expected_monthly_cost_usd": n(), "confirm_recurring_charge": b()},
    {"phone_number_sid": "string", "phone_number": "string",
     "friendly_name": "string", "country": "string",
     "monthly_cost": "number", "currency": "string",
     "capabilities": "object", "recurring": "boolean"},
    irreversible=True)

cmd("release_phone_number", "Release a number back to Twilio's pool", "high", W,
    {"phone_number_sid": s(True), "expected_phone_number": s(),
     "confirm_unrecoverable": b()},
    {"phone_number_sid": "string", "phone_number": "string",
     "released": "boolean", "reclaimable": "boolean",
     "monthly_cost_ended": "number"},
    irreversible=True)

cmd("update_phone_number", "Change a number's webhooks or friendly name", "medium", W,
    {"phone_number_sid": s(True), "friendly_name": s(), "sms_url": s(),
     "voice_url": s(), "status_callback": s(), "expected_phone_number": s()},
    {"phone_number_sid": "string", "phone_number": "string",
     "updated_fields": "array", "friendly_name": "string"})

# ── verify ─────────────────────────────────────────────────────────────────
cmd("start_verification", "Send a verification code (real SMS or call)", "high", W,
    {"service_sid": s(True), "to": s(True), "channel": s(),
     "expected_max_cost_usd": n(), "allowed_countries": a(),
     "locale": s()},
    {"verification_sid": "string", "to": "string", "channel": "string",
     "status": "string", "country": "string", "estimated_cost": "number",
     "currency": "string", "valid": "boolean"},
    irreversible=True)

cmd("check_verification", "Check a verification code", "medium", W,
    {"service_sid": s(True), "to": s(True), "code": s(True)},
    {"verification_sid": "string", "to": "string", "status": "string",
     "valid": "boolean"})

# ── conversations ──────────────────────────────────────────────────────────
cmd("create_conversation", "Create a Conversation", "medium", W,
    {"friendly_name": s(), "unique_name": s(), "attributes": o()},
    {"conversation_sid": "string", "friendly_name": "string",
     "unique_name": "string", "state": "string", "date_created": "string"})

cmd("send_conversation_message", "Post a message into a Conversation", "high", W,
    {"conversation_sid": s(True), "body": s(True), "author": s(),
     "media_sid": s(), "expected_participant_count": n()},
    {"conversation_sid": "string", "message_sid": "string", "body": "string",
     "body_sha256": "string", "author": "string", "index": "number",
     "participant_count": "number"},
    irreversible=True)

cmd("add_conversation_participant", "Add a participant to a Conversation", "high", W,
    {"conversation_sid": s(True), "phone_number": s(), "identity": s(),
     "proxy_number": s(), "expected_participant_count": n()},
    {"conversation_sid": "string", "participant_sid": "string",
     "phone_number": "string", "identity": "string",
     "participant_count_before": "number", "participant_count_after": "number",
     "is_sms_participant": "boolean"},
    irreversible=True)

cmd("remove_conversation_participant", "Remove a participant", "medium", W,
    {"conversation_sid": s(True), "participant_sid": s(True)},
    {"conversation_sid": "string", "participant_sid": "string",
     "removed": "boolean", "participant_count_after": "number"})

cmd("delete_conversation", "Delete a Conversation and its messages", "high", W,
    {"conversation_sid": s(True), "expected_friendly_name": s(),
     "confirm_deletes_messages": b()},
    {"conversation_sid": "string", "deleted": "boolean",
     "was_friendly_name": "string", "messages_destroyed": "number"},
    irreversible=True)

# ── studio ─────────────────────────────────────────────────────────────────
cmd("trigger_studio_flow", "Start a Studio flow execution", "high", W,
    {"flow_sid": s(True), "to": s(True), "from_number": s(),
     "parameters": o(), "confirm_may_spend_externally": b(),
     "allowed_countries": a()},
    {"flow_sid": "string", "execution_sid": "string", "to": "string",
     "from": "string", "status": "string", "country": "string",
     "cost_is_unbounded": "boolean"},
    irreversible=True)

cmd("stop_studio_execution", "Stop a running Studio execution", "medium", W,
    {"flow_sid": s(True), "execution_sid": s(True)},
    {"flow_sid": "string", "execution_sid": "string", "status": "string",
     "was_status": "string"})


# ── assemble ───────────────────────────────────────────────────────────────
commands = []
for name, title, risk, mode, irrev, inputs, outputs in C:
    entry = collections.OrderedDict()
    entry["id"] = "twilio." + name
    entry["title"] = title
    entry["provider"] = "twilio"
    entry["risk"] = risk
    entry["mode"] = mode
    entry["requires"] = ["TWILIO_AUTH"]
    entry["preview"] = mode != R
    entry["receipt_required"] = True
    if irrev:
        entry["irreversible"] = True
    entry["input_schema"] = collections.OrderedDict(
        (f, collections.OrderedDict([("type", t), ("required", req)]))
        for f, (t, req) in inputs.items()
    )
    entry["output_schema"] = collections.OrderedDict(outputs.items())
    commands.append(entry)

DESC = (
    "Governed Twilio messaging, voice, verification and telephony spend, from "
    "one credential. Thirty-eight commands: send SMS, MMS and WhatsApp; place "
    "and end calls; run Verify flows; buy, configure and release phone numbers; "
    "drive Conversations and Studio flows; and read messages, calls, "
    "recordings, usage and balance. Twenty are read-only. The other eighteen "
    "are approval-gated and twelve are flagged irreversible - here that means "
    "you cannot get the money back, not merely that you cannot unsend. Twilio "
    "bills real money per action and destination prices vary more than fiftyfold, "
    "so the headline guard is a LIVE-PRICED ceiling: every send and call is "
    "priced against Twilio's own Pricing API first, the exact billable segment "
    "count is computed from the message encoding, and expected_max_cost_usd "
    "refuses anything over. That segment maths is where the surprises live - a "
    "161-character message bills as two segments, and a single emoji drops the "
    "limit to 70 characters, so a short message can silently cost triple. "
    "allowed_countries refuses destinations outside an approved list, which is "
    "the defence against premium-rate toll fraud. buy_phone_number is the only "
    "command that keeps charging after it runs and needs its own recurring "
    "confirmation; call recording needs a consent confirmation; and "
    "trigger_studio_flow is flagged cost_is_unbounded because a Studio flow can "
    "spend outside every guard here. Auth is one Twilio credential, API keys "
    "preferred over the account auth token, network is pinned to Twilio's own "
    "hosts, and subprocess and filesystem writes are denied."
)

manifest = collections.OrderedDict()
manifest["id"] = "ray9/twilio"
manifest["name"] = "twilio"
manifest["version"] = "1.0.0"
manifest["provider"] = "twilio"
manifest["credential_spec"] = collections.OrderedDict([
    ("provider", "twilio"),
    ("category", "messaging"),
    ("name", "Twilio"),
    ("required", ["account_sid", "auth_token"]),
    ("optional", ["api_key_sid", "api_key_secret", "default_from",
                  "messaging_service_sid", "base_url"]),
    ("shape", "dict"),
    ("risk", "high"),
    ("read_write", "write"),
])
manifest["description"] = DESC
manifest["requires"] = collections.OrderedDict([
    ("network", ["api.twilio.com", "lookups.twilio.com", "verify.twilio.com",
                 "pricing.twilio.com", "conversations.twilio.com",
                 "studio.twilio.com", "messaging.twilio.com"]),
    ("subprocess", False),
    ("filesystem_writes", []),
])
manifest["commands"] = commands
# Published free (--price=0). license_required gates loading behind a
# marketplace entitlement, which for a free listing buys nothing and breaks
# the publisher's own install — publishing grants no entitlement to the author.
manifest["license_required"] = False
manifest["manifest_version"] = 2
manifest["publisher_pubkey"] = (
    "83454016a786db1218de8f90efb3ec26e23162686f9624ac23079be09b0aa1e6"
)

import os
_HERE = os.path.dirname(os.path.abspath(__file__))
out = os.path.join(_HERE, os.pardir, "module", "module.json")
with open(out, "w", encoding="utf-8", newline="\n") as f:
    json.dump(manifest, f, indent=2, ensure_ascii=False)
    f.write("\n")

reads = sum(1 for c in commands if c["mode"] == R)
irrev = sum(1 for c in commands if c.get("irreversible"))
print("wrote %s" % out)
print("commands: %d  (read_only %d / approval-gated %d / irreversible %d)"
      % (len(commands), reads, len(commands) - reads, irrev))
print("description: %d chars (cap 2000)" % len(DESC))
