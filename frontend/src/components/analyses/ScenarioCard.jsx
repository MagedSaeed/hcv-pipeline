import { useState } from 'react';
import ActionMenu from './ActionMenu';
import ManualTag, { SetAsideTag } from './ManualTag';
import { quoteId, quotedSpans } from './quotes';

const DETAILS = [
  ['actor', 'Who is involved'],
  ['trigger', 'Occasion'],
  ['standing_state', 'What was already true'],
  ['asset', 'What it reaches'],
  ['outcome', 'Outcome'],
];

function Highlighted({ text, labels, scenario }) {
  const parts = [];
  let at = 0;
  for (const [start, end, label] of quotedSpans(text, labels)) {
    parts.push(text.slice(at, start));
    parts.push(
      <mark
        key={start}
        data-quote={quoteId(scenario, label)}
        title={`${label.name} (${label.hcv_id})`}
        className="rounded bg-amber-100 px-0.5 text-gray-900 underline decoration-amber-400 decoration-2 underline-offset-2"
      >
        {text.slice(start, end)}
      </mark>
    );
    at = end;
  }
  parts.push(text.slice(at));
  return parts;
}

// `labels`, when given, highlights the words each label quotes.
export function ScenarioDetails({ scenario, labels = [] }) {
  const fields = [
    ...DETAILS,
    ['narrative', 'What happens'],
    ['threat', scenario.is_manual ? 'Threat, as entered' : 'Threat suggested by the generator'],
  ];
  return (
    <dl className="space-y-3 text-sm">
      {fields
        .filter(([key]) => scenario[key])
        .map(([key, title]) => (
          <div key={key}>
            <dt className="text-xs font-medium text-gray-500">{title}</dt>
            <dd dir="auto" className="whitespace-pre-wrap break-words text-gray-800">
              <Highlighted text={scenario[key]} labels={labels} scenario={scenario} />
            </dd>
          </div>
        ))}
    </dl>
  );
}

// The title is often just the occasion, so the outcome says what happens.
export function ScenarioSubtitle({ scenario }) {
  if (!scenario.outcome && !scenario.is_manual && !scenario.set_aside) return null;
  return (
    <span className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-gray-500">
      {scenario.outcome && (
        <span dir="auto" className="break-words">
          Outcome: {scenario.outcome}
        </span>
      )}
      {scenario.is_manual && <ManualTag />}
      {scenario.set_aside && <SetAsideTag />}
    </span>
  );
}

// Title first, details on demand; children always show under the title.
export default function ScenarioCard({ scenario, number, menuItems = [], defaultOpen = false, children }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <article
      id={`scenario-${scenario.id}`}
      className={`rounded-xl border border-gray-200 ${scenario.set_aside ? 'bg-gray-50 opacity-75' : 'bg-white'}`}
    >
      <div className="flex items-start gap-2 px-4 py-3">
        <button
          type="button"
          aria-expanded={open}
          onClick={() => setOpen((current) => !current)}
          className="flex min-w-0 flex-1 items-start gap-3 text-left"
        >
          {number && <span className="w-5 shrink-0 pt-0.5 text-sm tabular-nums text-gray-400">{number}</span>}
          <span className="min-w-0 flex-1">
            <span dir="auto" className="block break-words text-sm font-medium leading-6 text-gray-900">
              {scenario.title}
            </span>
            <ScenarioSubtitle scenario={scenario} />
          </span>
          <span aria-hidden="true" className="pt-0.5 text-gray-400">
            {open ? '▾' : '▸'}
          </span>
        </button>
        <ActionMenu label={`Actions for scenario ${number ?? scenario.scenario_id}`} items={menuItems} />
      </div>
      {children && <div className="border-t border-gray-100 px-4 py-3">{children}</div>}
      {open && (
        <div className="border-t border-gray-100 px-4 py-3">
          <ScenarioDetails scenario={scenario} />
        </div>
      )}
    </article>
  );
}
