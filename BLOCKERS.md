# Blockers — things only Aditya can supply or decide

Each line: the fix, what is needed, where to get it, and what it unlocks. Work that
does not depend on the item continues; the fix stays BLOCKED until the item arrives.

| Fix | Needed | Where / how | Unlocks |
|---|---|---|---|
| #42 | A live Gemini read of a real IDSP weekly report. No `GEMINI_API_KEY` is in any local `.env`, so the spike could not run here. | Put `GEMINI_API_KEY` and `LLM_MODE=live` in `backend/.env`, then run `python -m scripts.check_idsp_read <report.pdf>` (one model request; writes nothing). With `pypdf` installed it also reports rows the model missed. | Marking #42 DONE: the pipeline is built and tested against a stand-in transport, but whether the model reads NCDC's PDFs correctly is unverified. |
