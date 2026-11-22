import type { ReactNode } from "react";

// Hidden delimiters let quoted selections recover the original recorded wording.
export function inlineText(text: string): ReactNode[] {
  const tokens = /(`[^`\n]+`|\*\*[^*\n]+\*\*|\[[^\]\n]+\]\(https?:\/\/(?:[^\s()<>]|\([^\s()<>]*\))*\))/gi;
  return text.split(tokens).map((part, i) => {
    if (i % 2 === 0) return part;
    if (part.startsWith("`")) return <span key={i}><span hidden>`</span><code>{part.slice(1,-1)}</code><span hidden>`</span></span>;
    if (part.startsWith("**")) return <span key={i}><span hidden>**</span><strong>{inlineText(part.slice(2,-2))}</strong><span hidden>**</span></span>;

    const end = part.indexOf("](");
    const label = part.slice(1, end);
    const href = part.slice(end + 2, -1);
    if (!URL.canParse(href)) return part;
    const url = new URL(href);
    if (url.username || url.password) return part;
    const doi = url.hostname === "doi.org" && /^10\.\d+\//.test(label);
    return <span key={i}><span hidden>[</span><a href={href} target="_blank" rel="noopener noreferrer"
      onClick={event => event.stopPropagation()} title={doi ? label : href} aria-label={doi ? "DOI " + label : undefined} className={doi ? "doi-source" : undefined}>
      {doi ? <span hidden>{label}</span> : label}</a><span hidden>{part.slice(end)}</span></span>;
  });
}
