import { useId } from "react";

/** Vector brand mark combining the Q lens silhouette with an IgG antibody motif. */
export function BrandMark({className}: {className?: string}) {
  const gradientId = useId();
  return <span className={className} aria-hidden="true">
    <svg viewBox="0 0 64 64" focusable="false" aria-hidden="true">
      <defs>
        <linearGradient id={gradientId} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#5d4eb6"/>
          <stop offset="100%" stopColor="#483b8d"/>
        </linearGradient>
      </defs>
      <rect width="64" height="64" rx="18" fill={`url(#${gradientId})`}/>
      <rect x="0.75" y="0.75" width="62.5" height="62.5" rx="17.25" fill="none" stroke="#ffffff" strokeOpacity="0.14" strokeWidth="1.5"/>
      <circle cx="29.5" cy="28.5" r="17" fill="none" stroke="#ffffff" strokeWidth="4.6"/>
      <path d="M 35.0 34.0 L 50.5 49.5" fill="none" stroke="#ffffff" strokeWidth="4.8" strokeLinecap="round"/>
      <path d="M 34.2 21.2 L 35.0 34.0 L 22.2 33.2" fill="none" stroke="#ffffff" strokeWidth="3.8" strokeLinecap="round" strokeLinejoin="round"/>
      <path d="M 39.5 28.8 L 39.1 22.4 M 29.8 38.5 L 23.4 38.1" fill="none" stroke="#6ee7c8" strokeWidth="2.9" strokeLinecap="round"/>
      <circle cx="26.5" cy="25.5" r="3.4" fill="#6ee7c8"/>
    </svg>
  </span>;
}
