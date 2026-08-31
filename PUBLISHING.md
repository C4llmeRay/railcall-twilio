# Publishing `ray9/twilio`

Two listings ship from this repo, **both free**:

| Listing                      | Type     | Price            | Source            |
| ---------------------------- | -------- | ---------------- | ----------------- |
| `ray9/twilio`                 | module   | free (`--price=0`) | the staging dir |
| `ray9/twilio-spend-airlock` | workflow | free (`--price=0`) | `workflow/twilio-spend-airlock.json` |

`$STAGE` below is `C:/Users/samib/railcall-modules/twilio`. `railcall` is
`python C:/Users/samib/.railcall/railcall_cli.py`.

---

## 0. State of this release

**No command has been executed against a live Twilio workspace yet.** All 23
are spec-derived and covered by mocks. On zernio the live pass found ten real
bugs that the mock suites could not see — including a publish that returned
HTTP 200 while publishing nothing — so treat the live acceptance run in
TESTING.md §4 as required before anyone depends on this, not optional.

`tools/live_acceptance.py` drives the whole set against a scratch channel and
cleans up after itself:

```bash
python tools/live_acceptance.py --channel C0XXXXXXX --reads-only   # safe first pass
python tools/live_acceptance.py --channel C0XXXXXXX                # includes writes
```

Confirmed on this machine:

- Local publisher key `83454016a786db1218de8f90efb3ec26e23162686f9624ac23079be09b0aa1e6`
  **matches** `module.json`'s `publisher_pubkey`, so `module sign` needs no
  `--force`.
- Marketplace session is live for `chaaliaramy@gmail.com`.
- `LISTING.md` is inside the 40..2000 character `--description` bound.
  `tests/test_docs.py` enforces it, along with the manifest and workflow
  descriptions, so a publish is never aborted by prose nobody measured.
- `license_required` is **false**. It gates loading behind a marketplace
  entitlement, and publishing does not grant yourself one — a free module with
  it set to true cannot be loaded even by its own author. `test_docs.py`
  enforces this too.

---

## 1. Run the offline suites

Never sign a bundle that has not been tested.

```bash
cd C:/Users/samib/railcall-modules/railcall-twilio
python tests/test_schema.py     # 38/38
python tests/test_guards.py     # 51/51, 0 failed
python tests/test_docs.py
python tests/test_workflow.py
```

---

## 2. Stage, then sign, in that order

```bash
python tools/stage_bundle.py
railcall market module sign C:/Users/samib/railcall-modules/twilio
```

Staging wipes and rebuilds `$STAGE` from `module/`, then copies in
`LISTING.md` and `workflow/` — both of which `.moduleignore` keeps **out** of
the signed tree. Expect **4 files**: `.moduleignore`, `docs/SETUP.md`,
`handlers/handler.py`, `module.json`. Same shape as the published
`ray9/zernio` and `ray9/odoo` bundles.

`module sign` should report `spec: v2 (tree)` and `files: 4`.

### Any edit to `module/` invalidates the signature

**Sign last.** If you touch anything under `module/`, re-stage and re-sign.
`stage_bundle.py --verify` diffs the staging dir against `module/` and refuses
a signature that covers stale bytes:

```
STALE STAGING DIRECTORY - the signature below covers OLD bytes:
  handlers/handler.py (repo 81761 B, staged 80565 B)
```

### `module sign` rewrites `module.json` as CRLF

Windows line endings, because the CLI opens the file without `newline=""`.
That is why `.gitattributes` marks `module/module.json` and `module/module.sig`
as `-text`. After signing, copy both back so the repo is byte-for-byte what
was published:

```bash
cp $STAGE/module.json $STAGE/module.sig railcall-twilio/module/
```

---

## 3. Verify — two independent checks

```bash
railcall market module verify C:/Users/samib/railcall-modules/twilio
python tools/stage_bundle.py --verify
```

The first uses the CLI's own recipe. The second is an independent
reimplementation. Both must pass, and `--verify` must not report staleness.

---

## 4. Publish the module — free

```bash
railcall market publish C:/Users/samib/railcall-modules/twilio \
  --type=module \
  --id=ray9/twilio \
  --title=Twilio \
  --category=Ops \
  --price=0 \
  --version=1.0.0 \
  --description=C:/Users/samib/railcall-modules/twilio/LISTING.md
```

Flag notes, each of which is a real trap:

- **`--name=value` form is mandatory.** The CLI matches
  `a.startswith("--" + name + "=")` and nothing else. A space-separated
  `--price 0` is silently ignored — harmless at $0, catastrophic at any other
  price.
- `--description` takes a **file path**, not a string.
- `Ops` is a known category. The valid set is `Data`, `Eng`, `Finance`,
  `HR/IT`, `Marketing`, `Ops`, `Revenue`, `Risk`, `Success`, `Support`,
  `Governance`. An unknown one — `Communication`, for instance — does not
  error; it is silently dropped from the browse filters, so the listing
  publishes and is then invisible to anyone browsing by category.
- Before POSTing, the CLI runs a server-side lint and rebuilds the tree to
  re-verify the signature, so an edit between signing and publishing aborts
  rather than shipping a mismatch.

### Price is effectively permanent

`price_cents` is bound into the listing signature —
`listing_type|payload_sha|price_cents|created_at` — and the publisher
dashboard's edit form has no price field. Going from free to paid later means
publishing a **new version** through review. That is the right trade for now:
free gets it in front of people, and the pricing decision stays open.

---

## 5. Publish the workflow — free

```bash
railcall market publish \
  C:/Users/samib/railcall-modules/twilio/workflow/twilio-spend-airlock.json \
  --type=workflow \
  --id=ray9/twilio-spend-airlock \
  --title="Twilio Spend Airlock" \
  --category=Finance \
  --price=0 \
  --version=1.0.0
```

No `--description` — the spec carries its own, and `test_docs.py` checks it is
inside the same 40..2000 bound.

Publish the **module first**. The workflow declares
`module_dependency: {id: ray9/twilio, minimum_version: 1.0.0}`.

---

## 6. After publishing

```bash
railcall market list
railcall market install ray9/twilio
```

A free listing with `license_required: false` installs and loads for anyone,
the publisher included — unlike a paid one, where publishing does not grant
the author an entitlement.

Restart Studio and confirm the boot line reads `✓ ray9/twilio v1.0.0`, and on a
station with the Phase 4b sandbox:

```
[sandbox] ray9/twilio: network gate active — allow: ['twilio.com', '*.twilio.com']
[sandbox] ray9/twilio: subprocess gate CLOSED (ns-scoped)
[sandbox] ray9/twilio: filesystem-write gate active — allow: (none)
```

---

## 7. Pushing the source

The GitHub remote has not been created yet — it will be supplied. Push only
once the live acceptance run in §0 has actually been done — the README and
this file both currently say the module is spec-derived, and that claim has to
stay true in the published history.

---

## Versioning

- a new **command** — minor bump
- a changed **input or output schema** — major bump, because the airlock and
  any dependent workflow read those schemas
- a changed **risk, mode or irreversible flag** — major bump. These decide
  whether a human is asked before something happens.
- handler internals, error messages, docs — patch

Bump `version` in `tools/gen_manifest.py`, re-run it, add a CHANGELOG entry,
then re-stage, re-sign and re-verify: the version is inside the signed
payload, so changing it invalidates the previous signature.
