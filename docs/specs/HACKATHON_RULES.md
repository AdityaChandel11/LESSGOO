READ FIRST: HACKATHON RULES CORRECTION + KEY-HANDLING PROTOCOL

Save this entire message into CLAUDE.md as two new sections ("Hackathon Rules" and "Keys & Blockers Protocol"), change nothing else in the file, then follow it for the rest of the project.

## 1. The correction that matters most

Earlier in this project it was claimed that Cloud Run / Google Cloud hosting is a hard requirement. That is WRONG. Disregard it. The organizers' rules say:

- The ONLY hosting requirement is: "Deployed link: a live deployed link of your prototype." Any live URL qualifies (Vercel, Render, Railway, Cloud Run, Firebase, etc.).
- Cloud Run, Cloud Functions, BigQuery and Firebase appear only under "recommended and fully supported" tools. Recommended is not required.
- The one real hard requirement is: "Mandatory integration of Google AI: GenAI, predictive modelling, or computer vision etc." Our Gemini plan (bed-photo vision, plain-language briefings) satisfies it. The rules say nothing about where it must be hosted.

What this changes:
- Frontend on Vercel is fine. There is no compliance risk.
- Use Cloud Run for the backend only if it is genuinely the easiest working option. Vercel cannot host Flower's long-running processes. Render or Railway are acceptable alternatives. Do NOT force a GCP deploy under deadline pressure. If you're unsure Cloud Run can run our Flower setup, test it and tell me before committing to it.
- Gemini integration remains top priority. It must be part of the real end-to-end flow and visible in the demo, not decorative.
- Twilio is not a Google product and does not count toward Google AI compliance. Keep it only if it serves the IVR feature.
- Google Maps is listed under Geospatial (aimed at climate/agriculture tracks). It is optional for us. Keep it for redistribution routing only if it is a real improvement.
- Multilingual/voice is conditional ("where the track calls for it"). Our track text does not explicitly require it. Keep VoiceERA as a differentiator for "built for India" if time allows, but it is not pass/fail.

## 2. Full rules from the organizers' page

BUILD: the submission must demonstrate ALL of:
1. A functioning end-to-end flow for the track's core use case
2. Mandatory integration of Google AI (GenAI, predictive modelling, computer vision, etc.)
3. Real or realistic data: public datasets, sample data, or APIs where live data isn't available
4. Built for India: designed to scale across states and communities, not a single city
5. Multilingual or voice support where the track calls for it

SUBMIT: track these five explicitly.
1. Source code: public or access-granted GitHub repo
2. Demo video: 3-5 minutes, working end-to-end walkthrough
3. Pitch deck: 10-12 slides (problem, solution, AI approach, who it serves, why it's deployable, how it scales across India)
4. Brief description: 2-3 lines
5. Deployed link: any live host

RECOMMENDED GOOGLE TOOLS (all solutions must integrate Google AI):
- Generative AI and agents: Gemini API, Google AI Studio, Vertex AI
- Predictive modelling: Vertex AI (AutoML, custom training, model serving)
- Vision and multimodal: Gemini multimodal, Vertex AI Vision
- Language and voice: Cloud Speech-to-Text and Text-to-Speech, Translation API, Dialogflow
- Geospatial: Google Maps Platform, Earth Engine
- Data and backend: BigQuery, Firebase, Cloud Run / Cloud Functions

PUBLIC DATA SOURCES: data.gov.in and Indian government open data portals, FAO, WHO health data, ISRO/Bhuvan, IMD.

TRACK CORE USE CASE: a federated AI platform for national-scale health resource and supply chain management. It needs real-time visibility into medicine stocks, bed availability and medical personnel attendance across India's PHC network; demand forecasting; early warnings for stock-outs during health emergencies; automated cross-district resource redistribution; and shared predictive modelling across states.

DATA HONESTY: prefer public sources where possible. If any data is synthetic, label it clearly as synthetic in the README, the deck and the demo.

## 3. Keys & Blockers Protocol (strict)

1. PREFLIGHT: before each stage, list every key, credential, project ID, endpoint/URL/route, or account access that stage will need, with the exact env var name and where I can get it. Ask for all of them up front.
2. HARD STOP: if you hit a needed item you did not anticipate, stop that task at that exact step and ask me. Make one clear ask: what you need, the exact variable name, where to find it, and what it unlocks.
3. NEVER WORK AROUND A MISSING ITEM: no fake keys, placeholder values, silent mocks, hard-coded stand-ins, or "simulated" integrations presented as real. Do not mark any stage complete if part of it relied on a stand-in. You may continue unrelated tasks that don't depend on the missing item, but say explicitly that you are doing so.
4. WAIT FOR ME: I will supply items side by side as you ask. Do not guess, do not retry in loops, and resume only after I confirm.
5. SECRET HYGIENE: the repo is public or access-granted. Secrets live only in .env (gitignored) or the hosting platform's secret settings. Keep a .env.example with variable names only. Never print, log, echo or commit a secret, and never put one in frontend code. Check git status and git diff for leaked secrets before every commit. If I paste a key in chat, write it to .env only and do not repeat it back.

## 4. Stage reporting

After every stage, report:
- Status of each of the 5 submission items
- Whether Google AI is live in the real flow and demo-able
- Which tests were run and their results
- Anything blocked on me

## 5. Before you begin

Do NOT start any task yet. Reply with only:
(a) A 5-line summary of what you understood
(b) The full list of keys, credentials, IDs and URLs you expect to need for the rest of the build, grouped by service, with exact variable names
(c) Any doubts, or conflicts with the existing plan

Then wait for my "go"

ADDENDUM: OFFICIAL JUDGING CRITERIA

Append this to CLAUDE.md as a new section titled "Judging Criteria", change nothing else, then apply it to every decision from now on.

Submissions are judged on five criteria, weighted as follows:

1. AI / Technical Execution: 25% (highest weight)
   Is Google AI doing meaningful work? Does the prototype function end-to-end?
2. Problem-Solution Fit: 20%
   Does it directly and specifically address the stated challenge?
3. Depth & Reach Across India: 20%
   Can this realistically scale from one city or state to communities across India?
4. Deployability & Scalability: 20%
   Could this be piloted within a ministry or across states in weeks?
5. Impact Potential: 15%
   Scale of benefit: how many people, across how many states, how meaningfully?

How this changes the build:

- GEMINI MUST DO REAL WORK. "Meaningful" means removing Gemini would break or noticeably weaken a core feature (bed-photo vision, plain-language briefings, forecasting explanations). A chat box or decorative summary earns little. If any Google AI use is cosmetic, flag it to me and propose how to make it load-bearing.
- END-TO-END BEATS FEATURE COUNT. A smaller flow that works completely from data in to decision out, live, is worth more than many half-connected features. Before adding any new feature, tell me whether it strengthens the core flow or is a distraction. Stop and ask me if unsure.
- COVER THE FULL CHALLENGE. The track asks for: real-time visibility of medicine stocks, bed availability and personnel attendance across PHCs; demand forecasting; early stock-out warnings during health emergencies; automated cross-district redistribution; and shared predictive modelling across states. Tell me which of these are working, partial, or missing.
- SHOW MULTI-STATE REACH IN THE PROTOTYPE, not only in the deck. Data and demo should span several states and districts, and the federated setup should visibly involve multiple state-level participants.
- DEPLOYABILITY IS A SCORED ITEM. Keep setup simple and documented: README with run and deploy steps, .env.example, no manual hacks, and a clear "how a ministry could pilot this in weeks" path (data onboarding, hosting, roles, privacy).
- IMPACT CLAIMS MUST BE HONEST. Any numbers about lives, PHCs, or savings must come from a cited public source or be clearly labelled as an estimate. Never invent statistics. Label synthetic data as synthetic everywhere (README, deck, demo).

Deck and demo mapping (use this when we build them):
- Problem-Solution Fit: open the demo with the exact problem and show the flow solving it
- AI/Technical Execution: show Gemini and the model doing visible work live, then the architecture in one slide
- Depth & Reach: show several states and the scaling plan
- Deployability: show the live link plus the pilot-in-weeks plan
- Impact: sourced numbers only

Update stage reporting: after every stage, add a one-line self-score against each of the five criteria (weak / okay / strong) with the single biggest gap for each, so we always know where the marks are being lost.

Do not start new work because of this addendum. Confirm in 3 lines that you've applied it, then continue with the earlier instructions.