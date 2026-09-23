/**
 * The pharmacist's workspace — a phone-shaped app for one health centre.
 *
 * Why this is a separate shell rather than a breakpoint on App.tsx: App is a
 * fixed two-column console, a 400px aside beside a national map, with one
 * breakpoint in nearly nine hundred lines. Squeezing it to 360px would put a
 * map of India, five view tabs and a national SKU selector behind a screen
 * whose whole job is one facility's shelf. The officer app is not wrong; it is
 * for a different person at a different desk.
 *
 * Nothing here names a facility. The centre shown is whichever one the signed-
 * in account is attached to, so the same build serves any PHC in the country.
 */

import { useEffect, useState } from "react";

import BrandMark from "../Brand";
import DataNotice from "../DataNotice";
import { type FacilityDetail, type Session, api } from "../api";
import Beds from "./Beds";
import Medicines from "./Medicines";
import MyAttendance from "./MyAttendance";
import Orders from "./Orders";
import { both, en } from "./labels";

export type Tab = "medicines" | "orders" | "beds" | "attendance";

/**
 * Four tabs is the ceiling at 360px, which is why the labels here drop to
 * English alone while the two-language pair stays on each screen's own
 * heading. Attendance is last and conditional: an account with no attendance
 * record of its own does not get an empty tab explaining that it is empty.
 */
function tabsFor(hasStaffRecord: boolean): { id: Tab; label: string }[] {
  const tabs: { id: Tab; label: string }[] = [
    { id: "medicines", label: en("medicines") },
    { id: "orders", label: en("orders") },
    { id: "beds", label: en("beds") },
  ];
  if (hasStaffRecord) tabs.push({ id: "attendance", label: en("attendance") });
  return tabs;
}

/**
 * A facility_user with no facility is a state the server refuses to create
 * (scripts/users.py validates the scope) but the User type still allows. It
 * gets an explanation and a way out rather than a blank screen.
 */
function Unattached({ onSignOut }: { onSignOut: () => void }) {
  return (
    <div className="flex min-h-full flex-col items-start gap-4 bg-canvas px-4 py-10">
      <BrandMark size={30} />
      <h1 className="text-[17px] font-semibold text-ink">This account has no health centre</h1>
      <p className="max-w-prose text-[13px] leading-relaxed text-ink-2">
        Facility staff accounts are attached to one centre. Yours is not, so there is
        nothing to show here. Ask your district officer to attach it, then sign in again.
      </p>
      <button
        onClick={onSignOut}
        className="min-h-11 rounded-md border border-line bg-panel px-4 text-[13px] font-medium text-ink"
      >
        {both("signOut")}
      </button>
    </div>
  );
}

export default function Workspace({
  session,
  onSignOut,
}: {
  session: Session;
  onSignOut: () => void;
}) {
  const facilityId = session.user.facility_id;
  const tabs = tabsFor(session.user.has_staff_record);
  const [tab, setTab] = useState<Tab>("medicines");
  const [facility, setFacility] = useState<FacilityDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Bumped by a child when it changes something the other tab reads, so
  // confirming a delivery on Orders is visible on Medicines without a reload.
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    if (!facilityId) return;
    let alive = true;
    api
      .facility(facilityId)
      .then((d) => alive && (setFacility(d), setError(null)))
      .catch((e) => alive && setError(String(e)));
    return () => {
      alive = false;
    };
  }, [facilityId, refreshKey]);

  if (!facilityId) return <Unattached onSignOut={onSignOut} />;

  return (
    <div className="flex min-h-full flex-col bg-canvas font-sans text-ink">
      <header className="shrink-0 border-b border-line bg-panel">
        <div className="flex items-start gap-3 px-4 pt-3 pb-2">
          <BrandMark size={26} />
          <div className="min-w-0 flex-1">
            <p className="text-[10.5px] font-semibold uppercase tracking-[0.09em] text-ink-3">
              {facility?.type === "CHC" ? "Community Health Centre" : "Primary Health Centre"}
            </p>
            {/* The facility is the heading. An officer's app leads with the
                country; this one leads with the room the reader is standing in. */}
            <h1 className="mt-0.5 truncate text-[17px] leading-snug font-semibold tracking-tight text-ink">
              {facility?.name ?? "Loading…"}
            </h1>
            <p className="mt-0.5 truncate text-[12px] text-ink-2">
              {facility ? `${facility.district}, ${facility.state_silo}` : " "}
            </p>
          </div>
          <button
            onClick={onSignOut}
            className="-mr-1 min-h-11 shrink-0 px-2 text-[12px] font-medium text-brand"
          >
            {/* English alone: two languages do not fit beside a facility name
                at 360px, and the Hindi is on the account screen. */}
            Sign out
          </button>
        </div>

        <div role="tablist" aria-label="Workspace view" className="flex gap-1 px-2">
          {tabs.map((t) => {
            const active = tab === t.id;
            return (
              <button
                key={t.id}
                role="tab"
                aria-selected={active}
                onClick={() => setTab(t.id)}
                className={`min-h-11 flex-1 border-b-2 px-1.5 text-[12.5px] font-medium ${
                  active
                    ? "border-brand text-brand"
                    : "border-transparent text-ink-2"
                }`}
              >
                {t.label}
              </button>
            );
          })}
        </div>
      </header>

      <div className="shrink-0 border-b border-line bg-panel px-4 py-2">
        <DataNotice />
      </div>

      {error && (
        <p role="alert" className="border-b border-line px-4 py-3 text-[12.5px] text-crit">
          {error}
        </p>
      )}

      <main className="min-h-0 flex-1 px-4 py-4">
        {tab === "medicines" && (
          <Medicines
            facilityId={facilityId}
            refreshKey={refreshKey}
            onChanged={() => setRefreshKey((k) => k + 1)}
          />
        )}
        {tab === "orders" && (
          <Orders
            facilityId={facilityId}
            refreshKey={refreshKey}
            onChanged={() => setRefreshKey((k) => k + 1)}
          />
        )}
        {tab === "beds" && (
          <Beds facilityId={facilityId} facility={facility} refreshKey={refreshKey} />
        )}
        {tab === "attendance" && <MyAttendance refreshKey={refreshKey} />}
      </main>
    </div>
  );
}
