# railcall-twilio

Source repository for **`ray9/twilio`** — governed Twilio messaging, voice and
telephony spend for RailCall Station, and its companion workflow
**`ray9/twilio-spend-airlock`**.

Thirty-eight commands over the Twilio REST API, from one credential. Twenty
read-only, eighteen approval-gated, twelve flagged irreversible.

---

## Why this one is different

Slack and Telegram can embarrass you. **Twilio bills you.**

Every send, call, verification and phone number is a real charge against a
real balance, and the spread is enormous: a US SMS segment is a fraction of a
cent, some international destinations are over thirty cents, and premium-rate
ranges exist specifically to extract money from systems that send without
looking.

So in this module `risk` tracks money as well as reach, and **irreversible
means "you cannot get the money back"**, not merely "you cannot unsend".

## The cost pipeline

Every priced command runs the same three steps before it spends anything:

1. **resolve the destination country** — Lookup v2, which is free
2. **price it** — Twilio's own Pricing API, current live price
3. **compute the exact billable quantity** — segments for SMS, minutes for voice

and only then compares against `expected_max_cost_usd`. Because the price
comes from Twilio and the quantity comes from the actual payload, a refusal is
arithmetic rather than an estimate.

A ceiling that cannot be evaluated is a **refusal, never a pass** — an
unpriceable destination is exactly the one worth stopping.

## Segments: where the money actually leaks

An SMS bills per segment. A segment is 160 characters — *but only if every
character is in the GSM-7 alphabet*. One character outside it switches the
**entire message** to UCS-2, where a segment is **70 characters**.

| Message | Segments | Why |
| --- | --- | --- |
| 160 plain characters | 1 | fits GSM-7 |
| 161 plain characters | 2 | split segments hold 153, not 160 |
| 100 characters | 1 | GSM-7 |
| 100 characters **+ one emoji** | 2 | UCS-2, 70 per segment |
| `please don't reply` with a curly apostrophe | — | UCS-2 for the whole message |

That last row is the one that bites: a message pasted out of a word processor
carries typographic quotes, and its cost silently triples.

`_segments()` implements the real GSM 03.38 table — including the escape
characters (`^{}\[~]|€`) that bill as two units, and the fact that an astral
emoji is two UTF-16 code units, not one. `price_message` reports the true
count before anyone approves anything, and **`expected_segments`** refuses a
send whose body would split differently from the one that was approved.

## `allowed_countries` — the toll-fraud guard

The attack is not stealing your credential. It is getting your system to send
to numbers the attacker collects revenue from. The defence is a destination
allowlist, not a stronger secret — so every sending command, `place_call`,
`start_verification` and `trigger_studio_flow` accept one.

**Twilio's own geo permissions are separate and will bite you.** By default a
Twilio account cannot send to most countries; error 21408 means *the region is
disabled in the Console*, not that the number is wrong. Nothing in Twilio's
message says so, which is why it is glossed explicitly.

## Commands that carry their own confirmation

The airlock's approval covers "should this happen". These three need a second
answer to a different question:

- **`buy_phone_number`** → `confirm_recurring_charge`. The only command here
  that keeps charging after it runs — a monthly fee that continues until
  somebody releases the number, including on accounts nobody is watching.
- **`place_call` with `record: true`** → `confirm_recording_consent`. Many
  jurisdictions require all-party consent; a recording made without it is both
  a liability and inadmissible. That is a legal question, not a technical one.
- **`release_phone_number`** → `confirm_unrecoverable`. The number goes back to
  the pool and someone else can hold it within minutes. Everything routed to
  it — 2FA codes, published contact numbers — stops working permanently, and
  the people trying to reach it are not told why.

## The limit that is honestly declared unenforceable

`trigger_studio_flow` starts a program living in Twilio's console that can
send messages, place calls, loop and branch — all billed to your account, none
of it visible to this module.

So it does not pretend. It requires `confirm_may_spend_externally`, returns
`cost_is_unbounded: true`, and **refuses an `expected_max_cost_usd` outright**
rather than accepting a ceiling it could not honour. A guard that silently
enforces nothing is worse than no guard.

## Use an API key

The account auth token is the master credential: it can rotate itself, reach
every subaccount, and cannot be revoked without breaking everything else using
it. An API key (`SK…`/secret) is scoped and individually revocable.

This module uses the key whenever both halves are present, refuses half a pair
rather than silently falling back, and reports which credential is in play as
`verify_credential.auth_method`.

## Two Twilio details that break hand-rolled clients

- **Bodies are form-encoded, not JSON.** Twilio returns JSON but accepts only
  `application/x-www-form-urlencoded` on writes; a JSON body fails with a
  confusing 400 about missing parameters. Arrays go as repeated keys
  (`MediaUrl=a&MediaUrl=b`).
- **A 5xx is never retried.** A 429 is answered before Twilio acts, so a
  replay is safe. On a 5xx the message may already be queued for delivery, and
  retrying would send it twice — and bill twice.

## Layout

```
module/                 the shippable, signed bundle
  module.json           generated — do not hand-edit
  handlers/handler.py   all thirty-eight commands
  docs/SETUP.md         ships with the module
tools/
  gen_manifest.py       module.json generator (the spec table lives here)
  gen_workflow.py       companion workflow generator
  stage_bundle.py       stage + preview exactly what the signature covers
  save_credential.py    vault the credential without it touching a shell
  live_acceptance.py    drive the real API — reads and pricing only by default
tests/
  test_schema.py        drives all 38 commands against a mock
  test_guards.py        51 adversarial scenarios, incl. the segment arithmetic
  test_docs.py          docs, counts and marketplace bounds vs. the manifest
  test_workflow.py      validates AND executes the companion workflow
  test_credential.py    the vault script stores what the handler reads
workflow/               its own marketplace listing, not part of the bundle
LISTING.md              marketplace copy, passed via --description at publish
```

## Working on it

```bash
python tools/gen_manifest.py     # after editing the command spec table
python tools/gen_workflow.py
python tests/test_schema.py      # 38/38 commands
python tests/test_guards.py      # 51/51 refusal scenarios
python tests/test_docs.py
python tests/test_workflow.py
python tests/test_credential.py
python tools/stage_bundle.py
```

See **TESTING.md** for the verification procedure — including why the live
pass defaults to costing nothing — and **PUBLISHING.md** for signing and
publishing.
