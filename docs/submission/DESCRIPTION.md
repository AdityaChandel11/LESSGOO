# Brief description (2–3 lines)

SwasthSetu is a federated platform for medicine, bed and staff visibility across India's PHC/CHC network: four states train one demand-forecasting model on Flower and send only model weights, never a facility record. Forecasts and NCDC's IDSP outbreak reports become early stock-out warnings, an OR-Tools solver pre-positions stock from centres with surplus, and nothing moves until the donor centre accepts. Gemini reads photographed bills and ward boards into checked records and explains each transfer and trust flag; the prototype runs on synthetic data for 3,510 facilities in all 36 states and union territories, and says so on every screen.

## What each sentence rests on

| Claim | Where it is true today |
|---|---|
| Four states, weights only | Recorded run on Flower's deployment engine, replayed on the Federation tab; the aggregator asserts 0 facility rows per round |
| Forecasts and outbreaks become warnings | `backend/app/outbreak.py`, `tests/test_outbreak_surge.py`; forecasts cover the four training states and six medicines, burn rate elsewhere |
| Solver pre-positions; the donor decides | `backend/app/redistribution.py`, `tests/test_oversight.py` |
| Gemini reads bills and ward boards | `backend/app/vision.py`; on the demo the ward photo is a drawn whiteboard, labelled |
| Synthetic data, 3,510 facilities | `backend/scripts/seed.py`, `tests/test_seed_geography.py`. The outbreak rows are real, from NCDC |

Beds and staff are visible one facility at a time; a district or state view of either is not built yet. See the README's "Known gaps".
