# Testing `ray9/twilio`

Four suites run offline against a mocked transport. The live pass is
deliberately structured so the default run **costs nothing**.

```bash
python tests/test_schema.py     # 38/38 commands, output_schema conformance
python tests/test_guards.py     # 51/51 refusal scenarios
python tests/test_docs.py       # docs, counts and marketplace bounds
python tests/test_workflow.py   # validates AND executes the workflow
python tests/test_credential.py # 11/11 — the vault script stores what the handler reads
```

---

## 1. Bundle integrity

```bash
python tools/stage_bundle.py            # stage, and print the signed tree
railcall market module sign ../twilio   # sign it
python tools/stage_bundle.py --verify   # verify without re-staging
```

`--verify` does not re-stage, and diffs the staged bundle against `module/`
so a signature covering stale bytes is caught rather than shipped. Confirm
`workflow/` and `LISTING.md` appear under **EXCLUDED**.

## 2. Schema conformance — `test_schema.py`

Drives all 38 commands against a mock built from Twilio's documented
responses, asserting every `output_schema` key is returned.

The mock dispatches on `(method, path, host)` because this module talks to
**seven Twilio hosts** — api, lookups, verify, pricing, conversations, studio
and messaging. A mock that only understood `api.twilio.com` would pass while
the pricing path was broken.

## 3. The refusals — `test_guards.py`

51 scenarios. The groups, in the order they matter:

### Segment arithmetic — read this group first

Six scenarios, and they are not guards in the usual sense: they are the
calculation every cost ceiling rests on. If this is wrong, every ceiling in
the module is wrong by the same factor.

| Asserted | Why |
| --- | --- |
| 160 chars = 1 segment, 161 = 2 | the basic split |
| 306 = 2, 307 = 3 | concatenated segments hold **153**, not 160 |
| 100 chars + one emoji = 2 segments, UCS-2 | one character re-encodes the whole message at 70/segment |
| a curly apostrophe forces UCS-2 | the silent word-processor cost |
| `{}` costs 4 units | GSM extension characters bill as two each |
| one astral emoji = 2 UTF-16 units | not one |

### The cost ceiling

Refuses over budget; proceeds under it; **applies to the segment count, not
the message** (161 chars at $0.0079 is $0.0158, which a single-segment
assumption would have let through a $0.01 cap); explains *why* a UCS-2
message split; and refuses when the destination cannot be priced at all
rather than passing an unenforceable ceiling.

### Toll fraud and confirmations

`allowed_countries` on send, call and verify. `confirm_recurring_charge`,
`confirm_unrecoverable`, `confirm_recording_consent`,
`confirm_deletes_messages`, `confirm_may_spend_externally`.

**`trigger_studio_flow` refuses an `expected_max_cost_usd` outright** — the
one scenario asserting the module declines to accept a guard rather than
enforce it dishonestly.

### Transport

Form-encoding (not JSON — the classic hand-rolled-client failure), repeated
keys for arrays, auth token never in an error, errors naming the path rather
than the full URL, error 21408 glossed as a *region* setting, 21219 glossed
as a trial account, 429 retried, **5xx never retried**.

## 3b. The credential script — `test_credential.py`

`tools/save_credential.py` shipped as an unmodified copy of the Slack
module's: it prompted for a `bot_token` starting with `xoxb-` and wrote
`bot_token` / `default_channel`, while the handler reads `account_sid`,
`auth_token` and `api_key_sid`. It would have refused every real Twilio
credential.

Nothing caught it because the script is interactive, so no suite touched it.
"Fully tested" had quietly come to mean "every part a test could reach". The
fix is a suite that reaches it: the prompts are driven with scripted input,
so the flow runs headless.

The load-bearing assertion is that **the fields written are exactly those
`module.json` declares** — that one fails on any script copied from another
provider, whatever else looks right. Nine of the eleven scenarios fail
against the original.

The rest cover refusals worth having: half an API key pair (which would
silently fall back to the auth token, defeating the point of making a key),
an `SK…` SID pasted into the account slot, the account SID pasted in twice,
a non-E.164 `default_from`, and that no secret is ever printed.

## 4. Live acceptance — free by default

```bash
python tools/live_acceptance.py                 # reads + pricing only. COSTS NOTHING.
python tools/live_acceptance.py --to +1...      # adds pricing for a real destination
python tools/live_acceptance.py --to +1... --spend   # ACTUALLY SENDS ONE SMS
```

The default run exercises `verify_credential`, `get_balance`, `get_usage`, the
list reads, `price_message` and `price_call`. Pricing and Lookup are free, so
the whole default pass is zero-cost — which means there is no excuse for not
running it.

`--spend` sends exactly one SMS to the number you name, with a ceiling set, and
reports what it cost. It never buys a number, never places a call, never
triggers a Studio flow, and never releases anything. Those are hand-walked
below.

### What to check by hand

1. **`verify_credential`** — is `is_trial` true? On a trial, every send below
   fails with 21219 unless the destination is verified in the Console.
2. **`price_message`** with plain text, then the *same* text plus one emoji.
   The segment count must go 1 → 2 and the encoding GSM-7 → UCS-2. **This is
   the single most valuable live check in the module.**
3. **`send_sms`** with `expected_max_cost_usd` set below the priced cost →
   must refuse, naming both figures.
4. **`send_sms`** with `expected_segments: 1` and a 200-character body → must
   refuse.
5. **`send_sms`** with `allowed_countries: ["US"]` to a non-US number → must
   refuse before spending.
6. **`buy_phone_number`** without `confirm_recurring_charge` → must refuse.
   Only run it *with* the flag if you actually want the number and the
   recurring charge.
7. **`trigger_studio_flow`** with `expected_max_cost_usd` set → must refuse
   with "cannot be honoured".

## 5. What only a real account can tell you

- **Geo permissions.** A destination can be priced perfectly and still fail
  with 21408 because the region is off in the Console. Pricing and permission
  are unrelated systems, and no fixture reproduces that.
- **Trial-account behaviour.** 21219 only appears on a trial.
- **Carrier filtering (30007).** Non-deterministic, and no mock can produce it.
- **Whether a number is actually SMS-capable.** `lookup_number` reports line
  type only if you pay for the carrier package.

## 6. What is NOT covered anywhere

- **WhatsApp pricing.** Twilio bills WhatsApp per 24-hour conversation and the
  Pricing API does not expose it. `send_whatsapp` checks the ceiling against
  the destination's *SMS* price as a floor and returns
  `cost_is_approximate: true`. That is stated rather than hidden, but it is
  weaker than the SMS path and should be treated as such.
- **Verify's own per-attempt fee**, for the same reason — the underlying
  SMS/voice price is checked, the Verify surcharge is not exposed.
- **Studio flow spend**, by design. See `cost_is_unbounded`.

## 7. Before publishing

- [ ] `python tests/test_schema.py` — 38/38
- [ ] `python tests/test_guards.py` — 51/51, 0 failed
- [ ] `python tests/test_docs.py`
- [ ] `python tests/test_workflow.py`
- [ ] `python tests/test_credential.py` — 11/11
- [ ] `python tools/live_acceptance.py` — the free pass, at minimum
- [ ] `python tools/stage_bundle.py --verify` — verifies, no drift
- [ ] no credential anywhere in `git log -p`
