Governed Twilio messaging, voice and telephony spend — 38 commands.

SMS, MMS and WhatsApp. Place and end calls, read logs and recordings. Verify OTP flows. Search, buy, configure and release numbers. Conversations and Studio.

**Twenty are read-only.** Eighteen approval-gated, twelve irreversible — which here means *you cannot get the money back*.

**The headline guard is a live-priced ceiling.** Destination prices vary more than fiftyfold. Every send and call resolves the country, prices it against **Twilio's own Pricing API**, computes the exact billable quantity, and refuses over `expected_max_cost_usd`. A refusal is arithmetic, not opinion.

**Segment maths is where the money leaks.** An SMS bills per 160-character segment — but only in GSM-7. One character outside it (an emoji, a curly quote, an accent) switches the *whole message* to UCS-2, where a segment is **70 characters**. A 100-character message costs one segment; plus one emoji, two. `price_message` shows the true count before you approve.

**`allowed_countries` is the toll-fraud guard.** The attack is not stealing your credential — it is getting your system to send to numbers the attacker earns revenue from. An allowlist turns an open-ended bill into a refusal.

**Three carry their own confirmation.** `buy_phone_number` is the only command that keeps charging after it runs (`confirm_recurring_charge`). Recording needs `confirm_recording_consent` — consent is a legal question. `release_phone_number` needs `confirm_unrecoverable`; the number returns to the pool in minutes.

**One limit is honestly declared unenforceable.** `trigger_studio_flow` runs a flow that sends and calls inside Twilio, outside every guard here. It returns `cost_is_unbounded: true` and *refuses* a ceiling rather than imply one it cannot honour.

API keys preferred over the account auth token. Network pinned to Twilio's hosts; subprocess and file writes denied.

Pairs with the free `ray9/twilio-spend-airlock` workflow.
