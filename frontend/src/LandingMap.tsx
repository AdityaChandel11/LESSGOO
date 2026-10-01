import { type Bucket, STATUS_COLOR, bucketSeverity } from "./api";

/**
 * The national picture as a hero, for visitors who have not signed in.
 *
 * Deliberately not the real NationalMap. That one zooms into district rollups
 * and individual facility pins, both of which stay behind a session — pointing
 * it at a logged-out visitor would only produce 401s. This draws the one tier
 * that is public: every state and union territory, with a dot sized by how
 * many health centres it holds and coloured by how many of those are short.
 *
 * A tile map, drawn as SVG, rather than map tiles. Each state and union
 * territory is one square, placed roughly where it sits in the country. That
 * keeps the picture sharp at any size and any browser zoom, shows India and
 * nothing else, carries no place names in other scripts, and needs no key and
 * no third-party tile server. It is a schematic and says so: it draws no
 * boundary, so it cannot draw one wrongly.
 */

/** Column and row of each state's tile; nine columns, seven rows. */
const GRID: Record<string, [number, number]> = {
  JK: [1, 0], LA: [2, 0],
  PB: [0, 1], CH: [1, 1], HP: [2, 1], UK: [3, 1], AR: [8, 1],
  RJ: [0, 2], HR: [1, 2], DL: [2, 2], UP: [3, 2], BR: [4, 2], SK: [5, 2], AS: [7, 2], NL: [8, 2],
  GJ: [0, 3], MP: [2, 3], CT: [3, 3], JH: [4, 3], WB: [5, 3], ML: [7, 3], MN: [8, 3],
  DH: [1, 4], MH: [2, 4], TG: [3, 4], OD: [4, 4], TR: [7, 4], MZ: [8, 4],
  GA: [1, 5], KA: [2, 5], AP: [3, 5],
  LD: [0, 6], KL: [2, 6], TN: [3, 6], PY: [4, 6], AN: [7, 6],
};

const TILE = 64;
const GAP = 6;
const PITCH = TILE + GAP;
const PAD = 8;
const COLS = 9;
const ROWS = 7;

/** Area, not radius, should track the count, so the square root does the work. */
function radiusFor(total: number): number {
  return Math.min(Math.max(5, 4 + Math.sqrt(total) * 0.75), 19);
}

function describe(states: Bucket[]): string {
  if (!states.length) return "Tile map of India's states and union territories";
  const short = states.filter((s) => bucketSeverity(s) !== "healthy").length;
  return (
    `Tile map of India with one tile per state and union territory, each holding a dot sized ` +
    `by its number of health centres. ${short} of ${states.length} are carrying facilities ` +
    `that are short of stock.`
  );
}

export default function LandingMap({ states }: { states: Bucket[] }) {
  const byKey = new Map(states.map((s) => [s.key, s]));
  // A state the grid does not know still gets a tile, on a row of its own.
  const extras = states.filter((s) => !GRID[s.key]);
  const rows = ROWS + (extras.length ? 1 : 0);
  const tiles: { key: string; col: number; row: number }[] = [
    ...Object.entries(GRID).map(([key, [col, row]]) => ({ key, col, row })),
    ...extras.map((s, i) => ({ key: s.key, col: i % COLS, row: ROWS })),
  ];
  const width = PAD * 2 + COLS * PITCH - GAP;
  const height = PAD * 2 + rows * PITCH - GAP;

  return (
    <figure className="rounded-lg border border-line bg-panel p-2 sm:p-3">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={describe(states)}
        className="block h-auto w-full"
      >
        {tiles.map(({ key, col, row }) => {
          const s = byKey.get(key);
          const x = PAD + col * PITCH;
          const y = PAD + row * PITCH;
          const status = s ? bucketSeverity(s) : null;
          const colour = status ? STATUS_COLOR[status] : "#7d858f";
          const r = s ? radiusFor(s.total) : 0;
          return (
            <g key={key} transform={`translate(${x} ${y})`}>
              {s && (
                <title>
                  {`${s.label}: ${s.total.toLocaleString("en-IN")} centres, ${s.critical} critical, ${s.at_risk} at risk`}
                </title>
              )}
              <rect
                width={TILE}
                height={TILE}
                rx={8}
                fill={colour}
                fillOpacity={s ? 0.1 : 0.05}
                stroke={colour}
                strokeOpacity={s ? 0.35 : 0.2}
              />
              <text x={7} y={18} fontSize={14} fontWeight={600} fill="#4a525c">
                {key}
              </text>
              {s && (
                <>
                  {/* A critical state gets a ring that widens and fades
                      (index.css); the dot never moves, and reduced-motion
                      drops the ring. */}
                  {status === "critical" && (
                    <circle className="pulse-ring" cx={36} cy={40} r={r} fill={colour} opacity={0.5} />
                  )}
                  <circle cx={36} cy={40} r={r} fill={colour} stroke="#ffffff" strokeWidth={1.5} />
                  {r >= 11 && s.critical > 0 && (
                    <text
                      x={36}
                      y={44}
                      textAnchor="middle"
                      fontSize={11}
                      fontWeight={600}
                      fill="#ffffff"
                      fontFamily="var(--font-mono)"
                    >
                      {s.critical}
                    </text>
                  )}
                </>
              )}
            </g>
          );
        })}
      </svg>
      <figcaption className="mt-1.5 px-1 text-[11px] leading-snug text-ink-3">
        A schematic, not a boundary map: one tile per state and union territory, placed roughly
        by geography. The number in a dot is that state's centres under 3 days of stock.{" "}
        <span lang="hi">योजनाबद्ध चित्र, सीमाओं का नक्शा नहीं।</span>
      </figcaption>
    </figure>
  );
}
