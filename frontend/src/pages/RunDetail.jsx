import { useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useAuth } from '../contexts/auth-context';
import {
  isTerminalRunStatus,
  useCancelPipelineRun,
  useDeletePipelineRun,
  useExperiment,
  useExportPipelineRun,
  useFewShots,
  usePhaseRunCalls,
  usePhaseRuns,
  usePipelineRun,
  useRetryPhaseRun,
  useSystemDescriptions,
} from '../api/queries';
import CodeBlock from '../components/CodeBlock';
import ConfirmDialog from '../components/ConfirmDialog';
import ErrorBanner from '../components/ErrorBanner';
import ProgressBar from '../components/ProgressBar';
import StatusBadge from '../components/StatusBadge';

// The SAME states `POST /api/pipeline-runs/{id}/cancel/` itself accepts
// (pipeline/views.py's `_CANCELLABLE_RUN_STATUSES`) - anything else
// (completed/failed/already cancelled) is a 409, so offering the button
// there would only ever produce a doomed request.
const CANCELLABLE_STATUSES = new Set(['pending', 'queued', 'running']);

// Repeats pipeline/views.py's own CANCEL_CAVEAT in substance (plain text,
// no nested tags - see the component below for why) wherever cancel is
// offered, per spec: "the screen must repeat the caveat that cancelling
// marks a run dead but does not interrupt a worker already mid-call."
// Kept as one string (not split across elements) so it reads as a single
// node to both a screen reader and to a query that matches its full text.
const CANCEL_CAVEAT_TEXT =
  'Cancelling marks this run dead so it can be deleted; it does NOT interrupt a worker that may already be executing it — any write that worker is mid-way through (an LLMCall save, a status roll-up) may still land after cancel returns.';

const DELETE_CONFIRM_MESSAGE =
  'Deleting this run also deletes its generated material — every system description, scenario, LLM call and label produced by it — along with it. There is no per-row undo: deletion is per run, never per row.';

function filenameFromContentDisposition(headerValue) {
  if (!headerValue) return null;
  const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(headerValue);
  return match ? decodeURIComponent(match[1]) : null;
}

// Triggers a browser save of an axios `arraybuffer` export response,
// naming the file EXACTLY what the server's own Content-Disposition header
// said - never a name this app constructs (task 7: "Downloads use the
// API's Content-Disposition filename; do not construct one client-side").
// The `Blob` is built here, client-side, from the raw bytes rather than
// requested as `responseType: 'blob'` directly - see `useExportPipelineRun`
// (src/api/queries.js) for why that direct route breaks under test.
function downloadBlobResponse(response) {
  const contentType = response.headers?.['content-type'] ?? response.headers?.['Content-Type'];
  const disposition = response.headers?.['content-disposition'] ?? response.headers?.['Content-Disposition'];
  const filename = filenameFromContentDisposition(disposition);
  const blob = new Blob([response.data], contentType ? { type: contentType } : undefined);
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  if (filename) link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
  return filename;
}

function titleLookup(rows, key = 'title') {
  const map = new Map(rows.map((row) => [row.id, row[key]]));
  return (id) => (id == null ? null : (map.get(id) ?? `#${id}`));
}

function ConfigSummary({ testId, label, note, systems, fewshotNames, targetTitle, generateSystems, generateScenarios, nSystems, nScenariosPerSystem }) {
  return (
    <div data-testid={testId} className="space-y-2 rounded-md border border-gray-200 bg-white p-3">
      <p className="text-sm font-semibold text-gray-900">{label}</p>
      {note && <p className="text-xs text-gray-500">{note}</p>}
      <dl className="grid grid-cols-1 gap-2 text-xs text-gray-700 sm:grid-cols-2">
        <div>
          <dt className="font-medium text-gray-500">Seed systems</dt>
          <dd>{systems.length > 0 ? systems.join(', ') : '(none)'}</dd>
        </div>
        <div>
          <dt className="font-medium text-gray-500">Few-shots</dt>
          <dd>{fewshotNames.length > 0 ? fewshotNames.join(', ') : '(none)'}</dd>
        </div>
        <div>
          <dt className="font-medium text-gray-500">Target system</dt>
          <dd>{targetTitle ?? '(none — generating fresh systems)'}</dd>
        </div>
        <div>
          <dt className="font-medium text-gray-500">Generate</dt>
          <dd>
            {[generateSystems && 'systems', generateScenarios && 'scenarios'].filter(Boolean).join(' + ') || 'nothing (invalid)'}
          </dd>
        </div>
        <div>
          <dt className="font-medium text-gray-500">Counts</dt>
          <dd>
            {nSystems} {nSystems === 1 ? 'system' : 'systems'} × {nScenariosPerSystem} {nScenariosPerSystem === 1 ? 'scenario' : 'scenarios'}
          </dd>
        </div>
      </dl>
    </div>
  );
}

function IsolationBadge({ phaseRun }) {
  if (phaseRun.phase !== 2 || phaseRun.variant !== 'A') return null;
  if (phaseRun.leak_scan_ran) {
    return (
      <p className="text-xs font-semibold text-green-700">
        Isolation verified — guarded by taxonomy #{phaseRun.guarded_by.join(', ')}.
      </p>
    );
  }
  return (
    <p className="text-xs font-semibold text-red-700">
      Isolation NOT verified — the leak scan did not run for every call in this run. Do not treat
      this run as proof variant A stayed unbiased.
    </p>
  );
}

function CallRow({ callRow, onInspect }) {
  return (
    <li data-testid={`call-row-${callRow.id}`} className="space-y-1 rounded-md border border-gray-200 p-2 text-xs">
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium text-gray-900">
          Call #{callRow.sequence} {callRow.scenario != null && `· scenario #${callRow.scenario}`}
        </span>
        <button
          type="button"
          onClick={() => onInspect(callRow)}
          className="rounded border border-gray-300 px-2 py-0.5 text-xs font-medium text-gray-700 hover:bg-gray-100"
        >
          Inspect
        </button>
      </div>
      <p className={callRow.leak_scan_ran ? 'text-green-700' : 'text-gray-500'}>
        {callRow.leak_scan_ran
          ? `Leak scan ran — guarded by taxonomy #${callRow.guarded_by}.`
          : 'Leak scan did not run for this call.'}
      </p>
      {!callRow.parsed_ok && callRow.parse_error && (
        <p className="text-red-700">Parse error: {callRow.parse_error}</p>
      )}
    </li>
  );
}

function CallInspector({ callRow, onClose }) {
  return (
    <div className="space-y-2 rounded-md border border-brand-300 bg-brand-50 p-3">
      <div className="flex items-center justify-between">
        <p className="text-sm font-semibold text-gray-900">Call #{callRow.sequence} inspector</p>
        <button type="button" onClick={onClose} className="text-xs font-medium text-gray-600 hover:text-gray-900">
          Close
        </button>
      </div>
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
        <CodeBlock title="Rendered system prompt" code={callRow.rendered_system_prompt} />
        <CodeBlock title="Rendered user prompt" code={callRow.rendered_user_prompt} />
        <CodeBlock title="Verbatim response" code={callRow.raw_response} />
      </div>
    </div>
  );
}

function PhaseRunRow({ phaseRunRow, isStaff, retryPhaseRun, onRetry }) {
  const [expanded, setExpanded] = useState(false);
  const [inspecting, setInspecting] = useState(null);
  const callsQuery = usePhaseRunCalls(phaseRunRow.id, { enabled: expanded });
  const calls = callsQuery.data?.results ?? [];

  const canRetry = isStaff && phaseRunRow.phase === 3 && phaseRunRow.status === 'failed';
  const isRetryingThis = retryPhaseRun.variables === phaseRunRow.id;

  return (
    <li data-testid="phase-run-row" className="space-y-2 rounded-md border border-gray-200 bg-white p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="text-sm font-semibold text-gray-900">
            Phase {phaseRunRow.phase} — Variant {phaseRunRow.variant || '—'}
          </p>
          <p className="text-xs text-gray-500">
            {phaseRunRow.model} · temperature {phaseRunRow.temperature}
          </p>
        </div>
        <StatusBadge status={phaseRunRow.status} />
      </div>
      <ProgressBar progress={phaseRunRow.progress} label={`Phase ${phaseRunRow.phase}`} />
      <IsolationBadge phaseRun={phaseRunRow} />
      {phaseRunRow.status === 'failed' && phaseRunRow.error && (
        <p className="text-xs text-red-700">{phaseRunRow.error}</p>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => setExpanded((current) => !current)}
          className="rounded-md border border-gray-300 px-2 py-1 text-xs font-medium text-gray-700 hover:bg-gray-100"
        >
          {expanded ? 'Hide calls' : 'View calls'}
        </button>
        {canRetry && (
          <button
            type="button"
            onClick={() => onRetry(phaseRunRow.id)}
            disabled={retryPhaseRun.isPending && isRetryingThis}
            className="rounded-md border border-brand-600 px-2 py-1 text-xs font-medium text-brand-700 hover:bg-brand-50 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {retryPhaseRun.isPending && isRetryingThis ? 'Retrying…' : 'Retry'}
          </button>
        )}
      </div>

      {isRetryingThis && retryPhaseRun.isError && <ErrorBanner error={retryPhaseRun.error} />}

      {expanded && (
        <div className="space-y-2 border-t border-gray-200 pt-2">
          {callsQuery.isLoading && <p className="text-xs text-gray-500">Loading calls…</p>}
          {callsQuery.isError && <ErrorBanner error={callsQuery.error} />}
          {callsQuery.isSuccess && calls.length === 0 && (
            <p className="text-xs text-gray-500">No calls archived yet.</p>
          )}
          <ul className="space-y-2">
            {calls.map((callRow) => (
              <CallRow key={callRow.id} callRow={callRow} onInspect={setInspecting} />
            ))}
          </ul>
          {inspecting && <CallInspector callRow={inspecting} onClose={() => setInspecting(null)} />}
        </div>
      )}
    </li>
  );
}

export default function RunDetail() {
  const { id } = useParams();
  const runId = Number(id);
  const navigate = useNavigate();
  const { isStaff } = useAuth();

  const [confirmingDelete, setConfirmingDelete] = useState(false);

  const runQuery = usePipelineRun(runId);
  const run = runQuery.data ?? null;

  const experimentQuery = useExperiment(run?.experiment, { enabled: run != null });
  const phaseRunsQuery = usePhaseRuns({ pipeline_run: runId });
  const systemsQuery = useSystemDescriptions();
  const fewshotsQuery = useFewShots();

  const cancelRun = useCancelPipelineRun();
  const deleteRun = useDeletePipelineRun();
  const retryPhaseRun = useRetryPhaseRun();
  const exportRun = useExportPipelineRun();

  if (runQuery.isLoading) return <p className="text-sm text-gray-500">Loading run…</p>;
  if (runQuery.isError) return <ErrorBanner error={runQuery.error} />;
  if (!run) return null;

  const phaseRuns = phaseRunsQuery.data?.results ?? [];
  const experiment = experimentQuery.data ?? null;
  const systems = systemsQuery.data?.results ?? [];
  const fewshots = fewshotsQuery.data?.results ?? [];
  const systemTitle = titleLookup(systems, 'title');
  const fewshotName = titleLookup(fewshots, 'name');

  const pinned = run.config ?? {};
  const pinnedSystems = (pinned.seed_systems ?? []).map((system) => system.title);
  const pinnedFewshots = (pinned.fewshots ?? []).map((fewshot) => fewshot.name);
  const pinnedTarget = pinned.target_system ? pinned.target_system.title : null;

  const currentSystems = experiment ? experiment.seed_systems.map((sid) => systemTitle(sid)) : [];
  const currentFewshots = experiment ? experiment.fewshots.map((fid) => fewshotName(fid)) : [];
  const currentTarget = experiment?.target_system != null ? systemTitle(experiment.target_system) : null;

  const isCancellable = isStaff && CANCELLABLE_STATUSES.has(run.status);
  const isTerminal = isTerminalRunStatus(run.status);

  async function handleCancel() {
    try {
      await cancelRun.mutateAsync(runId);
    } catch {
      // Surfaced below via cancelRun.isError/error.
    }
  }

  async function handleRetry(phaseRunId) {
    try {
      await retryPhaseRun.mutateAsync(phaseRunId);
    } catch {
      // Surfaced inline, next to the phase run that was retried.
    }
  }

  async function handleExport(format) {
    try {
      const response = await exportRun.mutateAsync({ id: runId, format });
      downloadBlobResponse(response);
    } catch {
      // Surfaced below via exportRun.isError/error.
    }
  }

  async function handleDeleteConfirmed() {
    try {
      await deleteRun.mutateAsync(runId);
      navigate('/dashboard', { replace: true });
    } catch {
      // Surfaced below via deleteRun.isError/error - dialog stays open.
    }
  }

  return (
    <div className="space-y-8">
      <div>
        <p className="text-xs font-medium uppercase tracking-wide text-gray-500">
          {experiment?.name ?? `Experiment #${run.experiment}`}
        </p>
        <div className="mt-1 flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-semibold text-gray-900">Repetition {run.repetition_index}</h1>
          <StatusBadge status={run.status} />
        </div>
        <p className="mt-1 text-xs text-gray-500">
          Started {run.started_at ? new Date(run.started_at).toLocaleString() : '—'}
          {run.finished_at && ` · finished ${new Date(run.finished_at).toLocaleString()}`}
        </p>
        <div className="mt-2 max-w-md">
          <ProgressBar progress={run.progress} label="Overall" />
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        {/* task 10.2: before this link, the run id had to be typed by hand
            on the Labels screen - the walkthrough's own screenshot showed
            exactly that. `?run=` is read once at mount there as the
            initial filter, so the matrices for THIS repetition render
            immediately, no separate "Apply filters" click needed. */}
        <Link
          to={`/labels?run=${runId}`}
          className="rounded-md border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-100"
        >
          View labels
        </Link>
        <button
          type="button"
          onClick={() => handleExport('json')}
          disabled={exportRun.isPending}
          className="rounded-md border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-60"
        >
          Export JSON
        </button>
        <button
          type="button"
          onClick={() => handleExport('csv')}
          disabled={exportRun.isPending}
          className="rounded-md border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-60"
        >
          Export CSV
        </button>
        {isStaff && !isTerminal && (
          <button
            type="button"
            onClick={handleCancel}
            disabled={cancelRun.isPending}
            className="rounded-md border border-amber-600 px-3 py-1.5 text-sm font-medium text-amber-800 hover:bg-amber-50 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {cancelRun.isPending ? 'Cancelling…' : 'Cancel run'}
          </button>
        )}
        {isStaff && !confirmingDelete && (
          <button
            type="button"
            onClick={() => setConfirmingDelete(true)}
            className="rounded-md border border-red-600 px-3 py-1.5 text-sm font-medium text-red-700 hover:bg-red-50"
          >
            Delete run
          </button>
        )}
      </div>

      {isCancellable && <p className="max-w-2xl text-xs text-gray-500">{CANCEL_CAVEAT_TEXT}</p>}
      {cancelRun.isError && <ErrorBanner error={cancelRun.error} />}
      {cancelRun.isSuccess && (
        <p role="status" className="text-xs text-amber-800">
          {cancelRun.data.detail}
        </p>
      )}
      {exportRun.isError && <ErrorBanner error={exportRun.error} />}

      {confirmingDelete && (
        <ConfirmDialog
          title="Delete this run?"
          message={DELETE_CONFIRM_MESSAGE}
          confirmLabel="Confirm"
          cancelLabel="Never mind"
          pending={deleteRun.isPending}
          onConfirm={handleDeleteConfirmed}
          onCancel={() => setConfirmingDelete(false)}
        />
      )}
      {deleteRun.isError && <ErrorBanner error={deleteRun.error} />}

      <section className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <ConfigSummary
          testId="pinned-config"
          label="Pinned to this repetition"
          note="What repetition actually used, snapshotted once at launch — never re-read from the experiment afterward."
          systems={pinnedSystems}
          fewshotNames={pinnedFewshots}
          targetTitle={pinnedTarget}
          generateSystems={pinned.generate_systems}
          generateScenarios={pinned.generate_scenarios}
          nSystems={pinned.n_systems ?? '—'}
          nScenariosPerSystem={pinned.n_scenarios_per_system ?? '—'}
        />
        <ConfigSummary
          testId="current-config"
          label="Experiment's current configuration"
          note="May have changed since this repetition launched — this is NOT what this repetition used."
          systems={currentSystems}
          fewshotNames={currentFewshots}
          targetTitle={currentTarget}
          generateSystems={experiment?.generate_systems}
          generateScenarios={experiment?.generate_scenarios}
          nSystems={experiment?.n_systems ?? '—'}
          nScenariosPerSystem={experiment?.n_scenarios_per_system ?? '—'}
        />
      </section>

      <section aria-labelledby="phase-runs-heading" className="space-y-3">
        <h2 id="phase-runs-heading" className="text-lg font-medium text-gray-900">
          Phase runs
        </h2>
        {phaseRunsQuery.isLoading && <p className="text-sm text-gray-500">Loading phase runs…</p>}
        {phaseRunsQuery.isError && <ErrorBanner error={phaseRunsQuery.error} />}
        {phaseRunsQuery.isSuccess && phaseRuns.length === 0 && (
          <p className="text-sm text-gray-500">No phase runs recorded yet.</p>
        )}
        <ul className="space-y-3">
          {phaseRuns.map((phaseRunRow) => (
            <PhaseRunRow
              key={phaseRunRow.id}
              phaseRunRow={phaseRunRow}
              isStaff={isStaff}
              retryPhaseRun={retryPhaseRun}
              onRetry={handleRetry}
            />
          ))}
        </ul>
      </section>
    </div>
  );
}
