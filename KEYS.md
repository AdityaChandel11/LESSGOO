# API keys — what to get, where to put it, how to check it

**Nothing here is needed to run the site.** Every external service has a local
path that is on by default, and those paths are tested alongside the live ones.
Add a key when you want that particular capability to be real, one at a time.

All of them go in **one file**: `backend/.env` (copy `.env.example` to start).
That file is git-ignored and must stay that way. In deployment they go to Secret
Manager instead — never into the image, never into `firebase.json`.

---

## Needed now, if you want the live versions of what is already built

### 1. Google Maps — road distances between facilities

| | |
|---|---|
| Console | Google Cloud → APIs & Services → **Routes API** (enable) |
| Key restriction | Application: **None** or **IP addresses** (it stays on the server). API: **Routes API only** |
| `.env` | `MAPS_MODE=google`<br>`GOOGLE_MAPS_SERVER_KEY=…` |
| Gives you | Real road km and drive time in every transfer, instead of straight-line × 1.3 |
| Check | `cd backend && python -m scripts.check_maps` |

### 2. Google Maps — the map background

| | |
|---|---|
| Console | Same project → **Map Tiles API** (enable) |
| Key restriction | Application: **HTTP referrers**, your domains only. API: **Map Tiles API only** |
| `.env` | `GOOGLE_MAPS_BROWSER_KEY=…` |
| Gives you | Google's basemap instead of OpenStreetMap |
| Check | `cd backend && python -m scripts.check_maps` |

> This key is sent to every visitor's browser — that is how browser map keys
> work. The referrer and API restrictions are what protect it, so set both.
> Keep it separate from the server key above: one leaks by design, the other
> must not.

**Before demoing on Google's basemap:** their attribution terms require the
official Google logo asset on the map, not just the text line we render now.
Tell me when you add the key and I will put the proper logo in.

### 3. Gemini — reading ward photographs

| | |
|---|---|
| Console | [aistudio.google.com](https://aistudio.google.com) → Get API key (or Cloud → Generative Language API) |
| `.env` | `LLM_MODE=live`<br>`GEMINI_API_KEY=…` |
| Gives you | Real bed counting and code-reading from a photo, instead of the mock extractor |
| Check | `cd backend && python -m scripts.check_gemini` |

Without it the pipeline still runs end to end and every row it writes is
labelled `model: "mock"`, shown on screen as "no photograph was analysed".

---

## Needed for the next phases — get them started, they take time to approve

### 4. Twilio — SMS, WhatsApp and IVR *(Phase C, not built yet)*

| | |
|---|---|
| Console | [twilio.com/console](https://www.twilio.com/console) |
| `.env` | `COMMS_MODE=live`<br>`TWILIO_ACCOUNT_SID=…`<br>`TWILIO_AUTH_TOKEN=…`<br>`TWILIO_SMS_NUMBER=+91…`<br>`TWILIO_WHATSAPP_NUMBER=whatsapp:+14155238886`<br>`TWILIO_VOICE_NUMBER=+91…`<br>`PUBLIC_WEBHOOK_BASE_URL=https://…` |

Start the **WhatsApp sender approval now** — the sandbox works immediately but a
production sender takes days to weeks. The SMS and voice numbers are instant.
`PUBLIC_WEBHOOK_BASE_URL` is wherever Twilio can reach you: your deployed URL,
or an ngrok tunnel while developing.

### 5. Bhashini — Indian-language voice reporting *(Phase C, optional)*

| | |
|---|---|
| Console | [bhashini.gov.in](https://bhashini.gov.in) — registration, not instant |
| `.env` | `BHASHINI_API_KEY=…` |

Only needed for voice reporting in languages beyond what Twilio handles. The
IVR works without it.

---

## Not keys, but required before the site goes live publicly

| | |
|---|---|
| `JWT_SECRET` | `python -c "import secrets; print(secrets.token_urlsafe(48))"` — production refuses to start without a real one |
| `DATABASE_URL` | Created for you by `./deploy/cloudrun.sh setup` and stored in Secret Manager |
| GCP project | Needed before any deployment: `export PROJECT_ID=your-project` |

---

## Possible later, not required

**Google Geolocation API** — turns a cell tower id into coordinates, for
attendance from feature phones. Only useful once you have a USSD gateway or
operator integration, because an inbound Twilio call does not carry the tower.
Until then the demo labels those locations `simulated` and says so on screen.

---

## Checking everything at once

```bash
cd backend
python -m scripts.check_maps      # Routes API + Map Tiles API
python -m scripts.check_gemini    # Gemini
python -m pytest -q               # the local paths, which must keep passing
```

If a key is wrong the site does not break: distances fall back to estimates,
the map falls back to OpenStreetMap, photo extraction falls back to the mock —
and each one says on screen that it did.
