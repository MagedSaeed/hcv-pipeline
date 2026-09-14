import { useId, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  useExperiments,
  usePhaseRuns,
  usePipelineRuns,
  usePromptTemplates,
  useScenarios,
  useSystemDescriptions,
} from '../api/queries';
import ErrorBanner from './ErrorBanner';
import GenerationProvenance from './GenerationProvenance';

const SEED_GROUP = 'seed';
const SEED_TITLE = 'Seed & hand-written scenarios';

const CARD_FIELDS = [
  ['actor', 'Actor'],
  ['trigger', 'Trigger'],
  ['standing_state', 'Standing state'],
  ['asset', 'Asset'],
  ['threat', 'Threat'],
  ['outcome', 'Outcome'],
];

function truncate(text, max = 140) {
  const flat = text.replace(/\s+/g, ' ').trim();
  return flat.length > max ? `${flat.slice(0, max - 1)}…` : flat;
}

function plural(count, word) {
  return `${count} ${count === 1 ? word : `${word}s`}`;
}

// Every scenario, grouped by where it came from: seed/hand-written first, then
// one group per generation run (newest first), labelled so a researcher can
// recognise the run.
export function useScenarioGroups(options = {}) {
  const scenariosQuery = useScenarios({}, options);
  const systemsQuery = useSystemDescriptions({}, options);
  const runsQuery = usePhaseRuns({ phase: 2 }, options);
  const promptsQuery = usePromptTemplates({}, options);
  const pipelineRunsQuery = usePipelineRuns({}, options);
  const experimentsQuery = useExperiments({}, options);

  const scenarioData = scenariosQuery.data;
  const systemData = systemsQuery.data;
  const runData = runsQuery.data;
  const promptData = promptsQuery.data;
  const pipelineRunData = pipelineRunsQuery.data;
  const experimentData = experimentsQuery.data;

  const groups = useMemo(() => {
    const byId = (data) => new Map((data?.results ?? []).map((row) => [row.id, row]));
    const systems = byId(systemData);
    const runs = byId(runData);
    const prompts = byId(promptData);
    const pipelineRuns = byId(pipelineRunData);
    const experiments = byId(experimentData);

    const rowsByGroup = new Map();
    for (const scenario of scenarioData?.results ?? []) {
      const key = scenario.generation_run == null ? SEED_GROUP : `run-${scenario.generation_run}`;
      const systemTitle = systems.get(scenario.system_description)?.title ?? '';
      const text = [
        scenario.scenario_id,
        systemTitle,
        ...CARD_FIELDS.map(([field]) => scenario[field]),
        scenario.narrative,
      ]
        .join(' ')
        .toLowerCase();
      if (!rowsByGroup.has(key)) rowsByGroup.set(key, []);
      rowsByGroup.get(key).push({ ...scenario, systemTitle, searchText: text });
    }

    const result = [];
    const seedRows = rowsByGroup.get(SEED_GROUP);
    if (seedRows) {
      result.push({ key: SEED_GROUP, source: SEED_GROUP, variant: '', title: SEED_TITLE, details: [], searchText: '', rows: seedRows });
    }

    const runGroups = [];
    for (const [key, rows] of rowsByGroup) {
      if (key === SEED_GROUP) continue;
      const runId = rows[0].generation_run;
      const run = runs.get(runId);
      const prompt = run ? prompts.get(run.prompt_template) : null;
      const repetition = run?.pipeline_run ? pipelineRuns.get(run.pipeline_run) : null;
      const experiment = repetition ? experiments.get(repetition.experiment) : null;
      let title = 'Generation run';
      if (run?.pipeline_run) {
        title = `${experiment?.name ?? 'Experiment'} · repetition ${repetition?.repetition_index ?? '…'}`;
      } else if (run) {
        title = 'Prompt test run';
      }
      const variant = run?.variant ?? '';
      if (variant) title = `${title} · Variant ${variant}`;
      const finished = run?.finished_at ? new Date(run.finished_at).toLocaleString() : '';
      const details = [prompt ? `${prompt.name} v${prompt.version}` : '', run?.model ?? '', finished].filter(Boolean);
      runGroups.push({
        key,
        runId,
        source: run?.pipeline_run ? 'experiment' : 'test',
        variant,
        title,
        details,
        searchText: [title, ...details].join(' ').toLowerCase(),
        sortKey: run?.finished_at ?? run?.started_at ?? '',
        rows,
      });
    }
    runGroups.sort((a, b) => b.sortKey.localeCompare(a.sortKey) || b.runId - a.runId);
    return [...result, ...runGroups];
  }, [scenarioData, systemData, runData, promptData, pipelineRunData, experimentData]);

  const queries = [scenariosQuery, systemsQuery, runsQuery, promptsQuery, pipelineRunsQuery, experimentsQuery];
  return {
    groups,
    total: scenarioData?.results?.length ?? 0,
    isLoading: queries.some((query) => query.isLoading),
    error: queries.find((query) => query.isError)?.error ?? null,
  };
}

export function NoScenariosNotice() {
  return (
    <div role="note" className="space-y-1 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900">
      <p className="font-medium">There are no scenarios to label yet.</p>
      <p>
        Add seed or hand-written scenarios on the{' '}
        <Link to="/resources?tab=corpus" className="font-medium underline">
          Corpus
        </Link>{' '}
        tab, or test a{' '}
        <Link to="/prompts" className="font-medium underline">
          Phase 2 prompt
        </Link>{' '}
        (or run an experiment) to generate some.
      </p>
    </div>
  );
}

function ScenarioDetails({ scenario }) {
  const fields = CARD_FIELDS.filter(([field]) => scenario[field]);
  return (
    <div className="ml-6 mt-1 space-y-2 rounded-md bg-gray-50 p-2 text-xs text-gray-800">
      {fields.length > 0 && (
        <dl className="grid grid-cols-1 gap-x-3 gap-y-1 sm:grid-cols-[auto_1fr]">
          {fields.map(([field, label]) => (
            <div key={field} className="contents">
              <dt className="font-medium text-gray-500">{label}</dt>
              <dd className="whitespace-pre-wrap">{scenario[field]}</dd>
            </div>
          ))}
        </dl>
      )}
      {scenario.narrative && (
        <div>
          <p className="font-medium text-gray-500">Narrative</p>
          <p className="whitespace-pre-wrap">{scenario.narrative}</p>
        </div>
      )}
      {fields.length === 0 && !scenario.narrative && <p className="text-gray-500">This scenario has no text yet.</p>}
      {scenario.generation_run != null && <GenerationProvenance phaseRunId={scenario.generation_run} />}
    </div>
  );
}

// mode="single": value is one scenario id, onChange(id).
// mode="multiple": value is an array of ids, onChange(ids).
export default function ScenarioPicker({ mode = 'single', value, onChange, label = 'Scenario' }) {
  const { groups, total, isLoading, error } = useScenarioGroups();
  const [search, setSearch] = useState('');
  const [source, setSource] = useState('');
  const [variant, setVariant] = useState('');
  const [openOverrides, setOpenOverrides] = useState({});
  const [expanded, setExpanded] = useState({});
  const name = useId();
  const multiple = mode === 'multiple';

  const selected = new Set(
    (multiple ? (value ?? []) : [value]).filter((id) => id != null && id !== '').map(Number)
  );

  if (isLoading) return <p className="text-xs text-gray-500">Loading scenarios…</p>;
  if (error) return <ErrorBanner error={error} />;
  if (total === 0) return <NoScenariosNotice />;

  const words = search.toLowerCase().split(/\s+/).filter(Boolean);
  const newestRunKey = groups.find((group) => group.key !== SEED_GROUP)?.key;
  const visibleGroups = groups
    .filter((group) => {
      if (source && group.source !== source) return false;
      return !variant || group.variant === variant;
    })
    .map((group) => ({
      ...group,
      visibleRows: group.rows.filter((row) =>
        words.every((word) => row.searchText.includes(word) || group.searchText.includes(word))
      ),
    }))
    .filter((group) => group.visibleRows.length > 0);
  const shownIds = visibleGroups.flatMap((group) => group.visibleRows.map((row) => row.id));

  function isOpen(group) {
    if (group.key in openOverrides) return openOverrides[group.key];
    return words.length > 0 || group.key === SEED_GROUP || group.key === newestRunKey;
  }

  function toggleGroup(group) {
    setOpenOverrides((current) => ({ ...current, [group.key]: !isOpen(group) }));
  }

  function setMany(ids, checked) {
    const current = (value ?? []).map(Number);
    if (checked) {
      onChange([...new Set([...current, ...ids])]);
    } else {
      const removed = new Set(ids);
      onChange(current.filter((id) => !removed.has(id)));
    }
  }

  const selectedScenario = !multiple
    ? groups.flatMap((group) => group.rows.map((row) => ({ row, group }))).find(({ row }) => selected.has(row.id))
    : null;

  return (
    <fieldset className="space-y-2">
      <legend className="text-xs font-medium text-gray-700">{label}</legend>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
        <input
          type="search"
          aria-label="Search scenarios"
          placeholder="Search text, system, prompt, model…"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          className="rounded-md border border-gray-300 px-2 py-1 text-xs shadow-sm"
        />
        <select
          aria-label="Source"
          value={source}
          onChange={(event) => setSource(event.target.value)}
          className="rounded-md border border-gray-300 px-2 py-1 text-xs shadow-sm"
        >
          <option value="">All sources</option>
          <option value={SEED_GROUP}>{SEED_TITLE}</option>
          <option value="test">Prompt test runs</option>
          <option value="experiment">Experiments</option>
        </select>
        <select
          aria-label="Variant"
          value={variant}
          onChange={(event) => setVariant(event.target.value)}
          className="rounded-md border border-gray-300 px-2 py-1 text-xs shadow-sm"
        >
          <option value="">Both variants</option>
          <option value="A">Variant A</option>
          <option value="B">Variant B</option>
        </select>
      </div>

      {multiple ? (
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <button
            type="button"
            onClick={() => setMany(shownIds, true)}
            disabled={shownIds.length === 0}
            className="rounded-md border border-gray-300 px-2 py-1 font-medium text-gray-700 hover:bg-gray-100 disabled:opacity-60"
          >
            Select all shown
          </button>
          <button
            type="button"
            onClick={() => onChange([])}
            disabled={selected.size === 0}
            className="rounded-md border border-gray-300 px-2 py-1 font-medium text-gray-700 hover:bg-gray-100 disabled:opacity-60"
          >
            Clear
          </button>
          <span className="text-gray-600">{selected.size} selected</span>
        </div>
      ) : (
        <p className="text-xs text-gray-600">
          {selectedScenario
            ? `Selected: ${selectedScenario.row.scenario_id}${selectedScenario.row.systemTitle ? ` · ${selectedScenario.row.systemTitle}` : ''} (${selectedScenario.group.title})`
            : 'No scenario selected yet.'}
        </p>
      )}

      <ul className="max-h-96 overflow-y-auto rounded-md border border-gray-200">
        {visibleGroups.length === 0 && <li className="px-2 py-2 text-xs text-gray-500">No scenarios match.</li>}
        {visibleGroups.map((group) => {
          const open = isOpen(group);
          const groupIds = group.visibleRows.map((row) => row.id);
          const chosenInGroup = groupIds.filter((id) => selected.has(id)).length;
          const filtered = group.visibleRows.length !== group.rows.length;
          return (
            <li key={group.key} className="border-b border-gray-100 last:border-b-0">
              <div className="flex items-start gap-2 bg-gray-50 px-2 py-1.5">
                {multiple && (
                  <input
                    type="checkbox"
                    aria-label={`Select all in ${group.title}`}
                    checked={chosenInGroup > 0 && chosenInGroup === groupIds.length}
                    ref={(element) => {
                      if (element) element.indeterminate = chosenInGroup > 0 && chosenInGroup < groupIds.length;
                    }}
                    onChange={(event) => setMany(groupIds, event.target.checked)}
                    className="mt-0.5"
                  />
                )}
                <button
                  type="button"
                  onClick={() => toggleGroup(group)}
                  aria-expanded={open}
                  className="flex min-w-0 flex-1 items-start justify-between gap-2 text-left text-xs"
                >
                  <span className="min-w-0">
                    <span className="block font-medium text-gray-900">{group.title}</span>
                    <span className="block text-gray-600">
                      {[
                        filtered
                          ? `${group.visibleRows.length} of ${plural(group.rows.length, 'scenario')}`
                          : plural(group.rows.length, 'scenario'),
                        ...group.details,
                      ].join(' · ')}
                    </span>
                  </span>
                  <span className="shrink-0 text-gray-400">{open ? 'Hide' : 'Show'}</span>
                </button>
              </div>
              {open && (
                <ul className="divide-y divide-gray-100">
                  {group.visibleRows.map((row) => {
                    const inputId = `${name}-${row.id}`;
                    const snippet = truncate([row.threat, row.outcome].filter(Boolean).join(' — ') || row.narrative);
                    const detailsOpen = Boolean(expanded[row.id]);
                    return (
                      <li key={row.id} className={`px-2 py-1.5 ${selected.has(row.id) ? 'bg-brand-50' : ''}`}>
                        <div className="flex items-start gap-2">
                          <input
                            id={inputId}
                            type={multiple ? 'checkbox' : 'radio'}
                            name={multiple ? undefined : name}
                            checked={selected.has(row.id)}
                            onChange={(event) =>
                              multiple ? setMany([row.id], event.target.checked) : onChange(row.id)
                            }
                            className="mt-0.5"
                          />
                          <label htmlFor={inputId} className="min-w-0 flex-1 cursor-pointer text-xs">
                            <span className="block text-gray-700">
                              <span className="font-mono font-medium text-gray-900">{row.scenario_id}</span>
                              {row.systemTitle && ` · ${row.systemTitle}`}
                            </span>
                            {snippet && <span className="block truncate text-gray-500">{snippet}</span>}
                          </label>
                          <button
                            type="button"
                            onClick={() => setExpanded((current) => ({ ...current, [row.id]: !detailsOpen }))}
                            aria-expanded={detailsOpen}
                            aria-label={`${detailsOpen ? 'Hide details' : 'Details'} for ${row.scenario_id}`}
                            className="shrink-0 text-xs font-medium text-brand-700 hover:underline"
                          >
                            {detailsOpen ? 'Hide details' : 'Details'}
                          </button>
                        </div>
                        {detailsOpen && <ScenarioDetails scenario={row} />}
                      </li>
                    );
                  })}
                </ul>
              )}
            </li>
          );
        })}
      </ul>
    </fieldset>
  );
}
