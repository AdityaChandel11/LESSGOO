/**
 * The synthetic-data label.
 *
 * The hackathon rules require synthetic data to be labelled as synthetic
 * wherever it is shown — not only in the README and the deck. This platform
 * paints 3,510 health centres on a map of India with real district names and
 * real coordinates, which is exactly the kind of screen somebody could
 * screenshot and mistake for live national data. So the label travels with
 * the interface rather than sitting in a document nobody opens.
 *
 * No counts here on purpose: a number in a fixed string goes stale the first
 * time the database is reseeded, and a stale label is worse than a plain one.
 */

export default function DataNotice({ className = "" }: { className?: string }) {
  return (
    <p className={`text-[11px] leading-snug text-ink-3 ${className}`}>
      <span className="font-medium text-ink-2">Synthetic demonstration data</span>
      {" · "}
      Facilities, stock, beds and attendance are simulated for this prototype. No real patient
      or facility records exist in this system.
    </p>
  );
}
