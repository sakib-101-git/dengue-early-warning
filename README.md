# 🦟 Dengue Watch: Urban Dengue Early-Warning System

**EDU AI Engineering Hackathon 2026 — Phase 1: AI for Urban Dengue Outbreak**

Predicts **ward-level dengue risk 2–4 weeks ahead** for 60 wards in Dhaka North, Dhaka South,
Chattogram, Gazipur and Narayanganj. It uses multi-week rainfall, temperature and humidity lags
plus each ward's water-logging, density and sanitation profile. Health officers get a risk tier
(**Low / Medium / High / Critical**), expected cases with an 80% interval, and the weather drivers
behind each forecast, so larvicide and spraying teams can be sent *before* admissions spike.

## Quick start (about 1 minute)

Requires Python 3.10+ (tested on 3.12, 3.13 and 3.14).

```bash
git clone https://github.com/sakib-101-git/dengue-early-warning.git
cd dengue-early-warning
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m server
```

Open http://127.0.0.1:8000 (a browser tab opens automatically). Trained models are already in `models/`, so no training is needed.

Alternative UI (Streamlit):

```bash
pip install -r requirements-streamlit.txt
streamlit run app/streamlit_app.py
```

**Optional:**

```bash
pip install -r requirements-dev.txt   # pytest, xgboost, optuna
python -m pytest -q                   # lag-pipeline tests + API tests
python -m src.train                   # retrain + regenerate models/metrics.json (~3 min)
python -m src.experiments             # ensembles, tuning, neural net vs. deployed model (~3 min)
```

## What the dashboard shows

A custom web app (FastAPI JSON API + hand-written HTML/CSS/JS with ECharts, light and dark themes).
Three views, switched from the top bar; every view's settings live in the URL, so links are shareable:

- **Ward**
  - A rain-to-risk hero: the last 12 weeks of rain hanging from a sky line, the breeding window highlighted, the weeks still ahead, and the forecast week coloured by risk.
  - Expected cases with a likely range, and what actually happened.
  - The factors pushing risk up or down (TreeSHAP).
  - The season's forecast vs. recorded cases.
  - **Simulate weather:** change recent rain, temperature or humidity and see the forecast update instantly.
- **City:** how many wards need action, a ranked list of where to send teams first (click a ward to open it), and a ward × week risk heatmap for the year.
- **Model:**
  - Test accuracy in plain numbers.
  - Error by model and by year.
  - How a forecast is made, in 4 steps.

### Phase 2: operations views
- **Resources (constrained allocation):**
  - Turns each ward's forecast into saline bags, diagnostic kits and hospital beds. Each uses a per-case rate learned from the onsite data.
  - Scarcity controls: an **Available Regional Stock** slider (30–100%), **Total spraying crews**, and an optional 20% minimum-coverage floor. Scope is one city or all 60 wards.
  - Wards are served in priority order. Critical wards are filled first, then the rest by expected cases × population-density weight.
  - Headline cards show required vs. available per supply and the number of **critical supply deficits**.
  - A ranked plan shows coverage bars, crews assigned vs. needed, and CRITICAL DEFICIT / WARNING badges.
- **Hospitals (surge and overflow routing):**
  - Projected bed demand per ward, for expected or surge (upper-forecast) demand, against an assumed capacity. Each ward's baseline is the P95 of its weekly bed need in training years. A **capacity scale** slider (50–200%) adjusts it.
  - Overflow is routed automatically to lower-burden wards **in the same city only**: largest deficit first, to the non-Critical ward with the most free beds.
  - Shows city bed utilisation, overflow before routing, patients routed, **unresolved overflow** and remaining headroom.
  - Per-ward demand-vs-capacity bars and a transfer list (from → to, patients).
- **Simulator (what-if vector control):**
  - Each larvicide/fogging crew cuts a ward's water-logging risk index (10% per crew by default, adjustable 5–20%, capped at 50%). The LightGBM models are re-run on the changed features for 2, 3 and 4 weeks ahead: a model counterfactual, not a fixed percentage.
  - Per-ward crew steppers, or **auto-deploy N crews** by greedy marginal ROI.
  - Shows cases prevented, bed-weeks freed, saline bags saved and **ROI (cases prevented per crew)**, plus a without-vs-with-crews chart for each forecast week and per-ward impact.
  - An **intervention ROI ranking** of every ward by cases one crew prevents.
- **Dispatch (multi-channel notices):**
  - Ward or city notices as **SMS** (GSM-7, 1 segment for most wards and never more than 2, with a live character and segment counter), **WhatsApp** (a short chat message with *bold* section titles and a status dot) and a **printable A4 brief** (Print, then Save as PDF). A ward brief fits on one page; city briefs add a plain-language "How these numbers were made" page (a toggle switches it on or off).
  - Written for non-technical readers: whole numbers ("about 9 cases, likely 5 to 14"), need vs getting for saline, test kits and beds, the official CRITICAL DEFICIT / WARNING flag followed by what it means and *why* the ward is short, one plain story about hospital beds and transfers, and at most 3 actions, each with an amount and a deadline (the Monday before the forecast week). It can also show the simulator's estimate of cases prevented by the assigned crews.
  - Built from fixed templates over the same payloads as Resources, Hospitals and Simulator, so the numbers always match. There is no AI model or network call. Copy and download buttons are included.
  - `python -m src.export_samples` writes sample notices, briefs, `allocation_plan.csv` and `overflow_routes.csv` to [`exports/`](exports/).

## Results (held-out test 2024–2025, never used for training or tuning)

| Horizon | MAE (cases/ward-week) | RMSE | Exact risk tier (of 4) | High-risk recall |
|---|---|---|---|---|
| 2 weeks | 0.995 | 1.99 | 79.8% | 87% |
| 3 weeks | 1.019 | 2.01 | 78.9% | 84% |
| 4 weeks | 1.161 | 2.28 | 77.2% | 86% |

**As a yes/no outbreak alert (≥ 4 cases in a ward-week):** 93% accuracy, 94–95% of outbreaks caught,
ROC-AUC 0.97–0.98 at both 2 and 4 weeks ahead.

This is 1.7× more accurate than the "same week last year" baseline. Ensembles, tuning, neural
networks and per-ward models were also tested, and none beat the deployed model. Full details
are in [ARCHITECTURE.md](ARCHITECTURE.md).

## Project structure

```
data/raw/dengue.csv        dataset provided by the organisers (Phase 1)
data/raw/dengue_onsite.csv Phase 2 dataset: same rows + saline / kits / beds columns
src/config.py              paths, horizons, split years
src/data.py                load + validate
src/features.py            multi-week ward-level lag pipeline
src/tiers.py               Low/Medium/High/Critical cut-offs
src/train.py               model comparison, training, metrics, joblib export
src/predict.py             RiskEngine: forecasts, TreeSHAP drivers, what-if
src/experiments.py         ensembles / tuning / neural-net comparison
src/resources.py           supply rates, ward priority, scarce-stock greedy allocation, crews
src/hospitals.py           assumed bed capacity, ward deficits, greedy intra-city overflow routing
src/simulator.py           vector-control counterfactual: water-logging cut -> model re-run, ROI ranking, auto-deploy
src/dispatch.py            plain-language SMS (GSM-7, 1-2 segments) / WhatsApp / printable HTML brief templates
src/export_samples.py      writes exports/: sample notices, briefs, allocation_plan.csv, overflow_routes.csv
exports/                   committed sample dispatch exports (demo week 2025-07-07, 3 weeks ahead)
server/api.py              FastAPI JSON API over the RiskEngine + serves web/
server/__main__.py         `python -m server` launcher (uvicorn, opens the browser)
web/index.html             app shell (top bar, navigation, theme toggle)
web/styles.css             design system: tokens, light/dark themes, components, motion
web/js/                    ES modules: state/URL, API client, UI kit, ECharts theme, views
web/vendor/echarts.min.js  Apache ECharts, vendored so the app works offline
app/                       alternative Streamlit dashboard (pages, components, Plotly charts, theme)
models/                    model_h{2,3,4}.joblib + metrics.json + resource_rates.json
tests/test_features.py     lag/leakage tests
tests/test_api.py          API tests: every endpoint, city, horizon, date edge and simulation extreme
tests/test_data.py         onsite dataset = Phase 1 dataset + supply columns (forecasts unchanged)
tests/test_resources.py    allocation: rates, priority, conservation, Critical-first, flags, crews, 400s
tests/test_hospitals.py    overflow: capacity rule, no cross-city transfers, sent = received, utilisation maths
tests/test_simulator.py    simulator: rebuild equivalence, 0 crews = baseline, more crews never raise cases, ranking, auto-deploy
tests/test_dispatch.py     dispatch: SMS segment budget for all wards, numbers match allocation/overflow/simulate, flags + reasons, plain wording, offline brief, 400s
```

## Tech stack

- **Language:** Python
- **Data:** pandas, NumPy
- **Models:** LightGBM (Poisson + quantile), scikit-learn (Poisson GLM, Random Forest, MLP, metrics), XGBoost and Optuna (experiments only)
- **Serialisation:** joblib
- **Web app:** FastAPI + uvicorn, vanilla HTML/CSS/JS (no build step), Apache ECharts
- **Alternative dashboard:** Streamlit, Plotly
- **Tests:** pytest

## Team

| Member | Role |
|---|---|
| Syed Nazmus Sakib (Team Leader) | Data pipeline, lag features and leakage tests, FastAPI backend, ARCHITECTURE.md |
| MD Sadman | Model training, chronological validation, risk tiers, experiments, City and Model views, API tests |
| Misbah Uddin | Prediction engine, web frontend (design system, Ward view), Streamlit dashboard, README, demo video |

## AI tools & external APIs disclosure

| Tool / API | Used for |
|---|---|
| Claude (Anthropic, claude.ai) | Planning, exploring the data, drafting code for the feature pipeline, training script, inference engine and dashboard, and drafting documentation. The team reviewed, ran, tested and modified all code and can explain it. |
| Claude Code (Anthropic) | Building the web frontend (server/, web/), API tests and the Streamlit restyle. In Phase 2, drafting the resource allocation, hospital overflow, vector-control simulator and dispatch-notice engines, their APIs, tests, sample exports and the Resources, Hospitals, Simulator and Dispatch views. The team reviewed, ran and tested all changes. |

**External APIs at runtime: none.** The app makes no network calls and uses no LLM.

## Attributions

- Dataset: provided by the EDU AI Engineering Hackathon 2026 organisers.
- Open-source libraries listed in `requirements.txt`. No third-party code snippets are copied into this repository.

## Demo

- Video walkthrough (≤ 3 min, running on localhost): https://youtu.be/XYZo31OLgIU?si=c5v2DkbcPSqrxSiO
- Live demo: https://dengue-watch.onrender.com (free tier, so the first load may take about a minute)
- Run locally: see Quick start above.
