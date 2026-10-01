/**
 * Fix #91: the basics a public health website carries — privacy, accessibility,
 * sources, help, last updated — without pretending to be one. No emblem, no
 * "Government of India": this is an independent prototype and says so first.
 *
 * Every statement here was checked against the code (models.py, beds.py,
 * ingest.py, idsp.py) when it was written; change the code, change this page.
 */

declare const __BUILD_DATE__: string;

const REPO = "https://github.com/AdityaChandel11/LESSGOO";

function Section({
  id,
  title,
  hindi,
  children,
}: {
  id: string;
  title: string;
  hindi: string;
  children: React.ReactNode;
}) {
  return (
    <section id={id} className="border-t border-line py-5">
      <h2 className="text-[16px] font-semibold text-ink">
        {title} <span className="text-[13px] font-medium text-ink-3">· {hindi}</span>
      </h2>
      <div className="mt-2 space-y-2 text-[13.5px] leading-relaxed text-ink-2">{children}</div>
    </section>
  );
}

export default function Notices({ onBack }: { onBack: () => void }) {
  return (
    <div className="min-h-full bg-canvas font-sans text-ink">
      <a
        href="#notices-main"
        className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:rounded focus:bg-panel focus:px-3 focus:py-2 focus:text-[13px]"
      >
        Skip to main content
      </a>
      <main id="notices-main" className="mx-auto max-w-[760px] px-5 py-8">
        <button onClick={onBack} className="text-[13px] font-medium text-brand hover:underline">
          ← SwasthSetu
        </button>
        <h1 className="mt-3 text-[24px] font-semibold tracking-tight">Notices · सूचनाएँ</h1>
        <p className="mt-2 text-[13.5px] leading-relaxed text-ink-2">
          SwasthSetu is an independent hackathon prototype. It is not a government service and is not
          affiliated with the Ministry of Health and Family Welfare or any state government. Its data is
          synthetic.
        </p>
        <nav aria-label="On this page" className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[13px]">
          <a href="#privacy" className="text-brand hover:underline">Privacy</a>
          <a href="#accessibility" className="text-brand hover:underline">Accessibility</a>
          <a href="#sources" className="text-brand hover:underline">Data sources</a>
          <a href="#help" className="text-brand hover:underline">Help and contact</a>
        </nav>

        <Section id="privacy" title="Privacy notice" hindi="गोपनीयता सूचना">
          <p>
            The relevant law is India's Digital Personal Data Protection Act, 2023. This system holds{" "}
            <strong className="font-semibold text-ink">no patient-level data at all</strong>: no patient names,
            conditions or records of any kind.
          </p>
          <p>
            <strong className="font-semibold text-ink">What it holds:</strong> medicine stock counts, deliveries and
            their receipts, ward bed counts with the checks run on them, and staff check-ins, all for health
            centres.
          </p>
          <p>
            <strong className="font-semibold text-ink">About people:</strong> an officer account stores a name, an
            email address, a role and a password hash. A phone number that reports by SMS or call is stored only as
            a salted hash and shown masked. Staff check-ins carry a pseudonymous reference, and attendance is shown
            only for a centre as a whole, never for a person. A ward photograph is read for its bed figures and the
            day's code and is not stored; only a fingerprint (hash) of it is kept, so the same photo is recognised
            if it is sent again. Failed sign-in attempts keep the email address and IP address used, to limit
            repeated guessing.
          </p>
          <p>
            <strong className="font-semibold text-ink">Why:</strong> to see medicine, bed and staff availability
            across health centres and to move stock to where it is needed.
          </p>
          <p>
            <strong className="font-semibold text-ink">How long:</strong> this demonstration database is temporary.
            A pilot would set retention periods with the health department under the Act; none is claimed here.
          </p>
        </Section>

        <Section id="accessibility" title="Accessibility statement" hindi="सुगम्यता वक्तव्य">
          <p>
            The aim is WCAG 2.2 level AA. Controls are buttons, links and form fields that work from the keyboard;
            text colours were chosen for contrast; the main screens carry English and Hindi labels; each chart has
            a table view.
          </p>
          <p>
            <strong className="font-semibold text-ink">Known gaps:</strong> the map cannot be used with a screen
            reader — the panel beside it lists the same states, districts and centres. Hindi covers the main labels,
            not every sentence. There is no text-size or high-contrast control yet.
          </p>
        </Section>

        <Section id="sources" title="Data sources and citations" hindi="डेटा स्रोत">
          <p>
            <strong className="font-semibold text-ink">Outbreaks:</strong> IDSP Weekly Outbreak Report, National
            Centre for Disease Control (
            <a href="https://ncdc.mohfw.gov.in" target="_blank" rel="noreferrer" className="text-brand underline">
              ncdc.mohfw.gov.in
            </a>
            ), weeks 31 and 32 of 2026, parsed from the published PDFs.
          </p>
          <p>
            <strong className="font-semibold text-ink">Map:</strong> © OpenStreetMap contributors.
          </p>
          <p>
            <strong className="font-semibold text-ink">Places:</strong> district and city coordinates are of real
            places; health-centre positions are synthetic, scattered around them.
          </p>
          <p>
            <strong className="font-semibold text-ink">Stock, beds, staff and deliveries:</strong> synthetic,
            generated by the project's seed —{" "}
            <a href="/demo-data" className="text-brand underline">
              how this data is generated
            </a>
            . Forecasts come from a model trained across four state silos on that synthetic data.
          </p>
        </Section>

        <Section id="help" title="Help and contact" hindi="सहायता और संपर्क">
          <p>
            This is a prototype built for a hackathon. Its source code, and the place to report a problem, is the
            project's repository:{" "}
            <a href={REPO} target="_blank" rel="noreferrer" className="text-brand underline">
              {REPO.replace("https://", "")}
            </a>
            .
          </p>
        </Section>

        <p className="border-t border-line pt-4 text-[12px] text-ink-3">Last updated: {__BUILD_DATE__} (this build)</p>
      </main>
    </div>
  );
}
