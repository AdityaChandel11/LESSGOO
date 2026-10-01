/**
 * The synthetic generator's rules, as the numbers the "How this demo data is
 * generated" page shows (DemoData.tsx).
 *
 * Every value is a copy of a literal in backend/scripts/seed.py, app/geo.py or
 * the README's deploy step. backend/tests/test_demo_data_page.py reads this
 * file and the generator side by side and fails when they disagree, so the page
 * can only ever describe what the script actually does. Change the script, then
 * this file, then the test's snippet — in that order.
 *
 * One value per line: the test parses `name: number,`.
 */
export const SEED_RULES = {
  randomSeed: 20260915,
  medicines: 12,

  // Places — app/geo.py
  seededCentres: 3510,
  regions: 36,
  focusStates: 4,
  chcEvery: 5,
  phcBedsMin: 6,
  chcBedsMin: 30,
  bedsSpread: 6,

  // How far back the history goes
  historyDays: 35,
  focusHistoryDaysDefault: 400,
  focusHistoryDaysDeployed: 120,

  // Daily use
  centreMultiplierMin: 0.6,
  centreMultiplierMax: 1.5,
  chcScale: 2.2,
  seasonalSwing: 0.4,
  monsoonBoostMin: 0.05,
  monsoonBoostMax: 0.9,
  festivalFactor: 0.75,
  festivalHalfWidth: 3,
  noiseSd: 0.12,
  outbreakMultiplier: 2.8,
  outbreakDaysAgo: 140,
  outbreakLengthDays: 18,

  // Deliveries and stock-outs
  reorderPointDays: 7,
  resupplyCoverDays: 30,
  leadTimeMin: 2,
  leadTimeMax: 6,
  supplyFailureShare: 0.18,
  supplyFailureDays: 12,
  fullWardDays: 10,
  fullWardMin: 0.88,
  bedOccupancyMin: 0.35,
  bedOccupancyMax: 0.8,

  // Warehouse paperwork, and the weak and strong state
  weakFactor: 3.0,
  strongFactor: 0.4,
  consignmentsMin: 2,
  consignmentsMax: 6,
  consignmentDays: 45,
  unconfirmedRate: 0.18,
  unconfirmedCap: 0.5,
  recentDays: 12,
  shortRate: 0.11,
  shortCap: 0.35,
  overRate: 0.13,
  overCap: 0.4,

  // Centres made to misreport
  gamingShare: 0.08,
  gamingMinHistory: 70,
  gamingWindowMin: 60,
  gamingWindowMax: 200,
  gamingZeroFootfall: 0.6,
  gamingNoReply: 0.45,
  normalNoReply: 0.08,

  // Staff check-ins
  attendanceDays: 30,
  phcRoster: 4,
  chcRoster: 8,
  gpsShare: 0.7,
  ussdShare: 0.18,
  ivrShare: 0.12,
  attendRateMin: 0.7,
  attendRateMax: 0.93,
  absenceRunShare: 0.45,
  absenceRunMin: 2,
  absenceRunMax: 5,
  pingChance: 0.45,

  // Ward photos
  wardPhotoDays: 21,
  wardReportRateMin: 0.6,
  wardReportRateMax: 0.95,
  wardOldCodeBelow: 0.04,
  wardIllegibleBelow: 0.07,
  wardElsewhereBelow: 0.1,
  wardRegisterBelow: 0.14,
} as const;
