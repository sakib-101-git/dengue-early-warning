# Dengue Watch: 7-minute demo script (Phase 2)

One uncut take that covers all five Phase 2 modules. Every number below is what the app shows for the
default demo week: **data up to 7 Jul 2025, 3 weeks ahead, so the forecast week is 28 Jul 2025**. The
app opens on this week, so no date needs changing.

## Before recording

1. `python -m server` (local, so the offline segment works) and open http://127.0.0.1:8000.
   Use the deployed URL only for segments 1–5. With Wi-Fi off, a browser cannot reach Render.
2. Fresh page state: open `http://127.0.0.1:8000/?view=resources` and use the light theme.
   Defaults: all cities, 60% stock, 40 crews, minimum per ward off, 100% capacity, beds planned for
   a bad week, 10% effect per crew.
3. Keep a terminal open in the repo, and keep `exports/` visible in the file explorer.
4. Check that the chip at the top right says **Offline-ready**. At widths under 1,280 px it is
   a green dot; hover it for the text.

## Timeline

| Time | Segment |
|---|---|
| 0:00–0:30 | Intro and the status chip |
| 0:30–2:00 | 1. Resources: constrained allocation |
| 2:00–3:10 | 2. Hospitals: surge and overflow routing |
| 3:10–4:30 | 3. Simulator: vector-control what-if |
| 4:30–5:45 | 4. Dispatch: SMS, WhatsApp, brief |
| 5:45–6:45 | 5. Offline kill-switch |
| 6:45–7:00 | Wrap-up: exports and docs |

---

### 0:00–0:30 Intro

- **Say:** "Phase 1 forecast dengue cases per ward 2–4 weeks ahead. Phase 2 turns that forecast into
  operations: who gets scarce supplies, where hospital beds run out, where spraying crews prevent
  the most cases, and the notices we send. Everything runs on this machine."
- **Point at** the chip at the top right: **Offline-ready**. Hover it: "forecasts, allocation, routing,
  simulator and dispatch all run on this server, with no internet needed."
- Optional: click **?** next to it for one line per tab; every ⓘ icon explains how a number is made.

### 0:30–2:00 1. Resources (constrained allocation)

1. Click **Resources** in the top bar. The header reads *Supplies · All cities*, *Week of 28 Jul 2025 ·
   60 wards*.
2. **Expect** these headline cards at 60% stock:
   - **Saline bags: 1,344 need.** Getting 806, short 538 in 31 wards.
   - **Test kits: 655 need.** Getting 393, short 262 in 31 wards.
   - **Extra beds: 166 need.** Getting 99, short 67 in 31 wards.
   - **Critical deficits: 30 wards**, + 1 warning (lower risk).
   - Under **Total spraying crews**: **Need 142 · short 102 in 48 wards** (only 40 in the pool).
   - Hover a card's ⓘ for its learned rate: 2.38 saline bags, 1.16 test kits and 0.30 extra beds per
     expected case.
3. **Say:** "Rates are learned from the onsite data on training years only, not hardcoded. Wards are
   served by risk first, then expected cases × a population-density weight."
4. Scroll to **Plan by priority** (each supply column is *getting / need*).
   - Priority #1 is **Ward 04, Dhaka South** (Critical, 14.4 cases, one of the densest wards), fully
     covered.
   - Critical wards fill first.
   - The first shortfall appears at priority 30 (Ward 06, Chattogram: saline 11/20).
   - **Ward 01, Dhaka North** (High, priority 34) gets **0/21** saline and a **CRITICAL DEFICIT** badge.
   - The Crews column shows *assigned / need* in amber where a ward is short of crews (0/2).
5. Drag **Available regional stock** to **100%**: critical deficits drop to **0**, and every coverage
   bar is full.
6. Drag it to **30%**: **47 critical deficits**. Saline: getting 403, short 941 in 48 wards.
7. Set it back to **60%** and point at **Deficit alerts**: High/Critical wards short of anything, in
   priority order (the first 8; *+23 more in the plan*).
8. Optional: under **Minimum per ward**, click **20% each**. Ward 01, Dhaka North now gets 5 saline,
   2 kits and 1 extra bed, and more wards are partly short (47 critical deficits). Set it back to
   **Off**.

### 2:00–3:10 2. Hospitals (surge and overflow routing)

1. Click **Hospitals**. Defaults: All cities, **Plan beds for: A bad week**, **Capacity 100%**.
2. **Expect:** **Bed use 104%**: 232 patients may need a bed against **224** regular beds.
   - **Over capacity:** 10 patients in 9 wards.
   - **Moved:** 2 patients, both inside Chattogram: Ward 07 → Ward 08 and Ward 09 → Ward 03, one
     patient each.
   - **Still without a bed:** 8 patients in 7 wards: 4 in Dhaka North (78 for 74 beds, 105%) and 4 in
     Dhaka South (77 for 73 beds, 105%).
3. **Say:** "Capacity isn't in the data, so it's an assumption that we state. Each ward's regular beds
   are the 95th percentile of what it needed in 2017–2022, at least 2. Overflow only moves within the
   same city, from the largest deficit to the non-Critical ward with the most free beds."
4. Hover the ⓘ on **Regular beds** (chip *Assumed*) to show the stated assumption.
5. Drag **Capacity** to **115%**: 259 regular beds, bed use **90%**.
   - **6 patients moved, 0 still without a bed.**
   - Transfers: Dhaka North Ward 09 → Ward 01, Ward 13 → Ward 02 and Ward 20 → Ward 03; Dhaka South
     Ward 05 → Ward 03 and Ward 17 → Ward 08; Chattogram Ward 09 → Ward 08.
   - Every transfer stays inside its city.
6. Optional: switch **Plan beds for** to **A normal week**: 166 patients for 259 beds (64%), nobody
   over capacity. Set the slider back to **100%** and the toggle back to **A bad week**.

### 3:10–4:30 3. Simulator (what-if vector control)

1. Click **Simulator**. **Effect per crew** is 10%, and no crews are placed yet.
2. **Say:** "A crew removes breeding sites, which we model as a 10% cut in the ward's water-logging
   index per crew, capped at 50%. Then we re-run the real LightGBM models for 2, 3 and 4 weeks
   ahead. It is a model counterfactual, not a fixed percentage."
3. **Best wards for crews**, column *Prevented by 1 crew* (cases one crew prevents over 2–4 weeks;
   the ⓘ calls this the intervention ROI):
   1. **Ward 11, Dhaka South** (DSCC Ward 11), 4.54;
   2. **Ward 06, Dhaka South**, 4.52;
   3. **Ward 03, Gazipur**, 4.32.
4. Click **+** on Ward 06, Dhaka South once: **Cases prevented 5** (−4.5 cases in its *Impact by
   ward* row), 1 bed-week freed, 10 saline bags saved. Click **Clear crews**.
5. Set **Crews to place** to **10**, then click **Place crews**.
   - **Expect:** 6 wards: DNCC Ward 09 ×2, DSCC Ward 06 ×2, DSCC Ward 19 ×2, GCC Ward 03 ×2,
     DSCC Ward 11 ×1 and DSCC Ward 12 ×1.
   - **41 cases prevented**, 14 bed-weeks freed, 95 saline bags saved, **4.1 cases per crew**.
   - Chart *Without vs with crews*: 75.3 → 61.9 (2 weeks), 78.1 → 63.3 (3 weeks) and 79.1 → 66.3
     (4 weeks).
   - *Impact by ward*: Ward 03, Gazipur, Ward 09, Dhaka North and Ward 12, Dhaka South drop
     **Critical → High**. Open a row (⌄) for each week, the water-logging cut and cases per crew.
6. **Say:** "Greedy marginal ROI. Each crew goes where it prevents the most extra cases, and cases
   per crew fall as the best wards are used up: 20 crews prevent 72 cases, 3.6 per crew."

### 4:30–5:45 4. Dispatch (multi-channel notices)

1. Click **Dispatch**. Defaults: Notice for **Ward**, Dhaka North, **Ward 01**, crew impact
   **Allocated crews**. The context chips read *Stock 60% · All cities*, *Crews pool 40* and
   *Beds 100% of regular beds · bad week*.
2. **SMS** tab, 156 characters, **1 segment**:
   `CRITICAL: DNCC Ward 01 dengue, about 9 cases wk of 28 Jul (up to 14). Need 21 saline, 10 kits, 3 beds, none allocated yet. Resupply by 21 Jul. -Dengue Watch`
   - Type one word in the box: the counter updates live (161/306 characters, 2 segments). Delete it
     again.
   - The **Summary** beside the notice shows the flags (High risk, CRITICAL DEFICIT), cases (9, likely
     5 to 14), *Act by 21 Jul 2025* and Need / Getting / Short per supply.
3. **WhatsApp** tab. Point at:
   - *Supplies: need / getting*: Saline bags 21 / 0, test kits 10 / 0, extra (surge) beds 3 / 0, then
     **CRITICAL DEFICIT** with the reason "it went first to 33 wards with more risk or crowding";
   - *Hospital beds*: "Up to 4 patients may need a bed in a bad week, which fills all 4 regular
     beds…";
   - *Please do by 21 Jul*:
     1. Request 21 saline bags, 10 test kits and 3 extra beds from the regional store.
     2. Start door-to-door checks for mosquito breeding sites this week.
     3. Ask the city for 2 spraying crews.
   - Click **Copy text**.
4. **Brief** tab: a one-page A4 brief with In short, Expected cases (9, likely 5 to 14), a Need /
   Getting / Short table, the flag, the bed story, a tick-box action list and sign-off lines. Click
   **Print brief** to show the browser's *Save as PDF*, then cancel.
5. Switch **Notice for** to **City** (Dhaka North).
   - The SMS uses 2 segments: "Dengue alert Dhaka North: about 180 cases expected wk of 28 Jul, 20
     wards at high risk. 11 wards short of supplies and 4 patients may lack a bed…".
   - The city brief adds page 2, *How these numbers were made*, controlled by the **Include method
     page** toggle.
6. **Say:** "Fixed templates, no AI model and no network. The numbers are the same payloads as
   Resources, Hospitals and Simulator, and the tests check that they match."

### 5:45–6:45 5. Offline kill-switch

1. **Turn Wi-Fi off** (or unplug the network) on camera.
2. Wait about 2 seconds. The chip still says **Offline-ready**, with a hollow dot if the browser
   noticed the network drop. Hover it: "No internet connection, and nothing is lost…". If another
   network adapter stays up, the dot stays solid; both mean the same thing.
3. Press **Ctrl+R**. The page reloads fully, because fonts and charts are vendored. Then click
   through:
   - Resources: drag the stock slider;
   - Hospitals: drag capacity;
   - Simulator: Place crews;
   - Dispatch: Brief.

   Everything updates and nothing errors.
4. Open http://127.0.0.1:8000/api/health: `"offline_ready": true`, with every component `true`.
5. In the terminal, still offline:
   - `python -m src.export_samples`, which prints `wrote 75 files to ...exports`;
   - optionally, `python -m pytest -q tests/test_offline.py`: 82 passed in about 5 s, with
     sockets blocked.
6. Turn Wi-Fi back on.

### 6:45–7:00 Wrap-up

- Show `exports/`:
  - `sms/`, `whatsapp/` and `briefs/` (open `briefs/Dhaka_North.html`);
  - `allocation_plan.csv` and `overflow_routes.csv`.
- **Say:** "All five modules run on one laptop with no internet. The README and ARCHITECTURE
  document every formula, assumption and limit."

## If something goes wrong

- **Numbers differ.** Check the URL: `date=2025-07-07`, `h=3`, `stock=60`, `crews=40`, `cap=100`,
  `basis=surge` and `effect=10`. To reset every control, open `http://127.0.0.1:8000/?view=resources`
  in a fresh tab.
- **The chip says Server unreachable.** The browser is pointed at the hosted URL, or the local server
  stopped. Restart `python -m server`.
- **Old UI after an update.** This should not happen: the page loads its stylesheet and scripts by
  content-hashed URLs, so a new build always fetches new files. If it does, press **Ctrl+Shift+R**.
