# Sample dispatch exports

Generated offline by `python -m src.export_samples` from the same engines as the API.
Scenario: date=2025-07-07, h=3, stock_pct=60, crews=40, floor=0, capacity_pct=100, basis=surge, pool=all, plan=allocation, effect=10.
Issue week (data up to) 2025-07-07, forecast week 2025-07-28.

- `sms/`, `whatsapp/`: notices for the top 3 priority wards of each city and its first ward with a supply deficit, plus a `city_<city>.txt` summary. SMS are GSM-7, 1 segment where possible and never more than 2.
- `briefs/`: printable A4 briefs: one per city (with the plain-language method page) and a one-page brief per exported ward. Open in a browser and use Print, then Save as PDF.
- `allocation_plan.csv`: every ward in priority order: supplies required/allocated, crews, flags, beds.
- `overflow_routes.csv`: same-city bed transfers (from, to, patients).
