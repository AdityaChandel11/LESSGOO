# Blockers — things only Aditya can supply or decide

Each line: the fix, what is needed, where to get it, and what it unlocks. Work that
does not depend on the item continues; the fix stays BLOCKED until the item arrives.

| Fix | Needed | Where / how | Unlocks |
|---|---|---|---|
| #85 | One live Gemini write of the Today list. The computed list (English, Hindi) and every check on the model's answer are tested against a stand-in transport; what Gemini actually writes — the Marathi list above all — is unverified here, because no `GEMINI_API_KEY` is in any local `.env`. | After deploying: sign in as Pharmacist, Nashik PHC 1, press "Ask Gemini to write today's list", then EN / हिं / मराठी (one model request, cached for the day). If the note under the list says a figure was refused, tell me the note's text. | Saying the three-language briefing works live, not only that it falls back correctly. |
| #42 | A live Gemini read of a real IDSP weekly report. No `GEMINI_API_KEY` is in any local `.env`, so the spike could not run here. | Put `GEMINI_API_KEY` and `LLM_MODE=live` in `backend/.env`, then run `python -m scripts.check_idsp_read <report.pdf>` on NCDC's latest, https://ncdc.mohfw.gov.in/uploads/weekly_outbreaks/2026/week32_1790680151.pdf (the regex parser misses 2 of its 51 rows, so it also shows what the model adds) (one model request; writes nothing). With `pypdf` installed it also reports rows the model missed. | Marking #42 DONE: the pipeline is built and tested against a stand-in transport, but whether the model reads NCDC's PDFs correctly is unverified. |
