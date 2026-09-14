import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useExperiments, usePhaseRuns, usePipelineRuns, usePromptTemplates, useScenarios } from '../api/queries';
import ErrorBanner from './ErrorBanner';

// The finished phase 2 runs that saved at least one scenario, with what a
// researcher needs to recognise each one.
export function useLabellableRuns() {
  const runsQuery = usePhaseRuns({ phase: 2, status: 'completed' });
  const scenariosQuery = useScenarios({ origin: 'generated' });
  const promptsQuery = usePromptTemplates({ phase: 2 });
  const pipelineRunsQuery = usePipelineRuns();
  const experimentsQuery = useExperiments();

  const rows = useMemo(() => {
    const scenarioCounts = new Map();
    for (const scenario of scenariosQuery.data?.results ?? []) {
      scenarioCounts.set(scenario.generation_run, (scenarioCounts.get(scenario.generation_run) ?? 0) + 1);
    }
    const prompts = new Map((promptsQuery.data?.results ?? []).map((row) => [row.id, row]));
    const pipelineRuns = new Map((pipelineRunsQuery.data?.results ?? []).map((row) => [row.id, row]));
    const experiments = new Map((experimentsQuery.data?.results ?? []).map((row) => [row.id, row]));
    return (runsQuery.data?.results ?? [])
      .map((run) => {
        const prompt = prompts.get(run.prompt_template);
        const repetition = run.pipeline_run ? pipelineRuns.get(run.pipeline_run) : null;
        const experiment = repetition ? experiments.get(repetition.experiment) : null;
        return {
          ...run,
          scenarioCount: scenarioCounts.get(run.id) ?? 0,
          promptLabel: prompt ? `${prompt.name} v${prompt.version}` : `prompt #${run.prompt_template}`,
          sourceLabel: repetition
            ? `${experiment?.name ?? 'Experiment'} · repetition ${repetition.repetition_index}`
            : 'Prompt test run',
        };
      })
      .filter((run) => run.scenarioCount > 0)
      .sort((a, b) => (b.finished_at ?? '').localeCompare(a.finished_at ?? ''));
  }, [runsQuery.data, scenariosQuery.data, promptsQuery.data, pipelineRunsQuery.data, experimentsQuery.data]);

  const queries = [runsQuery, scenariosQuery, promptsQuery, pipelineRunsQuery, experimentsQuery];
  return {
    rows,
    isLoading: queries.some((query) => query.isLoading),
    error: queries.find((query) => query.isError)?.error ?? null,
  };
}

export function NoLabellableRunsNotice() {
  return (
    <div role="note" className="space-y-1 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900">
      <p className="font-medium">There are no generated scenarios to label yet.</p>
      <p>
        A labelling test labels the scenarios a finished generation run saved. Test a{' '}
        <Link to="/prompts" className="font-medium underline">
          Phase 2 prompt
        </Link>{' '}
        first (or run an experiment), then come back here.
      </p>
    </div>
  );
}

export default function SourceRunPicker({ rows, value, onChange, isLoading, error }) {
  const [search, setSearch] = useState('');
  const [variant, setVariant] = useState('');
  const [source, setSource] = useState('');

  if (isLoading) return <p className="text-xs text-gray-500">Loading generation runs…</p>;
  if (error) return <ErrorBanner error={error} />;

  const words = search.toLowerCase().split(/\s+/).filter(Boolean);
  const visible = rows.filter((run) => {
    if (variant && run.variant !== variant) return false;
    if (source === 'test' && run.pipeline_run) return false;
    if (source === 'experiment' && !run.pipeline_run) return false;
    const haystack = `${run.sourceLabel} ${run.promptLabel} ${run.model} #${run.id}`.toLowerCase();
    return words.every((word) => haystack.includes(word));
  });

  return (
    <fieldset className="space-y-2">
      <legend className="text-xs font-medium text-gray-700">Scenarios to label (from a finished generation run)</legend>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
        <input
          type="search"
          aria-label="Search generation runs"
          placeholder="Search prompt, model, experiment…"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          className="rounded-md border border-gray-300 px-2 py-1 text-xs shadow-sm sm:col-span-1"
        />
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
        <select
          aria-label="Run type"
          value={source}
          onChange={(event) => setSource(event.target.value)}
          className="rounded-md border border-gray-300 px-2 py-1 text-xs shadow-sm"
        >
          <option value="">Tests and experiments</option>
          <option value="test">Prompt tests only</option>
          <option value="experiment">Experiments only</option>
        </select>
      </div>
      <ul className="max-h-64 space-y-1 overflow-y-auto rounded-md border border-gray-200 p-1">
        {visible.length === 0 && <li className="px-2 py-1 text-xs text-gray-500">No runs match.</li>}
        {visible.map((run) => (
          <li key={run.id}>
            <label
              className={`flex cursor-pointer items-start gap-2 rounded px-2 py-1.5 text-xs ${
                String(run.id) === String(value) ? 'bg-brand-50 ring-1 ring-brand-300' : 'hover:bg-gray-50'
              }`}
            >
              <input
                type="radio"
                name="source-run"
                checked={String(run.id) === String(value)}
                onChange={() => onChange(String(run.id))}
                className="mt-0.5"
              />
              <span className="min-w-0">
                <span className="block font-medium text-gray-900">
                  {run.sourceLabel} · Variant {run.variant}
                </span>
                <span className="block text-gray-600">
                  {run.scenarioCount} {run.scenarioCount === 1 ? 'scenario' : 'scenarios'} · {run.promptLabel} ·{' '}
                  {run.model}
                  {run.finished_at && ` · ${new Date(run.finished_at).toLocaleString()}`}
                </span>
              </span>
            </label>
          </li>
        ))}
      </ul>
    </fieldset>
  );
}
