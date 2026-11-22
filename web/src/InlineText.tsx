import type { ReactNode } from "react";

export type ArtifactLink = {
  open?: () => void;
  downloadHref: string;
  name: string;
};

export type ArtifactResolver = (path: string) => ArtifactLink | null;

// Hidden delimiters let quoted selections recover the original recorded wording.
export function inlineText(text: string, resolveArtifact?: ArtifactResolver): ReactNode[] {
  const tokens =
    /(`[^`\n]+`|\*\*[^*\n]+\*\*|\*[^*\n]+\*|\[[^\]\n]+\]\((?:https?:\/\/|sandbox:|file:\/\/|\/)[^\s()<>]*(?:\([^\s()<>]*\)[^\s()<>]*)*\))/gi;
  return text.split(tokens).map((part, i) => {
    if (i % 2 === 0) return part;
    if (part.startsWith("`"))
      return (
        <span key={i}>
          <span hidden>`</span>
          <code>{part.slice(1, -1)}</code>
          <span hidden>`</span>
        </span>
      );
    if (part.startsWith("**"))
      return (
        <span key={i}>
          <span hidden>**</span>
          <strong>{inlineText(part.slice(2, -2), resolveArtifact)}</strong>
          <span hidden>**</span>
        </span>
      );
    if (part.startsWith("*"))
      return (
        <span key={i}>
          <span hidden>*</span>
          <em>{inlineText(part.slice(1, -1), resolveArtifact)}</em>
          <span hidden>*</span>
        </span>
      );

    const end = part.indexOf("](");
    const label = part.slice(1, end);
    const href = part.slice(end + 2, -1);

    if (/^(?:sandbox:|file:\/\/|\/)/i.test(href)) {
      const resolved = resolveArtifact?.(href);
      if (resolved) {
        return (
          <span key={i} className="inline-artifact">
            <span hidden>[</span>
            {resolved.open ? (
              <button
                type="button"
                className="inline-artifact-link"
                onClick={(event) => {
                  event.stopPropagation();
                  resolved.open?.();
                }}
                title={"Open " + resolved.name + " in viewer"}
              >
                {label}
              </button>
            ) : (
              <a
                href={resolved.downloadHref}
                download
                className="inline-artifact-link"
                onClick={(event) => event.stopPropagation()}
                title={"Download " + resolved.name}
              >
                {label}
              </a>
            )}
            <span hidden>{part.slice(end)}</span>
          </span>
        );
      }
      return (
        <span key={i} className="inline-artifact-missing" title="File was not recorded in this session">
          <span hidden>[</span>
          {label}
          <span hidden>{part.slice(end)}</span>
        </span>
      );
    }

    if (!URL.canParse(href)) return part;
    const url = new URL(href);
    if (url.protocol !== "http:" && url.protocol !== "https:") return part;
    if (url.username || url.password) return part;
    const doi = url.hostname === "doi.org" && /^10\.\d+\//.test(label);
    return (
      <span key={i}>
        <span hidden>[</span>
        <a
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          onClick={(event) => event.stopPropagation()}
          title={doi ? label : href}
          aria-label={doi ? "DOI " + label : undefined}
          className={doi ? "doi-source" : undefined}
        >
          {doi ? <span hidden>{label}</span> : label}
        </a>
        <span hidden>{part.slice(end)}</span>
      </span>
    );
  });
}
