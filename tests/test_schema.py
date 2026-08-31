# -*- coding: utf-8 -*-
"""Static + smoke checks for the twilio handler.

Execs handler.py in a namespace shaped like the station's module loader,
swaps the transport for a canned-response mock built from Twilio's
documented response shapes, then drives every command in the manifest and
asserts each declared output_schema key is actually produced.

The mock answers at the `_req` seam by (method, path-fragment), which is how
it can serve seven different Twilio hosts from one function.
"""
import json
import os
import sys
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
MOD = os.path.join(_HERE, os.pardir, "module")

src = open(os.path.join(MOD, "handlers", "handler.py"), encoding="utf-8").read()
manifest = json.load(open(os.path.join(MOD, "module.json"), encoding="utf-8"))

ACCOUNT = "AC" + "0" * 32
VAULT = {"account_sid": ACCOUNT, "auth_token": "a" * 32,
         "default_from": "+14155550100"}

ns = {
    "__name__": "railcall_module_twilio",
    "__rc_helpers__": {
        "vault_get": lambda p: VAULT,
        "airlock_payload_hash": lambda c, i: "hash_" + c,
    },
}
exec(compile(src, "handler.py", "exec"), ns)

MESSAGE = {
    "sid": "SM" + "1" * 32, "to": "+14155550123", "from": "+14155550100",
    "body": "release 4.2 is live", "status": "queued", "num_segments": "1",
    "price": "-0.0079", "price_unit": "USD", "error_code": None,
    "error_message": None, "date_sent": "2026-08-31T10:00:00Z",
}
CALL = {
    "sid": "CA" + "1" * 32, "to": "+14155550123", "from": "+14155550100",
    "status": "queued", "duration": "42", "price": "-0.013",
    "price_unit": "USD", "direction": "outbound-api", "answered_by": "human",
    "start_time": "2026-08-31T10:00:00Z", "end_time": "2026-08-31T10:00:42Z",
}
NUMBER = {
    "sid": "PN" + "1" * 32, "phone_number": "+14155550100",
    "friendly_name": "ops", "capabilities": {"sms": True, "voice": True},
    "sms_url": "", "voice_url": "", "status": "in-use",
}
RECORDING = {
    "sid": "RE" + "1" * 32, "call_sid": CALL["sid"], "duration": "42",
    "channels": 1, "status": "completed",
    "date_created": "2026-08-31T10:00:00Z",
}
CONVERSATION = {
    "sid": "CH" + "1" * 32, "friendly_name": "support-1234",
    "unique_name": "sup-1234", "state": "active",
    "date_created": "2026-08-31T10:00:00Z",
}


def fake_req(method, url, params=None, host=None, timeout=30):
    u = url
    # ---- pricing -------------------------------------------------------
    if host == "pricing":
        if "/Messaging/Countries/" in u:
            return {"country": "United States", "iso_country": "US",
                    "price_unit": "USD",
                    "outbound_sms_prices": [
                        {"carrier": "att", "prices": [
                            {"number_type": "mobile", "current_price": "0.0079",
                             "base_price": "0.0079"}]},
                        {"carrier": "verizon", "prices": [
                            {"number_type": "mobile", "current_price": "0.0083",
                             "base_price": "0.0083"}]}]}
        if "/Voice/Numbers/" in u:
            return {"destination_number": "+14155550123", "price_unit": "USD",
                    "outbound_call_price": {"current_price": "0.013",
                                            "base_price": "0.013"}}
        if "/PhoneNumbers/Countries/" in u:
            return {"price_unit": "USD", "phone_number_prices": [
                {"number_type": "local", "current_price": "1.15"},
                {"number_type": "tollfree", "current_price": "2.15"}]}
    # ---- lookups -------------------------------------------------------
    if host == "lookups":
        return {"phone_number": "+14155550123", "valid": True,
                "country_code": "US", "national_format": "(415) 555-0123",
                "line_type_intelligence": {"type": "mobile",
                                           "carrier_name": "AT&T"},
                "validation_errors": []}
    # ---- verify --------------------------------------------------------
    if host == "verify":
        if "VerificationCheck" in u:
            return {"sid": "VE" + "1" * 32, "to": "+14155550123",
                    "status": "approved", "valid": True}
        return {"sid": "VE" + "1" * 32, "to": "+14155550123",
                "channel": "sms", "status": "pending", "valid": False}
    # ---- conversations -------------------------------------------------
    if host == "conversations":
        if "/Participants" in u:
            if method == "POST":
                return {"sid": "MB" + "1" * 32}
            if method == "DELETE":
                return {}
            return {"participants": [
                {"sid": "MB" + "1" * 32, "identity": "",
                 "messaging_binding": {"address": "+14155550123",
                                       "proxy_address": "+14155550100"}}]}
        if "/Messages" in u:
            if method == "POST":
                return {"sid": "IM" + "1" * 32,
                        "body": (params or {}).get("Body", ""),
                        "author": "system", "index": 1}
            return {"messages": [
                {"sid": "IM" + "1" * 32, "author": "system", "body": "hi",
                 "index": 0, "date_created": "2026-08-31T10:00:00Z"}]}
        if method == "DELETE":
            return {}
        if u.rstrip("/").endswith("/Conversations"):
            if method == "POST":
                return dict(CONVERSATION)
            return {"conversations": [dict(CONVERSATION)]}
        return dict(CONVERSATION)
    # ---- studio --------------------------------------------------------
    if host == "studio":
        if "/Executions" in u:
            return {"sid": "FN" + "1" * 32, "status": "active",
                    "contact_channel_address": "+14155550123",
                    "date_created": "2026-08-31T10:00:00Z",
                    "date_updated": "2026-08-31T10:00:00Z"}
        return {"flows": [{"sid": "FW" + "1" * 32, "friendly_name": "onboard",
                           "status": "published", "revision": 3}]}
    # ---- messaging services -------------------------------------------
    if host == "messaging":
        return {"services": [{"sid": "MG" + "1" * 32,
                              "friendly_name": "main", "use_case": "notify"}]}
    # ---- core API ------------------------------------------------------
    if "/Balance.json" in u:
        return {"account_sid": ACCOUNT, "balance": "42.1900",
                "currency": "USD"}
    if "/Usage/Records.json" in u:
        return {"usage_records": [
            {"category": "sms", "description": "SMS", "count": "12",
             "usage": "12", "price": "0.0948", "price_unit": "USD",
             "start_date": "2026-08-01", "end_date": "2026-08-31"}]}
    if "/Messages/" in u:
        if method == "DELETE":
            return {}
        return dict(MESSAGE)
    if "/Messages.json" in u:
        if method == "POST":
            return dict(MESSAGE, body=(params or {}).get("Body", ""))
        return {"messages": [dict(MESSAGE)], "next_page_uri": ""}
    if "/Calls/" in u:
        return dict(CALL, status="completed")
    if "/Calls.json" in u:
        if method == "POST":
            return dict(CALL)
        return {"calls": [dict(CALL)]}
    if "/Recordings/" in u:
        if method == "DELETE":
            return {}
        return dict(RECORDING)
    if "/Recordings.json" in u:
        return {"recordings": [dict(RECORDING)]}
    if "/AvailablePhoneNumbers/" in u:
        return {"available_phone_numbers": [
            {"phone_number": "+14155550199", "friendly_name": "(415) 555-0199",
             "locality": "San Francisco", "region": "CA",
             "capabilities": {"sms": True, "voice": True}}]}
    if "/IncomingPhoneNumbers/" in u:
        if method == "DELETE":
            return {}
        return dict(NUMBER)
    if "/IncomingPhoneNumbers.json" in u:
        if method == "POST":
            return dict(NUMBER)
        return {"incoming_phone_numbers": [dict(NUMBER)]}
    if u.endswith(".json") and "/Accounts/" in u:
        return {"sid": ACCOUNT, "friendly_name": "Ray9",
                "status": "active", "type": "Full",
                "date_created": "2026-01-01T00:00:00Z"}
    raise AssertionError("unmocked Twilio call: %s %s (host=%s)"
                         % (method, u, host))


ns["_req"] = fake_req

TO = "+14155550123"
INPUTS = {
    "verify_credential": {},
    "get_balance": {},
    "get_usage": {},
    "price_message": {"to": TO, "body": "release 4.2 is live"},
    "price_call": {"to": TO, "minutes": 3},
    "lookup_number": {"phone_number": TO},
    "list_messages": {"limit": 10},
    "get_message": {"message_sid": MESSAGE["sid"]},
    "list_calls": {"limit": 10},
    "get_call": {"call_sid": CALL["sid"]},
    "list_recordings": {"limit": 10},
    "get_recording": {"recording_sid": RECORDING["sid"]},
    "list_phone_numbers": {},
    "search_available_numbers": {"country": "US", "area_code": "415"},
    "list_messaging_services": {},
    "list_conversations": {},
    "get_conversation": {"conversation_sid": CONVERSATION["sid"]},
    "list_conversation_messages": {"conversation_sid": CONVERSATION["sid"]},
    "list_studio_flows": {},
    "get_studio_execution": {"flow_sid": "FW" + "1" * 32,
                             "execution_sid": "FN" + "1" * 32},

    "send_sms": {"to": TO, "body": "release 4.2 is live"},
    "send_whatsapp": {"to": TO, "body": "hello",
                      "from_number": "+14155550100"},
    "delete_message_record": {"message_sid": MESSAGE["sid"]},
    "place_call": {"to": TO, "twiml": "<Response><Say>hi</Say></Response>"},
    "hangup_call": {"call_sid": CALL["sid"]},
    "delete_recording": {"recording_sid": RECORDING["sid"]},
    "buy_phone_number": {"country": "US", "area_code": "415",
                         "confirm_recurring_charge": True},
    "release_phone_number": {"phone_number_sid": NUMBER["sid"],
                             "confirm_unrecoverable": True},
    "update_phone_number": {"phone_number_sid": NUMBER["sid"],
                            "friendly_name": "ops-2"},
    "start_verification": {"service_sid": "VA" + "1" * 32, "to": TO},
    "check_verification": {"service_sid": "VA" + "1" * 32, "to": TO,
                           "code": "123456"},
    "create_conversation": {"friendly_name": "support-1234"},
    "send_conversation_message": {"conversation_sid": CONVERSATION["sid"],
                                  "body": "hello"},
    "add_conversation_participant": {"conversation_sid": CONVERSATION["sid"],
                                     "phone_number": TO,
                                     "proxy_number": "+14155550100"},
    "remove_conversation_participant": {
        "conversation_sid": CONVERSATION["sid"],
        "participant_sid": "MB" + "1" * 32},
    "delete_conversation": {"conversation_sid": CONVERSATION["sid"],
                            "confirm_deletes_messages": True},
    "trigger_studio_flow": {"flow_sid": "FW" + "1" * 32, "to": TO,
                            "confirm_may_spend_externally": True},
    "stop_studio_execution": {"flow_sid": "FW" + "1" * 32,
                              "execution_sid": "FN" + "1" * 32},
}


def run():
    failures = []
    checked = 0
    for cmd in manifest["commands"]:
        name = cmd["id"].split(".", 1)[1]
        fn = ns.get("twilio_" + name)
        if fn is None:
            failures.append("%s: no handler function twilio_%s" % (name, name))
            continue
        if name not in INPUTS:
            failures.append("%s: no test input defined" % name)
            continue
        try:
            out, _err = fn(dict(INPUTS[name]), None)
        except Exception as e:
            failures.append("%s: raised %s: %s" % (name, type(e).__name__, e))
            traceback.print_exc()
            continue

        checked += 1
        if not isinstance(out, dict) or not out.get("ok"):
            failures.append("%s: did not return ok=True (%r)" % (name, out))
            continue
        if out.get("loaded_from") != "module:twilio":
            failures.append("%s: wrong loaded_from %r"
                            % (name, out.get("loaded_from")))
        for key in cmd["output_schema"]:
            if key not in out:
                failures.append("%s: output_schema promises %r, handler did "
                                "not return it" % (name, key))

    print("drove %d/%d commands" % (checked, len(manifest["commands"])))
    if failures:
        print("\nFAILURES (%d):" % len(failures))
        for f in failures:
            print("  - " + f)
        return 1
    print("every command returned every key its output_schema promises")
    return 0


if __name__ == "__main__":
    sys.exit(run())
