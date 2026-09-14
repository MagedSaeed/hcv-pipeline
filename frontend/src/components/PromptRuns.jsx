import { Link } from 'react-router-dom';
import { useExperiments, usePhaseRuns, usePipelineRuns, usePromptTemplates } from '../api/queries';
import ErrorBanner from './ErrorBanner';
import StatusBadge from './StatusBadge';

// Every run of a prompt (all its versions), newest first: prompt tests open in
// the test panel, experiment runs link to their repetition.
export default function PromptRuns({ promptName, phase, editId, selectedRunId, onOpenTestRun }) {
  const runsQuery = usePhaseRuns({ prompt_name: promptName, phase }, { enabled: Boolean(promptName) });
  const promptsQuery = usePromptTemplates({ phase });
  const pipelineRunsQuery = usePipelineRuns();
  const experimentsQuery = useExperiments();

  if (runsQuery.isLoading) return <p className="text-xs text-gray-500">Loading runs…</p>;
  if (runsQuery.isError) return <ErrorBanner error={runsQuery.error} />;

  const versions = new Map((promptsQuery.data?.results ?? []).map((row) => [row.id, row.version]));
  const repetitions = new Map((pipelineRunsQuery.data?.results ?? []).map((row) => [row.id, row]));
  const experiments = new Map((experimentsQuery.data?.results ?? []).map((row) => [row.id, row.name]));
  const runs = [...(runsQuery.data?.results ?? [])].sort((a, b) => b.id - a.id);

  if (runs.length === 0) {
    return <p className="text-xs text-gray-500">This prompt has not been run yet.</p>;
  }

  return (
    <ul className="max-h-80 space-y-1 overflow-y-auto rounded-md border border-gray-200 p-1">
      {runs.map((run) => {
        const repetition = run.pipeline_run ? repetitions.get(run.pipeline_run) : null;
        const when = run.finished_at ?? run.started_at;
        const summary = [
          versions.has(run.prompt_template) ? `v${versions.get(run.prompt_template)}` : null,
          run.variant ? `Variant ${run.variant}` : null,
          run.model,
          when ? new Date(when).toLocaleString() : null,
          run.total_cost != null ? `$${Number(run.total_cost).toFixed(4)}` : null,
        ]
          .filter(Boolean)
          .join(' · ');
        const title = run.pipeline_run
          ? `${experiments.get(repetition?.experiment) ?? 'Experiment'} · repetition ${repetition?.repetition_index ?? '…'}`
          : 'Prompt test';
        const selected = run.id === selectedRunId;

        return (
          <li
            key={run.id}
            className={`flex flex-wrap items-center justify-between gap-2 rounded px-2 py-1.5 text-xs ${
              selected ? 'bg-brand-50 ring-1 ring-brand-300' : 'hover:bg-gray-50'
            }`}
          >
            <div className="min-w-0">
              <p className="flex items-center gap-2 font-medium text-gray-900">
                <StatusBadge status={run.status} />
                {title}
              </p>
              <p className="mt-0.5 text-gray-600">{summary}</p>
            </div>
            {run.pipeline_run ? (
              <Link to={`/pipeline-runs/${run.pipeline_run}`} className="font-medium text-brand-700 hover:underline">
                Open run
              </Link>
            ) : onOpenTestRun ? (
              <button
                type="button"
                onClick={() => onOpenTestRun(run.id)}
                disabled={selected}
                className="rounded border border-gray-300 px-2 py-0.5 font-medium text-gray-700 hover:bg-gray-100 disabled:opacity-50"
              >
                {selected ? 'Showing' : 'Show'}
              </button>
            ) : (
              <Link
                to={`/prompts/${editId ?? run.prompt_template}/edit?testRun=${run.id}`}
                className="font-medium text-brand-700 hover:underline"
              >
                Open
              </Link>
            )}
          </li>
        );
      })}
    </ul>
  );
}
