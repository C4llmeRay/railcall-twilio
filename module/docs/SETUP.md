# ray9/twilio — setup

Governed Twilio messaging, voice and telephony spend. Thirty-eight commands,
from one credential.

**Read this section first.** Unlike most integrations, every write here costs
real money. A misdirected send is not just embarrassing, it is billed — and
some destinations cost fifty times others. The whole module is built around
knowing the price before spending it.

---

## 1. Get your Account SID

Twilio Console → dashboard. The Account SID starts with `AC` and is 34
characters. It is not secret, and it is not the credential.

## 2. Create an API key — do not use the auth token

Console → **Account → API keys & tokens → Create API key**. You get an
`SK…` SID and a secret, shown once.

> **Why not the auth token.** The auth token is the master credential for the
> whole account: it can rotate itself, reach every subaccount, and cannot be
> revoked without breaking every other thing using it. An API key is scoped,
> individually revocable, and leaving it behind when someone leaves is a
> five-second fix rather than an incident.

This module uses the API key whenever both halves are present, and reports
which credential is in play as `verify_credential.auth_method`. The auth token
still works if you have no key — it is a fallback, not the recommendation.

## 3. Know whether you are on a trial account

A **trial** account can only send to numbers you have verified in the Console.
Everything else fails with error 21219, which reads like a bad phone number
rather than an account state.

`verify_credential` reports `is_trial` for exactly this reason. Check it
before concluding a number is wrong.

## 4. Turn on the countries you actually send to

Console → **Messaging → Geo permissions** (and the equivalent for Voice).

**Most international destinations are OFF by default.** A send to a disabled
region fails with error 21408 — *permission*, not a bad number. Twilio's own
message does not say this; the module's does.

Leave everything you do not need turned off. It is the cheapest toll-fraud
control available, and it works even if the credential leaks.

## 5. Save the credential

```
python tools/save_credential.py
```

Prompts with no echo, validates the SID shapes, writes the vault entry at mode
0600. Nothing touches your shell history.

| Field                   | Required | Example        | Notes                                              |
| ----------------------- | :------: | -------------- | -------------------------------------------------- |
| `account_sid`           |    ●     | `AC…` (34 ch)  | From the dashboard. Not secret.                     |
| `auth_token`            |    ●     | …              | Required, but only used if no API key is configured |
| `api_key_sid`           |          | `SK…`          | **Preferred.** Must be set with the secret          |
| `api_key_secret`        |          | …              | Shown once at creation                              |
| `default_from`          |          | `+14155550100` | A number you own, in E.164                          |
| `messaging_service_sid` |          | `MG…`          | Optional default sender                             |

Half an API key pair is refused rather than silently ignored — a key SID with
no secret would quietly fall back to the auth token, which is the opposite of
what whoever configured it intended.

## 6. Verify

Run `twilio.verify_credential` — read-only, no approval gate.

```json
{
  "ok": true,
  "valid": true,
  "account_sid": "AC…",
  "status": "active",
  "type": "Full",
  "is_trial": false,
  "auth_method": "api_key"
}
```

Then `twilio.get_balance`. Knowing what is left is the first half of spending
it deliberately.

---

## Numbers must be E.164

`+` then country code then digits. No spaces, no dashes, no parentheses,
no leading zeros from a national format.

```
+14155550123     correct
14155550123      refused — no leading +
+1 (415) 555-0123 accepted (punctuation is stripped) but write it clean
```

Twilio's own error for a malformed number is a generic "not a valid phone
number", which is why this module validates the shape itself and says which
rule was broken.

---

## The cost guards

### `expected_max_cost_usd` — on every send, call and verification

The headline. Before spending, the module resolves the destination country
(free Lookup), prices it against Twilio's Pricing API, computes the exact
billable quantity, and refuses if the total exceeds your ceiling.

Run **`price_message`** or **`price_call`** first to see the real number. They
cost nothing and take the same arguments.

A ceiling that cannot be evaluated is a refusal, not a pass.

### `expected_segments` — on `send_sms`

An SMS bills per segment, and the segment size depends on the *encoding*:

| | Single message | When it splits |
| --- | --- | --- |
| GSM-7 (plain text) | 160 chars | 153 per segment |
| UCS-2 (anything else) | **70 chars** | 67 per segment |

**One character outside GSM-7 switches the entire message to UCS-2.** An
emoji. A curly quote pasted from a document. An accented name. A 100-character
message that cost one segment now costs two.

`price_message` reports `segments` and `encoding`. Pass the segment count you
approved and the send refuses if the text would split differently.

### `allowed_countries` — on every command that reaches a phone

An ISO country allowlist. The toll-fraud attack is not stealing your
credential, it is getting your system to send to numbers the attacker earns
from — so this is the control that matters. Use it alongside geo permissions
(§4), not instead of.

### The confirmation flags

| Command | Flag | Why it is separate |
| --- | --- | --- |
| `buy_phone_number` | `confirm_recurring_charge` | The only command that keeps charging after it runs |
| `place_call` with `record: true` | `confirm_recording_consent` | All-party consent is required in many jurisdictions |
| `release_phone_number` | `confirm_unrecoverable` | The number is gone; someone else may hold it in minutes |
| `delete_conversation` | `confirm_deletes_messages` | Destroys every message in it |
| `trigger_studio_flow` | `confirm_may_spend_externally` | The flow spends outside every guard here |

### What `trigger_studio_flow` does NOT guard

A Studio flow runs inside Twilio and can send and call on its own. This module
cannot see or bound that spend, so it **refuses `expected_max_cost_usd`
outright** rather than accept a ceiling it could not honour. Bound the spend
inside the flow, or use the individual commands.

---

## Common failures

| Code | What it actually means |
| ---- | ---------------------- |
| 20003 | Auth failed — check the SID/token, or the API key pair |
| 21211 | Not valid E.164 — usually a missing `+` |
| 21219 | **Trial account**: the destination is not verified in the Console |
| 21408 | **The REGION is disabled** in geo permissions — not a bad number |
| 21606 | The `From` number is not one you own, or is not SMS-capable |
| 21610 | The recipient replied STOP. You cannot override this from the API |
| 21614 | The `To` number is not a mobile, so SMS cannot reach it |
| 30007 | The carrier filtered the message as spam |
| 63016 | WhatsApp: outside the 24-hour window — use an approved template |

### Rate limits

A 429 is answered before Twilio acts, so nothing happened and a replay is
safe: the module retries up to 3 times honouring `Retry-After`, capped at 45
seconds. **A 5xx is never retried** — there the message may already be queued,
and a retry would send and bill it twice.
