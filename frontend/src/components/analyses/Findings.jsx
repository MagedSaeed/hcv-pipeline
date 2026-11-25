import { Evidence } from './LabelList';
import { ScenarioSubtitle } from './ScenarioCard';
import { linkButton } from './styles';

const THREAT_STYLES = {
  STRIDE: 'border-amber-300 bg-amber-50 text-amber-900',
  LINDDUN: 'border-violet-300 bg-violet-50 text-violet-900',
};

function plural(count, word) {
  return `${count} ${count === 1 ? word : `${word}s`}`;
}

export function ThreatLegend() {
  return (
    <p className="flex flex-wrap items-center gap-2 text-xs text-gray-500">
      <span className={`h-3 w-3 rounded-full border ${THREAT_STYLES.STRIDE}`} aria-hidden="true" />
      Security threat (STRIDE)
      <span className={`ml-2 h-3 w-3 rounded-full border ${THREAT_STYLES.LINDDUN}`} aria-hidden="true" />
      Privacy threat (LINDDUN)
    </p>
  );
}

function Occurrence({ occurrence, onShow }) {
  return (
    <div className="space-y-2">
      <div>
        <p dir="auto" className="break-words text-sm font-medium text-gray-800">
          {occurrence.title}
        </p>
        <ScenarioSubtitle scenario={occurrence} />
      </div>
      <Evidence evidence={occurrence.evidence} rationale={occurrence.rationale} />
      {onShow && (
        <button type="button" onClick={() => onShow(occurrence.scenario)} className={linkButton}>
          See how it could happen →
        </button>
      )}
    </div>
  );
}

// `onShow` is absent on paper, where every occurrence is written out.
// `bare` drops the card frame, for a finding shown inside a detail pane.
export function Finding({ finding, onShow, bare = false }) {
  const [first, ...more] = finding.occurrences;
  const manual = finding.manual_count > 0 ? ` · ${finding.manual_count} added manually` : '';

  return (
    <article
      className={`space-y-4 print:break-inside-avoid ${bare ? '' : 'rounded-xl border border-gray-200 bg-white p-5'}`}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h3 className="font-semibold text-gray-900">{finding.name}</h3>
        <p className="text-xs text-gray-500">{`found in ${plural(finding.scenario_count, 'scenario')}${manual}`}</p>
      </div>
      {finding.only_manual && <p className="text-xs font-medium text-teal-800">Only in manually added scenarios</p>}

      <dl className="grid gap-x-4 gap-y-2 text-sm sm:grid-cols-[5rem_1fr]">
        <dt className="text-gray-500">Enables</dt>
        <dd>
          {finding.threats.length === 0 ? (
            <span className="text-gray-600">No threat mapping defined for this vulnerability</span>
          ) : (
            <ul className="flex flex-wrap gap-1.5">
              {finding.threats.map(({ name, framework, qualifier }) => (
                <li
                  key={`${framework}-${name}`}
                  data-framework={framework}
                  title={framework}
                  className={`rounded-full border px-2.5 py-0.5 text-xs font-medium ${THREAT_STYLES[framework]}`}
                >
                  <span>{name}</span>
                  <span className="sr-only"> ({framework})</span>
                  {qualifier && <span className="font-normal"> ({qualifier})</span>}
                </li>
              ))}
            </ul>
          )}
        </dd>
        {finding.elements.length > 0 && (
          <>
            <dt className="text-gray-500">At risk</dt>
            <dd className="text-gray-800">{finding.elements.join('; ')}</dd>
          </>
        )}
        <dt className="text-gray-500">Why</dt>
        <dd className="min-w-0 space-y-4">
          {first && <Occurrence occurrence={first} onShow={onShow} />}
          {!onShow && more.map((row) => <Occurrence key={row.scenario} occurrence={row} />)}
          {onShow && more.length > 0 && (
            <details>
              <summary className="cursor-pointer text-sm text-gray-600">
                And in {plural(more.length, 'more scenario')}
              </summary>
              <div className="mt-3 space-y-4">
                {more.map((row) => (
                  <Occurrence key={row.scenario} occurrence={row} onShow={onShow} />
                ))}
              </div>
            </details>
          )}
        </dd>
      </dl>
    </article>
  );
}
