/**
 * The mark, kept in one file because three entry points draw it: the landing
 * page header, the sign-in panel, and the checking-your-session splash.
 */
export default function BrandMark({ size = 30 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 26 26" aria-hidden="true">
      <rect width="26" height="26" rx="6" fill="#0b3d5c" />
      <path d="M13 6v14M6 13h14" stroke="#fff" strokeWidth="3" strokeLinecap="round" />
      <circle cx="19.5" cy="6.5" r="3" fill="#e0900e" stroke="#0b3d5c" strokeWidth="1.5" />
    </svg>
  );
}
