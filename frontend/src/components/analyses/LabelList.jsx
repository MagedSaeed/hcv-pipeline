export function Evidence({ evidence, rationale }) {
  return (
    <div className="space-y-1.5 text-sm">
      {rationale && (
        <p dir="auto" className="break-words text-gray-700">
          {rationale}
        </p>
      )}
      {evidence && (
        <figure>
          <figcaption className="text-xs text-gray-500">From the scenario</figcaption>
          <blockquote dir="auto" className="break-words border-s-2 border-brand-200 ps-3 text-gray-600">
            {evidence}
          </blockquote>
        </figure>
      )}
    </div>
  );
}

export default function LabelList({ labels }) {
  const title = `Vulnerabilities found (${labels.length})`;
  return (
    <section aria-label={title} className="space-y-2">
      <h4 className="text-xs font-semibold uppercase tracking-wide text-brand-700">{title}</h4>
      <ul className="space-y-2">
        {labels.map((label) => (
          <li key={label.hcv_id} className="space-y-2 rounded-lg border border-brand-100 bg-brand-50/60 p-3">
            <p className="flex flex-wrap items-baseline gap-2">
              <span className="rounded bg-white px-1.5 py-0.5 font-mono text-xs text-brand-700 ring-1 ring-brand-200">
                {label.hcv_id}
              </span>
              <span className="text-sm font-semibold text-gray-900">{label.name}</span>
            </p>
            <Evidence evidence={label.evidence} rationale={label.rationale} />
          </li>
        ))}
      </ul>
    </section>
  );
}
