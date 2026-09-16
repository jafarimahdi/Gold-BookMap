# Review: "Live Gold (COMEX) reader" for Rithmic Paper Trading (`async_rithmic`)

**Reviewed:** `gold_depth_reader.py` (script shared 2026-09-04)
**Library checked against:** `async_rithmic` **v1.6.6** (current, released 2026-08-21) — verified directly against the library's source code on GitHub, its Read the Docs documentation, Rithmic's own website, and real user reports in the library's issue tracker.

---

## TL;DR verdict

| Aspect | Status |
|---|---|
| Python code / API usage | ~95% correct — every import, constructor argument, method and event name matches the real library API |
| **Will it connect & stream as-is?** | **Almost certainly not** — for *account/gateway* reasons, not code bugs |

> **UPDATE (2026-09-04, after the first real run):** the run failed exactly as predicted, and the error message
> `Exception: You must specify valid SYSTEM_NAME in the credentials: ['Rithmic Test']`
> is the gateway itself confirming Blocker 1 below: `rituz00100.rithmic.com:443` only serves the
> "Rithmic Test" system. See the addendum at the bottom for the full analysis of the run and the
> conversation with the other AI.

The script is well-written and matches the library's documented API almost perfectly. But it fails at the login step because of three things: a mismatched gateway URL, Rithmic's API access policy for the Paper Trading system, and (possibly) unsigned agreements.

---

## 1. What is verified correct

Checked line-by-line against the library source (`client.py`, `enums.py`, `plants/ticker.py`, `plants/base.py`, protos) at v1.6.6:

| Your code | Verified |
|---|---|
| `RithmicClient(user=…, password=…, system_name=…, app_name=…, app_version=…, url=…)` | exact signature match (`url` is the correct parameter name; the old `gateway=` param was removed in v1.5.0) |
| `connect(plants=[SysInfraType.TICKER_PLANT])` | supported since v1.5.6 ("connect to a subset of plants"); `TICKER_PLANT` exists in the login proto. Level 1, Level 2 order book **and** per-price depth all flow through the ticker plant, so this single plant is sufficient |
| `list_exchanges()` | exists; returns objects with `exchange` + `entitlement_flag` (1 = entitled, 2 = not) |
| `get_front_month_contract("GC", "COMEX")` | exists; returns e.g. `"GCV6"` (root + month code + 1-digit year). Can raise "no data" (rp_code 7) during the maintenance window — the try/except handles this |
| `subscribe_to_market_data(code, exch, DataType.LAST_TRADE \| DataType.BBO)` | `DataType` is an `IntEnum` (LAST_TRADE=1, BBO=2, ORDER_BOOK=4), OR-combining is the documented pattern |
| `client.on_tick += …`, `client.on_order_book += …`, `client.on_market_depth += …` | all three events exist; ticks arrive as dicts (snake_case keys: `trade_price`, `bid_price`, `presence_bits`, `datetime`, …); order-book messages arrive as **raw protobuf** (the docstring says this correctly) |
| Presence-bit filtering in `on_tick` | correct and important — Rithmic also sends volume-only LAST_TRADE updates (`presence_bits=8`); the filter drops them |
| `request_market_depth` / `subscribe_to_market_depth` / `unsubscribe_from_market_depth` | all exist (since v1.5.4) |
| Auto-reconnect + auto-resubscribe | the library re-establishes market-data subscriptions after a connection drop (since v1.4.3) — good for an unattended robot |
| SSL | the library bundles Rithmic's SSL cert; nothing to configure |

Requires **Python >= 3.10** (library requirement). The user runs Python 3.12 — fine.

---

## 2. Blocker 1 — Wrong gateway URL for your account  *(CONFIRMED by the run)*

`rituz00100.rithmic.com:443` is **not** a Paper Trading address — it is the **"Rithmic Test"** system gateway:

- The library's own docs pair it with `system_name="Rithmic Test"` and comment it *"Example: test gateway only"*.
- The library's connect sequence asks the gateway which systems it serves (`RequestRithmicSystemInfo`, template 16). The gateway `rituz00100.rithmic.com:443` answers `system_name=['Rithmic Test']` — which is precisely the error the user got on the real run.
- The library **used to ship** a hardcoded gateway list (removed in v1.5.0). From the v1.2.4 source:

```python
class Gateway(enum.Enum):
    TEST         = "rituz00100.rithmic.com:443"    # <- Rithmic Test (dev/conformance system)
    CHICAGO      = "rprotocol.rithmic.com:443"     # <- production connection point
    FRANKFURT    = "de.rithmic.com:443"            # <- regional production points
    IRELAND      = "ie.rithmic.com:443"
    TOKYO        = "jp.rithmic.com:443"
    SINGAPORE    = "sg.rithmic.com:443"
    HONGKONG     = "hk.rithmic.com:443"
    SYDNEY       = "au.rithmic.com:443"
    MUMBAI       = "in.rithmic.com:443"
    SEOUL        = "kr.rithmic.com:443"
    SAO_PAULO    = "br.rithmic.com:443"
    CAPETOWN     = "za.rithmic.com:443"
    COLO_75      = "colo75.rithmic.com:443"
```

The 14-day free trial account lives on the **"Rithmic Paper Trading"** system, whose connection point is `rprotocol.rithmic.com:443` (Chicago — actually a geo-distributed cluster of ~20 IPs, usable from any region).

> **DNS-verified 2026-09-04:** the *regional* hostnames from the old list above (`de.rithmic.com`, `jp.rithmic.com`, `sg.rithmic.com`, `hk.`, `au.`, `br.`, `in.`, `kr.`, `za.`, `ie.`, `colo75.`) **no longer resolve in public DNS** — they were decommissioned. Only `rituz00100.rithmic.com` (Test) and `rprotocol.rithmic.com` (production) resolve. A TLS probe confirms `rprotocol.rithmic.com:443` is live (TLS 1.3, cert `*.rithmic.com`).

**So the claim made in the other AI's conversation — that the Paper Trading gateway address "isn't published anywhere public" — is incorrect.** The exact library being used shipped these addresses publicly until v1.5.0 (June 2025).

---

## 3. Blocker 2 — Paper Trading API access policy (the big one)

This is the part that kills most "demo + custom Python app" plans:

- Rithmic's own API page: conformance testing is **"Required to connect to production systems (Rithmic 01, Rithmic Paper Trading)"** — Paper Trading is classified as a *production* system. (rithmic.com/products/api-suite)
- The library author, quoting Rithmic: *"Paper Trading environment (simulator) is supported after a conformance process is passed."*
- **Directly relevant real-world report** (async_rithmic issue #24, Aug 2025): a user with **exactly this setup** — 14-day free-trial paper account + custom Python app — connected to `wss://rprotocol.rithmic.com:443` (i.e. the *correct* URL) and got:
  ```
  RithmicErrorResponse: Rithmic returned an error={'rpCode': ['13', 'permission denied'], 'templateId': 11}
  ```
  …**even after passing the conformance test** with an authorized app name. The same account worked fine in off-the-shelf OrderFlow software (those platforms are already conformance-approved). The user's conclusion: free-trial accounts may not be permitted API access at all. The thread ended unresolved.
- The 14-day trial is marketed for **R|Trader Pro and 3rd-party platforms** (Quantower, ATAS, Bookmap, MultiCharts, NinjaTrader, Sierra Chart…). Those apps have passed conformance; their `app_name`s are whitelisted. A custom `app_name="gold_depth_reader"` is not.
- Historical context (2021 email from Rithmic; pricing may have changed): *"R|Protocol API is not available for Demos which have live market data"*; production/paper API use was quoted at **$99.99/month + exchange fees**; the Rithmic **Test** system is free but *"does NOT contain live market data"*.

**Bottom line:** after fixing the URL, the *most likely* next failure is `permission denied` at login — that is Rithmic's access policy, not a code bug.

---

## 4. Blocker 3 — Digital agreements

Even with correct credentials and URL, **API login fails until the account's agreements are signed** — done by logging into R|Trader Pro (or NinjaTrader, which the user already did) once with the demo credentials and accepting everything, choosing "Non-Professional".

---

## 5. Realistic paths forward

**Path A — Prove the code works (free, no conformance):**
1. Email `rapi@rithmic.com` (or the request form at rithmic.com) asking for R|Protocol API access / dev kit for personal use.
2. They send free **Rithmic Test** credentials → run the script with preset TEST (`rituz00100.rithmic.com:443`, system "Rithmic Test").
3. Caveat: the Test system has **no live market data** — it validates connectivity/code, not real gold ticks.

**Path B — Actually stream live GC data with your own code:**
1. Agreements already signed via NinjaTrader login (verify they were accepted).
2. Use the production Paper Trading gateway: `rprotocol.rithmic.com:443` (geo-distributed; works from Europe). The old regional hostnames (de.rithmic.com etc.) are dead in DNS.
3. If you get `permission denied` (rpCode 13) → the conformance/app_name wall. Ask Rithmic: (a) does the free trial include API access? (b) can they authorize your `app_name` via conformance? (c) can they issue Rithmic Test credentials meanwhile.
4. Conformance is typically quick (days); they usually give a 4-char prefix for the `app_name` (e.g. `"abcd:gold_depth_reader"`).

**Path C — Just see the data during the trial, no custom code:** R|Trader Pro / Quantower / ATAS / Bookmap / NinjaTrader with the same credentials (all pre-approved).

**Path D — Personal data robot with less gatekeeping:** Databento, IQFeed, Polygon, Interactive Brokers — different stack, no conformance process.

---

## 6. Operational caveats (once connected)

| Item | Detail |
|---|---|
| Rithmic daily maintenance | 17:20–17:45 ET = **23:20–23:45 Budapest** (Mon–Fri). `get_front_month_contract` can return "no data" (rp_code 7) then |
| Weekend maintenance | Friday night → ~12:00 ET Sunday = **~18:00 Budapest Sunday** |
| CME Globex gold halt | 17:00–18:00 ET = **23:00–00:00 Budapest** daily — no ticks during the halt |
| Trial lifetime | **14 days from signup**, once per signup; then paid subscription |
| Login format | Demo User ID = the signup email, **case-sensitive** |
| Entitlements | Verify `COMEX` shows `entitlement_flag=1` (ENABLED) in `list_exchanges()` output |
| Front month != most liquid | Early Sep 2026: front GC month = Oct (**GCV6**); most volume may already be in Dec (**GCZ6**) around the roll. The function returns nearest-expiry. Pick by volume or subscribe to both. `"MGC"` (Micro Gold) also works |

---

## 7. Code-level fixes (applied in `gold_depth_reader_v2.py`)

1. **Credentials hygiene** — read from environment variables; never hardcode; the demo password was exposed in chats (and equals the username) — change it.
2. **Gateway presets** — TEST / PAPER (`rprotocol.rithmic.com:443`) selected via `RITHMIC_PRESET` env var, with system names matched to each. (v2.1: the Frankfurt preset was removed — `de.rithmic.com` no longer resolves in public DNS.)
3. **BBO `elif` bug** — a BBO message carrying both BID and ASK bits printed only the bid; now two separate `if`s.
4. **Print flooding** — the L2 order book is now aggregated into a local book (handling `CLEAR_ORDER_BOOK` / `BEGIN` / `MIDDLE` / `END` / `SOLO` / `NO_BOOK` update types) with a compact top-of-book summary printed every 5 seconds instead of raw protobuf floods.
5. **Login failure diagnostics** — on failure the script explains the likely cause (system mismatch vs. permission denied vs. maintenance) instead of a bare stack trace.
6. **Maintenance-window awareness** — warns if run during the daily 17:20–17:45 ET maintenance or the Globex halt.
7. **`watch_one_price_level`** — kept, but with warnings that the price must be the current best bid/ask and the subscription goes stale as the market moves.

---

## 8. What could NOT be verified

- No live connection test was possible from this workspace (no external network), so connectivity conclusions come from documentation, source code, and user reports.
- Whether Rithmic *currently* (Sep 2026) allows free-trial accounts to use the API is not officially documented; the only data point (issue #24, Aug 2025) says it failed even after conformance. **Ask Rithmic (`rapi@rithmic.com`).**

---

## Addendum (2026-09-04): analysis of the first real run + the other AI's advice

**The run:**
```
$ python gold_depth_reader.py
Connecting to Rithmic...
Exception: You must specify valid SYSTEM_NAME in the credentials: ['Rithmic Test']
```
**What actually happened (from the library source, `plants/base.py`):** on connect, the library opens a WebSocket, sends `RequestRithmicSystemInfo` (template 16), and the gateway replies with the list of systems it serves. `rituz00100.rithmic.com:443` replied `['Rithmic Test']`; since "Rithmic Paper Trading" wasn't in the list, the library raised before even attempting login. This is a clean, unambiguous confirmation of Blocker 1 — no credentials were even checked yet.

**Scorecard for the other AI's advice in the shared conversation excerpt:**

*Correct:*
- Verifying the account via R|Trader Pro / NinjaTrader first (separates "account works" from "code works") — good advice.
- Signing agreements as Non-Professional — correct.
- After the error: correctly identified rituz00100 as the Test sandbox gateway, and correctly warned **not** to simply switch `SYSTEM_NAME` to "Rithmic Test" (the paper credentials don't exist there, and it wouldn't be the account's real entitlements).
- "Everything else in the script is verified correct" — matches this independent review.

*Incorrect:*
- **"One of the sources I found earlier pairs 'Rithmic Paper Trading' with… rituz00100.rithmic.com:443, so there's a real chance it just works as-is."** — False. No source pairs those two; the run proved it. (The address in the script came from the library docs, where it is explicitly the *Test* gateway example.)
- **"That specific address isn't published anywhere public" / "Rithmic deliberately doesn't publish Paper Trading gateway addresses."** — Incorrect. The `async_rithmic` library itself published the production gateway list (rprotocol.rithmic.com:443, de.rithmic.com:443, …) until v1.5.0, and a real user has since connected to `rprotocol.rithmic.com:443` with a paper-trading account. The Rust library's placeholder template is not the only public source.
- Minor: "if you're on Mac or Linux, use the web version" — Rithmic's own site says web/mobile are **not available to demo users**. (Moot here: the user is on Windows.)

*Missing (the important one):*
- **No mention of the conformance / `app_name` authorization requirement.** The whole conversation frames the problem as "find the right URL and it will work." Evidence (Rithmic's API page, the library author, and issue #24) strongly suggests that even with the correct URL, the next failure will be `rpCode 13, permission denied` because a custom `app_name` must pass Rithmic's conformance before connecting to Paper Trading — and free 14-day trials may not include API access at all. Expect this, recognize it, and take it to `rapi@rithmic.com` rather than debugging the script.

**Immediate next step:** change the gateway to `rprotocol.rithmic.com:443` (the production connection point — geo-distributed, works from Europe; the old regional hostnames are dead), keep `SYSTEM_NAME="Rithmic Paper Trading"`, and run again (v2 script does exactly this). Interpret the outcome per section 5, Path B.

---

## Addendum 2 (2026-09-04, evening): run #2 and DNS verification

**Run #2** (v2 script, preset PAPER_FRANKFURT → `de.rithmic.com:443`):
```
socket.gaierror: [Errno 11001] getaddrinfo failed
```
**Diagnosis:** plain DNS failure — the hostname does not exist. No credentials were sent; nothing about the account was tested. The Frankfurt address came from the library's 2024 gateway list and is stale.

**Independent DNS verification (same evening):**

| Hostname | DNS | Notes |
|---|---|---|
| `rprotocol.rithmic.com` | ✅ resolves (~20 IPs across 208.97.247.x, 184.105.22x, 38.65.210.x, 128.177.47.x) | **Live**: TLS 1.3 handshake completes, valid cert `CN=*.rithmic.com`. Geo-distributed production endpoint — use this from any region |
| `rituz00100.rithmic.com` | ✅ resolves (38.79.0.86) | The Test-system gateway (serves `['Rithmic Test']` only) |
| `de.rithmic.com`, `jp.`, `sg.`, `hk.`, `au.`, `br.`, `in.`, `kr.`, `za.`, `ie.`, `colo75.` | ❌ NXDOMAIN — all dead | The 2024 regional gateway list is decommissioned |

**Conclusion:** for "Rithmic Paper Trading" the only publicly resolvable production endpoint is **`rprotocol.rithmic.com:443`**. The v2 script's default preset was corrected accordingly (v2.1).

---

## Addendum 3 (2026-09-04, night): run #3 — the moment of truth

**Run #3** (v2.1 script, preset PAPER → `rprotocol.rithmic.com:443`, system "Rithmic Paper Trading", app_name "gold_depth_reader"):
```
rithmic.plant.ticker - ERROR - ... Rithmic returned an error=
  {'rpCode': ['13', 'permission denied'], 'templateId': 11}
  for the request={... 'system_name': 'Rithmic Paper Trading', 'app_name': 'gold_depth_reader' ...}
```
(followed by a cosmetic library bug: `AttributeError: 'NoneType' object has no attribute 'heartbeat_interval'`)

**Diagnosis:** everything up to and including the login *request* now works — DNS, TLS, gateway handshake, and the system-name check all passed; the request reached the real "Rithmic Paper Trading" login. Rithmic's server itself rejected the login with **rpCode 13 "permission denied"**. This is byte-for-byte the same outcome as issue #24 (free-trial paper account + custom app_name, even after conformance).

**Conclusion:** the script is finished and proven end-to-end at the protocol level. The only remaining blocker is Rithmic's access policy — the `app_name` is not authorized (conformance) and/or free trials do not include API access. Next step: email `rapi@rithmic.com` (draft provided in chat). When/if Rithmic authorizes the account/app (possibly issuing a 4-char `app_name` prefix), set `RITHMIC_APP_NAME` to exactly what they send and re-run — no other changes needed.

**Timeline of the three runs:**
| Run | Gateway | Failed at | Root cause |
|---|---|---|---|
| 1 | rituz00100.rithmic.com:443 (Test) | System-name check | Wrong gateway for a Paper Trading account |
| 2 | de.rithmic.com:443 (old Frankfurt) | DNS | Hostname decommissioned (all 2024 regional names are dead) |
| 3 | rprotocol.rithmic.com:443 (production) | **Login** | rpCode 13 — Rithmic permission policy (app_name/trial not authorized) |

---

## Sources

- Library docs: https://async-rithmic.readthedocs.io/en/latest/ (connection, market data, order book, market depth, conformance)
- Library source: https://github.com/rundef/async_rithmic (`client.py`, `enums.py`, `plants/base.py`, `plants/ticker.py`, `protocol_buffers/source/*.proto`, old `Gateway` enum at tag v1.2.4, `CHANGELOG.md`)
- Issue #24 "About conformance test with account" (free trial → permission denied at rprotocol.rithmic.com): https://github.com/rundef/async_rithmic/issues/24
- Issue #16 (front-month "no data" rp_code 7, gateway matters): https://github.com/rundef/async_rithmic/issues/16
- Issue #42 + #50 (test credentials at rituz00100, conformance process, rapi@rithmic.com): https://github.com/rundef/async_rithmic/issues/42
- Issue #56 (presence_bits=8 volume-only updates; paper trading streaming works for conformance-passed user): https://github.com/rundef/async_rithmic/issues/56
- Rithmic API suite page (conformance required for Rithmic 01 / Paper Trading): https://www.rithmic.com/products/api-suite
- Rithmic Exchange Simulator page (14-day trial, maintenance windows, login=email, live CME data, no web/mobile for demos): https://www.rithmic.com/products/exchange-simulator
- Rithmic demo signup: https://signup.rithmic.com/demo.html
- rithmic-rs (system name defaults: Demo = "Rithmic Paper Trading"): https://github.com/pbeets/rithmic-rs
- Discount Trading (demo = Rithmic Paper Trading system, case-sensitive email login): https://www.discounttrading.com/rithmic-broker.html
- 2021 Rithmic quote on demo API availability & pricing: https://www.reddit.com/r/algotrading/comments/lt6pyf/information_about_r_api_rithmic/
