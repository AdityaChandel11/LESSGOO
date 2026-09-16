# Data Collection & Trust Layer — Addendum to Build Brief

Extends `federated-health-brief.md`. This covers how raw data actually gets captured from rural PHCs/CHCs (many without internet or biometric hardware) and how the platform verifies that data can be trusted before it feeds the forecasting/redistribution pipeline.

## 1. Personnel attendance — cell-signal geofence, not biometrics

Biometric hardware installed across ~1.6 lakh PHCs isn't a realistic assumption (cost, procurement, maintenance). Instead:
- Health worker checks in/out via a phone call or IVR session at shift start/end, plus random re-verification pings during the day.
- The call/session carries the **serving Cell Tower ID** — available over any 2G connection, no internet or GPS needed — giving an approximate location without any dedicated hardware.
- For fully offline structured input, fall back to **USSD** (same mechanism as `*99#` banking) so a worker can report status from any basic phone with zero data connectivity.
- Cross-check: attendance compared against OPD patient footfall logged the same day/period — staff marked present with zero patients logged for hours is an anomaly, not proof of fraud, but a flag.

## 2. Bed occupancy — daily rotating code + Gemini vision

- Each PHC uploads one photo per ward, daily. A **rotating verification code**, generated server-side each morning and delivered by SMS/IVR, must be visibly placed in the photo (whiteboard/printed slip) — prevents reusing an old photo, since the code is unpredictable and expires daily.
- **Gemini Vision** processes the photo to: count total beds, classify occupied vs. empty, and read the rotating code back out of the image to confirm freshness.
- Location cross-check: photo metadata (device GPS if available, or cell-ID fallback per section 1) is checked against the PHC's registered coordinates via the **Google Maps API**, confirming the photo was taken at the facility.
- Gemini's extracted occupied-bed count is cross-checked against admission/discharge register entries for the same period — mismatches are flagged for review.
- All outputs write directly into the shared database — this is the "Gemini updates the database" loop.

## 3. Medicine stock

**Dispatch side (already solved, replicate the pattern):** when a batch leaves a state warehouse for a PHC, that dispatch — medicine, quantity, destination, timestamp — is logged at the point of dispatch, independent of the PHC. This mirrors how e-Aushadhi already works in production; build the same pattern into this system rather than depending on PHC self-reporting for what arrived.

**Consumption side:**
- Internet-connected PHCs: photograph the bill/register entry; **Gemini** OCRs it, extracting medicine, quantity, date, and deducts from the stock ledger automatically.
- Non-connected PHCs: report usage by voice through **VoiceERA on Bhashini** (multilingual IVR, ~15 languages) — speech is transcribed and parsed, then updates the same ledger.

**New: medicine movement tracking tab** — a dedicated ledger view, not just a stock-level number. Each row is one movement event:

| Field | Description |
|---|---|
| Batch ID | Unique id for the dispatched batch |
| Medicine | Name/SKU |
| Quantity dispatched | From warehouse records |
| Source → Destination | Warehouse/PHC → PHC/district |
| Dispatch timestamp | Logged at source |
| Receipt confirmation timestamp | Logged when PHC confirms |
| Quantity received (confirmed) | From PHC-side confirmation |
| Discrepancy flag | Auto-set if dispatched ≠ received |
| Status | In-transit / received / discrepancy |

This two-sided ledger (dispatch record + independent receipt confirmation) is itself a cross-reference — a batch that's "dispatched" for weeks with no receipt confirmation, or a receipt quantity that doesn't match dispatch, surfaces automatically instead of only being visible during a manual audit.

## 4. Trust layer (four layers, applied across all three data types)

1. **Raise the cost of faking** — rotating codes, geofencing, dispatch/receipt pairing. Stops casual fraud, not determined fraud.
2. **Cross-reference independent signals** — attendance vs. footfall vs. bed occupancy vs. medicine consumption. A PHC with high reported occupancy and consumption but near-zero attendance is a strong, hard-to-fake anomaly, since faking all three consistently is much harder than faking one.
3. **Statistical outlier detection at the aggregator** — PHCs whose numbers are implausibly smooth (never zero beds, suspiciously round consumption, zero absences ever) get auto-flagged.
4. **Targeted audit escalation** — flagged PHCs surface on a District Health Officer view for prioritized physical audit, replacing blind trust or random sampling with the same kind of check courts have had to order manually elsewhere.

## 5. Where Gemini and Google Maps are actually used

- **Gemini Vision**: bed-photo analysis (counting/classifying beds, reading the rotating code), medicine-bill OCR for consumption logging.
- **Google Maps API**: geofence verification (photo/check-in location vs. registered PHC coordinates), and real road distance/travel time between districts feeding the redistribution optimizer's cost function (replacing straight-line distance, which is misleading on rural road networks).

## 6. Updated build order (merges with base brief section 4)

1. Synthetic data generation — now includes attendance logs (with cell-ID field), bed photos/metadata, dispatch+receipt+consumption records, and PHC coordinates.
2. Data ingestion + trust layer — Gemini vision pipeline, VoiceERA IVR pipeline, cell-ID/Maps geofence check, medicine movement ledger.
3. Dashboard — live views for attendance/beds/stock, the new medicine movement tab, and an anomaly-flag view for district officers.
4. Flower federated learning simulation for demand forecasting (unchanged).
5. Cross-signal anomaly detection — correlates attendance/bed/medicine data per PHC (new).
6. Stockout early-warning threshold, now also weighted by a per-PHC data-trust confidence score.
7. Redistribution optimizer using Google Maps real distances.
8. Wire everything into one pipeline, with flagged anomalies routed to the audit-escalation view.

## 7. Integration notes — this is not a bolt-on

This addendum is the input layer the base brief implicitly assumed existed. The base build order jumped straight from "generate synthetic data" to forecasting; this document is the actual ingestion path that data takes, plus the verification logic that makes it trustworthy enough to forecast on.

- Sections 1–3 here feed section 1 of the base brief: synthetic data generation must include these fields (cell-ID, rotating-code metadata, dispatch/receipt pairs) from the start, not bolted on later.
- The trust layer (section 4) is a data-quality gate that sits **before** the Flower federated simulation, not after. Bad data at even a few PHCs pushes bad gradients into the shared model every round — flag-then-forecast, not forecast-then-flag.
- Gemini and Google Maps are doing real inference work here, not decoration for a Google hackathon checkbox — say this explicitly in the pitch. Gemini Vision turns an unstructured photo into a structured, verifiable database row (bed count + freshness check in one pass) — genuine multimodal reasoning, not OCR for its own sake. Google Maps does two distinct jobs: geofence verification (is this check-in actually near the PHC) and real-road-distance routing for the redistribution optimizer (straight-line distance is a bad proxy for transfer feasibility on rural roads). "Gemini closes the trust gap, Maps makes redistribution physically realistic" is a stronger pitch line than just listing APIs used.
- Keep the cross-signal anomaly check (section 4, point 2) as a rules-based scorer for the hackathon, not a trained model — don't let it become a second ML system competing for build time. Note it as future work, not something to ship now.
