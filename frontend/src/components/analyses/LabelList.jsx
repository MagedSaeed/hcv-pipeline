import { hasQuote, quoteId } from './quotes';

// Scrolls to a quote's highlighted words and lets them glow for a moment.
function showQuote(id) {
  const mark = document.querySelector(`mark[data-quote="${id}"]`);
  if (!mark) return;
  const still = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  mark.scrollIntoView?.({ behavior: still ? 'auto' : 'smooth', block: 'center' });
  mark.classList.remove('animate-glow');
  void mark.offsetWidth;
  mark.classList.add('animate-glow');
}

// `onShow`, when given, makes the quote a link to its words in the scenario.
export function Evidence({ evidence, rationale, onShow }) {
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
            {onShow ? (
              <button
                type="button"
                onClick={onShow}
                aria-label={`Show “${evidence}” in the scenario`}
                title="Show in the scenario"
                className="text-start decoration-brand-300 underline-offset-2 hover:text-gray-900 hover:underline"
              >
                {evidence}
              </button>
            ) : (
              evidence
            )}
          </blockquote>
        </figure>
      )}
    </div>
  );
}

// `scenario`, when given, lets each quote scroll to its words in that scenario's text.
export default function LabelList({ labels, scenario }) {
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
            <Evidence
              evidence={label.evidence}
              rationale={label.rationale}
              onShow={scenario && hasQuote(scenario, label) ? () => showQuote(quoteId(scenario, label)) : undefined}
            />
          </li>
        ))}
      </ul>
    </section>
  );
}
