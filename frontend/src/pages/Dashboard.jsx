import { Link } from 'react-router-dom';
import { RUN_STATUSES, runsRefetchInterval, useExperiments, usePipelineRuns } from '../api/queries';
import EmptyState from '../components/EmptyState';
import ErrorBanner from '../components/ErrorBanner';
import ProgressBar from '../components/ProgressBar';
import StatusBadge from '../components/StatusBadge';

// Experiment (backend/pipeline/serializers.py's ExperimentSerializer)
// carries no status field of its own - only real PipelineRun/PhaseRun rows
// do. This derives a coarse status word from what an experiment DOES
// carry (run_count, and progress - the roll-up of every LLM call any of
// its runs has issued) so the same StatusBadge vocabulary can label a row
// here too, without a page inventing its own colour for it.
function experimentStatus(experiment) {
  if (!experiment.run_count) return RUN_STATUSES.PENDING;
  if (experiment.progress.percent < 100) return RUN_STATUSES.RUNNING;
  return experiment.progress.failed > 0 ? RUN_STATUSES.FAILED : RUN_STATUSES.COMPLETED;
}

export default function Dashboard() {
  // The runs list is the authoritative "is anything still active?" signal:
  // PipelineRun.status is real backend state, unlike an experiment's own
  // progress rollup, which reads 0/0 (0%) for a run that is merely
  // PENDING/QUEUED and has not issued a single LLM call yet - polling off
  // that number alone would never even notice a just-launched batch.
  // usePipelineRuns already stops polling ITSELF once every run on the page
  // is terminal (see runsRefetchInterval, src/api/queries.js); the same
  // verdict drives the experiments list below too, so its progress bars
  // keep moving for exactly as long as there is something to watch, and
  // both queries go idle together once nothing is left running.
  const runsQuery = usePipelineRuns();
  const pollInterval = runsRefetchInterval(runsQuery.data?.results);

  const experimentsQuery = useExperiments({}, { refetchInterval: pollInterval });

  const experiments = experimentsQuery.data?.results ?? [];
  const runs = runsQuery.data?.results ?? [];
  const experimentNameById = new Map(experiments.map((experiment) => [experiment.id, experiment.name]));

  return (
    <div className="space-y-10">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold text-gray-900">Dashboard</h1>
        <Link
          to="/experiments/new"
          className="rounded-md bg-brand-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-brand-700"
        >
          New experiment
        </Link>
      </div>

      <section aria-labelledby="experiments-heading" className="space-y-3">
        <h2 id="experiments-heading" className="text-lg font-medium text-gray-900">
          Experiments
        </h2>

        {experimentsQuery.isLoading && <p className="text-sm text-gray-500">Loading experiments…</p>}
        {experimentsQuery.isError && <ErrorBanner error={experimentsQuery.error} />}

        {experimentsQuery.isSuccess && experiments.length === 0 && (
          <EmptyState
            title="No experiments yet"
            message="Create an experiment to start comparing variant A (taxonomy withheld) against variant B (taxonomy supplied)."
            action={
              <Link
                to="/experiments/new"
                className="inline-flex rounded-md bg-brand-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-brand-700"
              >
                New experiment
              </Link>
            }
          />
        )}

        {experimentsQuery.isSuccess && experiments.length > 0 && (
          <ul className="divide-y divide-gray-200 rounded-lg border border-gray-200 bg-white">
            {experiments.map((experiment) => (
              <li key={experiment.id} className="flex items-center justify-between gap-6 px-4 py-3">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-gray-900">{experiment.name}</p>
                  <p className="text-xs text-gray-500">
                    {experiment.run_count} {experiment.run_count === 1 ? 'run' : 'runs'}
                  </p>
                </div>
                <div className="w-48 shrink-0">
                  <ProgressBar progress={experiment.progress} />
                </div>
                <StatusBadge status={experimentStatus(experiment)} />
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="recent-runs-heading" className="space-y-3">
        <h2 id="recent-runs-heading" className="text-lg font-medium text-gray-900">
          Recent runs
        </h2>

        {runsQuery.isLoading && <p className="text-sm text-gray-500">Loading runs…</p>}
        {runsQuery.isError && <ErrorBanner error={runsQuery.error} />}

        {runsQuery.isSuccess && runs.length === 0 && (
          <p className="text-sm text-gray-500">No runs have been launched yet.</p>
        )}

        {runsQuery.isSuccess && runs.length > 0 && (
          <ul className="divide-y divide-gray-200 rounded-lg border border-gray-200 bg-white">
            {runs.map((run) => (
              <li key={run.id} className="flex items-center justify-between gap-6 px-4 py-3">
                <Link to={`/pipeline-runs/${run.id}`} className="min-w-0 flex-1 hover:underline">
                  <p className="truncate text-sm font-medium text-gray-900">
                    {experimentNameById.get(run.experiment) ?? `Experiment #${run.experiment}`}
                  </p>
                  <p className="text-xs text-gray-500">Repetition {run.repetition_index}</p>
                </Link>
                <div className="w-48 shrink-0">
                  <ProgressBar progress={run.progress} />
                </div>
                <StatusBadge status={run.status} />
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
