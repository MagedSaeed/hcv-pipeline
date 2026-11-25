import ActionMenu from './ActionMenu';
import MasterDetail from './MasterDetail';
import ScenarioCard, { ScenarioDetails, ScenarioSubtitle } from './ScenarioCard';
import useWideScreen from './useWideScreen';

const BREATHE = 'animate-breathe motion-reduce:animate-none';

// How many vulnerabilities a scenario has; grey when its labels are out of date.
function FoundCount({ scenario }) {
  const count = scenario.labels?.length ?? 0;
  if (count === 0 || scenario.set_aside) return null;
  const words = `${count} ${count === 1 ? 'vulnerability' : 'vulnerabilities'}`;
  const stale = scenario.label_status === 'stale';
  return (
    <span
      aria-label={stale ? `${words}, out of date` : words}
      className={`mt-1.5 inline-block rounded-full border px-2 py-0.5 text-xs tabular-nums ${
        stale ? 'border-gray-200 bg-gray-50 text-gray-500' : 'border-brand-100 bg-brand-50 text-brand-700'
      }`}
    >
      {words}
    </span>
  );
}

// Scenarios as a list beside the chosen one on wide screens, as cards on phones.
// `renderBelow` adds a step's own content, such as labels, under each scenario.
// `numberOf` keeps a scenario's number when the list shown is a subset.
// `workingId` is the scenario a model is on now; its row fades softly.
export default function ScenarioBrowser({
  scenarios,
  selectedId,
  onSelect,
  menuFor = () => [],
  renderBelow,
  itemNote,
  numberOf,
  showLabels = false,
  workingId = null,
}) {
  const wide = useWideScreen();
  const number = numberOf ?? ((scenario) => scenarios.indexOf(scenario) + 1);

  if (!wide) {
    return (
      <div className="space-y-3">
        {scenarios.map((scenario) => (
          <ScenarioCard
            key={scenario.id}
            scenario={scenario}
            number={number(scenario)}
            menuItems={menuFor(scenario)}
            defaultOpen={scenario.id === selectedId}
          >
            {renderBelow?.(scenario)}
          </ScenarioCard>
        ))}
      </div>
    );
  }

  const selected = scenarios.find((scenario) => scenario.id === selectedId) ?? scenarios[0];
  return (
    <MasterDetail
      listLabel="Scenarios"
      detailLabel="Selected scenario"
      items={scenarios.map((scenario) => ({ key: scenario.id, scenario }))}
      selectedKey={selected?.id}
      onSelect={onSelect}
      itemLabel={({ scenario }) => `${number(scenario)}. ${scenario.title}`}
      renderCompact={({ scenario }) => (
        <span
          className={`text-sm tabular-nums ${scenario.set_aside ? 'text-gray-300' : 'text-gray-700'} ${
            scenario.id === workingId ? BREATHE : ''
          }`}
        >
          {number(scenario)}
          {scenario.is_manual && <span aria-hidden="true" className="ml-0.5 text-teal-600">•</span>}
        </span>
      )}
      renderItem={({ scenario }) => (
        <span className={`flex gap-3 ${scenario.set_aside ? 'opacity-60' : ''} ${scenario.id === workingId ? BREATHE : ''}`}>
          <span className="w-5 shrink-0 text-sm tabular-nums text-gray-400">{number(scenario)}</span>
          <span className="min-w-0 flex-1">
            <span dir="auto" className="line-clamp-2 break-words text-sm font-medium text-gray-900">
              {scenario.title}
            </span>
            <ScenarioSubtitle scenario={{ ...scenario, outcome: '' }} />
            {itemNote?.(scenario) && <span className="mt-0.5 block text-xs text-gray-500">{itemNote(scenario)}</span>}
            {showLabels && <FoundCount scenario={scenario} />}
            {scenario.id === workingId && <span className="sr-only">(being checked)</span>}
          </span>
        </span>
      )}
    >
      {selected && (
        <div className="space-y-5">
          <div className="flex items-start gap-3">
            <div className="min-w-0 flex-1">
              {/* The title is a cut of the occasion, shown in full below, so the number heads it. */}
              <h3 className="text-base font-semibold text-gray-900">Scenario {number(selected)}</h3>
              <ScenarioSubtitle scenario={selected} />
            </div>
            <ActionMenu label={`Actions for scenario ${number(selected)}`} items={menuFor(selected)} />
          </div>
          {renderBelow && <div>{renderBelow(selected)}</div>}
          <div className="space-y-3 border-t border-gray-100 pt-4">
            <h4 className="text-xs font-semibold uppercase tracking-wide text-gray-500">The scenario</h4>
            <ScenarioDetails scenario={selected} labels={showLabels ? selected.labels : []} />
          </div>
        </div>
      )}
    </MasterDetail>
  );
}
