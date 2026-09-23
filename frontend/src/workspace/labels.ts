/**
 * English and Hindi, side by side, as inline pairs.
 *
 * There is no i18n layer in this project and there is not going to be one for
 * this stage (decided 2026-09-21): the officer app already carries its Hindi
 * inline, and a translation runtime for one screen would be more machinery
 * than text. The pairs live here rather than in the components so that the
 * two languages cannot drift apart in a diff that only touches one of them.
 *
 * `both()` is the default rendering. Where space genuinely does not allow two
 * languages — inside a 360px table cell — use `en()` and put the Hindi on the
 * label above it, never drop it silently.
 */

export const L = {
  // Navigation
  medicines: ["Medicines", "दवाइयाँ"],
  orders: ["Orders", "ऑर्डर"],
  beds: ["Beds", "बिस्तर"],
  attendance: ["Attendance", "उपस्थिति"],
  signOut: ["Sign out", "साइन आउट"],

  // Stock position
  inStock: ["In stock", "उपलब्ध स्टॉक"],
  daysOfCover: ["Days of cover", "कितने दिन का स्टॉक"],
  runsOut: ["Runs out about", "लगभग समाप्त"],
  notEnoughReadings: ["Not enough readings to estimate", "अनुमान के लिए पर्याप्त रिकॉर्ड नहीं"],

  // Verification
  countedByHand: ["Counted by hand", "हाथ से गिना गया"],
  confirmedOnDelivery: ["Confirmed on delivery", "डिलीवरी पर पुष्टि"],
  reportedByPhone: ["Reported by phone", "फ़ोन से दर्ज"],
  notYetReported: ["Not yet reported", "अभी तक दर्ज नहीं"],
  dataConfidence: ["Data confidence", "डेटा विश्वसनीयता"],

  // Today's team
  todaysTeam: ["Today's team", "आज की टीम"],
  patientsLogged: ["Patients logged today", "आज दर्ज मरीज़"],

  // Actions
  findSupply: ["Find supply", "आपूर्ति खोजें"],
  requestStock: ["Request stock", "स्टॉक माँगें"],
  confirmReceipt: ["Confirm receipt", "प्राप्ति की पुष्टि करें"],
  print: ["Print", "प्रिंट करें"],

  // Your own record
  yourAttendance: ["Your attendance", "आपकी उपस्थिति"],
  dayByDay: ["Day by day", "दिन-प्रतिदिन"],
  noCheckIn: ["No check-in recorded", "कोई उपस्थिति दर्ज नहीं"],
  randomChecks: ["Random verification", "आकस्मिक जाँच"],
  onlyYou: ["Only you can see this page.", "यह पृष्ठ केवल आप देख सकते हैं।"],

  // Beds
  bedsOccupied: ["Beds occupied", "भरे हुए बिस्तर"],
  bedsFree: ["Beds free", "खाली बिस्तर"],
  todaysCode: ["Today's ward code", "आज का वार्ड कोड"],
  lastWardReport: ["Last ward report", "पिछली वार्ड रिपोर्ट"],
} as const;

export type LabelKey = keyof typeof L;

/** "Medicines · दवाइयाँ" — the default. */
export const both = (k: LabelKey): string => `${L[k][0]} · ${L[k][1]}`;

/** English alone, for places where two languages genuinely will not fit. */
export const en = (k: LabelKey): string => L[k][0];

/** Hindi alone, for a label whose English sits immediately beside it. */
export const hi = (k: LabelKey): string => L[k][1];
