// The scenario fields a label may quote from.
const QUOTED_FIELDS = ['actor', 'trigger', 'standing_state', 'asset', 'outcome', 'narrative', 'threat'];

// The parts of `text` each label quotes, as [start, end, label], without overlaps.
export function quotedSpans(text, labels) {
  const lower = text.toLowerCase();
  const spans = [];
  for (const label of labels) {
    for (const piece of (label.evidence || '').split(/\.\.\.|…/)) {
      const quote = piece.trim().toLowerCase();
      if (quote.length < 3) continue;
      const start = lower.indexOf(quote);
      if (start >= 0) spans.push([start, start + quote.length, label]);
    }
  }
  spans.sort((a, b) => a[0] - b[0]);
  return spans.filter((span, index) => index === 0 || span[0] >= spans[index - 1][1]);
}

// The id a label's highlighted words carry, so its quote can scroll to them.
export function quoteId(scenario, label) {
  return `quote-${scenario.id}-${label.hcv_id}`;
}

// Whether any of a label's quoted words appear in the scenario text.
export function hasQuote(scenario, label) {
  return QUOTED_FIELDS.some((key) => scenario[key] && quotedSpans(scenario[key], [label]).length > 0);
}
