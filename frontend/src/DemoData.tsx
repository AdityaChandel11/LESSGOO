/**
 * "How this demo data is generated" — fix list #28.
 *
 * Everything on this site is synthetic, and a label saying so is necessary but
 * not sufficient: a reviewer who is told the data is made up will reasonably
 * ask how, and whether the interesting parts (the red centres, the weak state,
 * the flagged facilities) were planted. They were, by rules, and this page
 * states those rules in plain words.
 *
 * Every number below comes from SEED_RULES, which a backend test pins to the
 * literals in backend/scripts/seed.py. Nothing here is read from the database:
 * the page describes the generator, not today's data, so it cannot go stale
 * when the database is reseeded — only when the script changes, and then the
 * test fails.
 */

import BrandMark from "./Brand";
import { SEED_RULES as R } from "./seedRules";

const pct = (x: number) => `${Math.round(x * 1000) / 10}%`;
const fmt = (n: number) => n.toLocaleString("en-IN");

/** The chance a consignment lands in each outcome, for a state's paperwork factor. */
function paperwork(factor: number) {
  const short = Math.min(R.shortCap, R.shortRate * factor);
  return {
    unconfirmed: Math.min(R.unconfirmedCap, R.unconfirmedRate * factor),
    short,
    over: Math.min(R.overCap, R.overRate * factor) - short,
  };
}

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
    <section aria-labelledby={id} className="border-t border-line py-7">
      <h2 id={id} className="text-[17px] leading-snug font-semibold tracking-tight text-ink">
        {title}
        <span className="ml-2 text-[14px] font-normal text-ink-3">· {hindi}</span>
      </h2>
      <div className="mt-3 space-y-2.5 text-[14px] leading-relaxed text-ink-2">{children}</div>
    </section>
  );
}

export default function DemoData({ onBack }: { onBack: () => void }) {
  const normal = paperwork(1);
  const weak = paperwork(R.weakFactor);
  const strong = paperwork(R.strongFactor);
  const outbreakFrom = R.outbreakDaysAgo - R.outbreakLengthDays;

  return (
    <div className="min-h-full bg-canvas font-sans text-ink">
      <header className="border-b border-line bg-panel">
        <div className="mx-auto flex max-w-3xl items-center gap-2.5 px-5 py-4 sm:px-8">
          <BrandMark />
          <div className="text-[15px] font-semibold tracking-tight">SwasthSetu</div>
          <button
            type="button"
            onClick={onBack}
            className="ml-auto rounded-sm text-[12.5px] font-medium text-ink-3 hover:text-ink focus:ring-2 focus:ring-brand/30 focus:outline-none"
          >
            ← Back to the overview
          </button>
        </div>
      </header>

      <main id="main" className="mx-auto max-w-3xl px-5 pt-10 pb-16 sm:px-8">
        <p className="text-[12px] font-semibold tracking-[0.14em] text-ink-3 uppercase">
          Synthetic demonstration data · कृत्रिम (सिंथेटिक) डेटा
        </p>
        <h1 className="mt-2 text-[26px] leading-tight font-semibold tracking-tight">
          How this demo data is generated
        </h1>
        <p className="mt-1 text-[15px] text-ink-3">यह डेमो डेटा कैसे बनाया गया है</p>

        <div className="mt-6 space-y-3 text-[14.5px] leading-relaxed text-ink-2">
          <p>
            Every health centre, stock count, bed figure, staff check-in and delivery on this site
            is synthetic. One script, <code className="font-mono text-[13px]">backend/scripts/seed.py</code>,
            makes all of it from a fixed random seed ({R.randomSeed}), so the same seed reproduces
            the same data. No real patient, staff member or facility record exists in this system.
          </p>
          <p>
            The problems you will find in the data — centres running out, a state with poor
            paperwork, centres whose numbers do not add up — were put there by the rules below, so
            that the parts of the platform that are meant to find them have something to find.
            Nothing on this page describes any real centre, district or state administration.
          </p>
        </div>

        <Section id="places" title="Centres and places" hindi="केंद्र और स्थान">
          <p>
            The district and city coordinates are real. Each centre is placed by random scatter
            around one of them, so a dot on the map is near a real place but is not a real building.
          </p>
          <p>
            There are {fmt(R.seededCentres)} centres across all {R.regions} states and union
            territories — each state gets roughly 12% of its published count of primary health
            centres, not its real network. Every {R.chcEvery}th centre is a community health centre
            (CHC); the rest are primary health centres (PHCs). A PHC has {R.phcBedsMin}–
            {R.phcBedsMin + R.bedsSpread} beds and a CHC {R.chcBedsMin}–{R.chcBedsMin + R.bedsSpread}.
          </p>
          <p>
            Every centre gets {R.historyDays} days of daily stock counts. {R.focusStates} states —
            Uttar Pradesh, Maharashtra, Bihar and Kerala — get a longer history, because they are
            the ones the shared forecasting model trains on: {R.focusHistoryDaysDefault} days by
            default, and {R.focusHistoryDaysDeployed} days on the deployed demo (the README's deploy
            step), which keeps the hosted database inside its size limit.
          </p>
        </Section>

        <Section id="use" title="Daily medicine use" hindi="दवा की दैनिक खपत">
          <p>
            {R.medicines} medicines are tracked. Each has a typical daily use per centre, and each
            centre is given its own multiplier between {R.centreMultiplierMin}× and{" "}
            {R.centreMultiplierMax}× of it; a CHC uses {R.chcScale}× what a PHC does.
          </p>
          <p>
            On top of that, use rises and falls by up to {pct(R.seasonalSwing)} over the year, on a
            cycle shifted differently in each state, so states' seasons genuinely differ. In each
            state's own monsoon months, the monsoon-sensitive medicines (ORS, zinc, amoxicillin, IV
            fluid, antimalarials) rise by that state's monsoon factor — between{" "}
            {pct(R.monsoonBoostMin)} and {pct(R.monsoonBoostMax)} depending on the state. Use falls
            to {pct(R.festivalFactor)} of normal within {R.festivalHalfWidth} days of 20 October and
            5 November. Each day then gets random noise of about {pct(R.noiseSd)} of the centre's
            usual use.
          </p>
          <p>
            One outbreak is written in: in Nashik district, ORS, zinc and IV fluid run at{" "}
            {R.outbreakMultiplier}× for {R.outbreakLengthDays} days, {outbreakFrom}–
            {R.outbreakDaysAgo} days before the seed was run. It only exists when the history
            reaches that far back — it is in the default {R.focusHistoryDaysDefault}-day history,
            and it is <strong className="font-semibold text-ink">not</strong> in the deployed
            demo's {R.focusHistoryDaysDeployed} days.
          </p>
        </Section>

        <Section id="supply" title="Deliveries and stock-outs" hindi="आपूर्ति और स्टॉक की कमी">
          <p>
            When a centre's stock falls below {R.reorderPointDays} days of its usual use, a delivery
            of {R.resupplyCoverDays} days' worth is ordered and arrives {R.leadTimeMin}–
            {R.leadTimeMax} days later.
          </p>
          <p>
            {pct(R.supplyFailureShare)} of centres, chosen at random, receive no new deliveries in
            the last {R.supplyFailureDays} days before the seed was run. They are where most of the
            map's red (critical) centres come from. Their wards also run {pct(R.fullWardMin)}–100%
            full over the last {R.fullWardDays} days; other centres' wards are{" "}
            {pct(R.bedOccupancyMin)}–{pct(R.bedOccupancyMax)} full.
          </p>
        </Section>

        <Section id="paperwork" title="Paperwork quality by state" hindi="राज्यवार काग़ज़ी रिकॉर्ड की गुणवत्ता">
          <p>
            Each centre receives {R.consignmentsMin}–{R.consignmentsMax} warehouse consignments over
            the last {R.consignmentDays} days. At normal quality, {pct(normal.unconfirmed)} of those
            sent in the last {R.recentDays} days whose delivery window has passed are left with no
            receipt confirmation; of older ones, {pct(normal.short)} arrive short and{" "}
            {pct(normal.over)} over.
          </p>
          <p>
            Each time the seed runs, one of the {R.focusStates} long-history states is drawn at
            random to keep its paperwork {R.weakFactor}× worse ({pct(weak.unconfirmed)} unconfirmed,{" "}
            {pct(weak.short)} short, {pct(weak.over)} over, and later deliveries), and one is drawn
            to keep it {R.strongFactor}× as often wrong ({pct(strong.unconfirmed)},{" "}
            {pct(strong.short)}, {pct(strong.over)}). Which state comes out weakest is a coin toss
            by the generator. It says nothing about that real state's health administration.
          </p>
          <p>
            This is what makes the federation weighting visible: a state whose deliveries go
            unconfirmed contributes less to the shared model, and the evidence is the consignment
            rows themselves, which anyone can open in the movement ledger.
          </p>
        </Section>

        <Section id="gaming" title="Centres made to misreport" hindi="जानबूझकर गलत रिपोर्ट करने वाले केंद्र">
          <p>
            About {pct(R.gamingShare)} of centres are made to misreport on purpose, chosen at random
            but {R.weakFactor}× as likely in the weak state. Where a centre has more than{" "}
            {R.gamingMinHistory} days of history, it reports a frozen stock figure for one stretch
            of {R.gamingWindowMin}–{R.gamingWindowMax} days instead of the real one. It marks every
            member of staff present every day, logs no patients on {pct(R.gamingZeroFootfall)} of
            those days, and leaves {pct(R.gamingNoReply)} of its random checks unanswered (other
            centres: {pct(R.normalNoReply)}).
          </p>
          <p>
            The seed writes down which centres it made dishonest in a separate file that only the
            evaluation harness reads. No screen of the app can read it: the data-trust layer has to
            find these centres from the ordinary records, the way it would have to in real life.
          </p>
        </Section>

        <Section id="staff" title="Staff check-ins" hindi="कर्मचारियों की उपस्थिति">
          <p>
            A PHC has {R.phcRoster} staff and a CHC {R.chcRoster}, with {R.attendanceDays} days of
            shift check-ins. No names exist: staff are reference codes, and officers see counts per
            centre. Each person keeps their own attendance rate, between {pct(R.attendRateMin)} and{" "}
            {pct(R.attendRateMax)}, and {pct(R.absenceRunShare)} of people have one run of{" "}
            {R.absenceRunMin}–{R.absenceRunMax} consecutive days away — leave, training or a
            posting elsewhere.
          </p>
          <p>
            {pct(R.gpsShare)} of centres check in from a phone with GPS, {pct(R.ussdShare)} over
            USSD (which gives only a mobile tower) and {pct(R.ivrShare)} by voice call (which gives
            no location at all). On {pct(R.pingChance)} of shifts a random check is sent at an
            unpredictable moment during the shift.
          </p>
        </Section>

        <Section id="beds" title="Beds and ward photos" hindi="बिस्तर और वार्ड की तस्वीरें">
          <p>
            Ward reports cover the last {R.wardPhotoDays} days. Each centre reports on{" "}
            {pct(R.wardReportRateMin)}–{pct(R.wardReportRateMax)} of days. Of those reports,{" "}
            {pct(R.wardOldCodeBelow)} carry the previous day's code,{" "}
            {pct(R.wardIllegibleBelow - R.wardOldCodeBelow)} have an unreadable code,{" "}
            {pct(R.wardElsewhereBelow - R.wardIllegibleBelow)} were taken kilometres away from the
            centre, and {pct(R.wardRegisterBelow - R.wardElsewhereBelow)} disagree with the
            admissions register.
          </p>
          <p>
            No photograph exists behind these seeded reports: the seed writes the figures a
            reading would have returned and marks them as coming from the stand-in model, not from
            Gemini. Only a photo taken in the app is read by Gemini, and only while Gemini is
            switched on for the deployment.
          </p>
        </Section>

        <Section id="limits" title="What this data cannot tell you" hindi="यह डेटा क्या नहीं बता सकता">
          <ul className="list-disc space-y-1.5 pl-5">
            <li>
              The rates above are chosen to be plausible. They are not measured from any real
              centre's records, so no figure on this site is evidence about real stock-outs.
            </li>
            <li>There is no patient data at all — not synthetic, not real.</li>
            <li>District warehouse stock is not modelled; only the warehouses' dispatches are.</li>
            <li>
              One database stands in for every state's own store. In a state deployment each
              state's rows would stay in that state's database.
            </li>
          </ul>
        </Section>

        <p className="mt-4 border-t border-line pt-5 text-[12px] leading-relaxed text-ink-3">
          Sources: <code className="font-mono">backend/scripts/seed.py</code> (rules),{" "}
          <code className="font-mono">backend/app/geo.py</code> (places). The test{" "}
          <code className="font-mono">backend/tests/test_demo_data_page.py</code> fails if this page
          and the script disagree.
        </p>
      </main>
    </div>
  );
}
