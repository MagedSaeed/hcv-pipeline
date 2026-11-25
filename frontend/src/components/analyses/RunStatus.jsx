import { useCancelRun } from '../../api/analyses';
import { useAuth } from '../../contexts/auth-context';
import ErrorBanner from '../ErrorBanner';
import { linkButton } from './styles';

// A run's progress while active, and how it ended afterwards.
export default function RunStatus({ analysisId, run, working }) {
  const { isStaff } = useAuth();
  const cancel = useCancelRun(analysisId);
  if (!run) return null;

  if (run.status === 'queued' || run.status === 'running') {
    const finished = run.done + run.failed;
    const waiting = run.status === 'queued' && finished === 0;
    return (
      <div className="space-y-3 rounded-xl border border-brand-100 bg-brand-50 p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p aria-live="polite" className="text-sm font-medium text-brand-900">
            <span aria-hidden="true" className="mr-2 inline-block h-2 w-2 animate-pulse rounded-full bg-brand-600" />
            {waiting ? 'Waiting to start…' : working}
          </p>
          {isStaff && (
            <button type="button" disabled={cancel.isPending} onClick={() => cancel.mutate()} className={linkButton}>
              Cancel
            </button>
          )}
        </div>
        {run.total > 1 && (
          <div
            role="progressbar"
            aria-label="Progress"
            aria-valuemin={0}
            aria-valuemax={run.total}
            aria-valuenow={finished}
            className="h-1.5 overflow-hidden rounded-full bg-brand-100"
          >
            <div className="h-full bg-brand-600" style={{ width: `${(finished / run.total) * 100}%` }} />
          </div>
        )}
        <p className="text-xs text-brand-800">You can close this page. The work continues.</p>
        {cancel.isError && <ErrorBanner error={cancel.error} />}
      </div>
    );
  }

  if (run.status === 'failed') {
    return <ErrorBanner error={{ message: run.error || 'The run failed. Try again.' }} />;
  }
  if (run.status === 'cancelled') return <p className="text-sm text-gray-600">The last run was cancelled.</p>;
  if (run.notice) return <p className="rounded-md bg-amber-50 p-3 text-sm text-amber-900">{run.notice}</p>;
  return null;
}
