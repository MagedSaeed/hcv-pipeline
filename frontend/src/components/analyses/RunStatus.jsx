import { useEffect, useState } from 'react';
import { useCancelRun } from '../../api/analyses';
import { useAuth } from '../../contexts/auth-context';
import ErrorBanner from '../ErrorBanner';
import { linkButton } from './styles';

function Dots() {
  return (
    <span aria-hidden="true" className="inline-flex w-5">
      {[0, 250, 500].map((delay) => (
        <span key={delay} style={{ animationDelay: `${delay}ms` }} className="animate-dot motion-reduce:animate-none">
          .
        </span>
      ))}
    </span>
  );
}

// Time since the run was created, ticking each second.
function Elapsed({ since }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const seconds = Math.max(0, Math.floor((now - Date.parse(since)) / 1000));
  const minutes = Math.floor(seconds / 60);
  const rest = String(seconds % 60).padStart(2, '0');
  const spoken = `Running for ${minutes ? `${minutes} minute${minutes === 1 ? '' : 's'} ` : ''}${rest} seconds`;
  return (
    <span aria-label={spoken} className="font-mono tabular-nums text-brand-700">
      {minutes}:{rest}
    </span>
  );
}

// A run's progress while active, and how it ended afterwards.
export default function RunStatus({ analysisId, run, working, typical }) {
  const { isStaff } = useAuth();
  const cancel = useCancelRun(analysisId);
  if (!run) return null;

  if (run.status === 'queued' || run.status === 'running') {
    const finished = run.done + run.failed;
    const waiting = run.status === 'queued' && finished === 0;
    return (
      <div
        role="status"
        aria-label="Run progress"
        className="space-y-3 rounded-xl border border-brand-100 bg-brand-50 p-4"
      >
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="flex flex-wrap items-baseline gap-x-3 text-sm font-medium text-brand-900">
            <span>
              <span aria-hidden="true" className="mr-2 inline-block h-2 w-2 animate-pulse rounded-full bg-brand-600" />
              <span aria-live="polite">{waiting ? 'Waiting to start' : working}</span>
              <Dots />
            </span>
            {run.created_at && <Elapsed since={run.created_at} />}
            {typical && <span className="text-xs font-normal text-brand-800">{typical}</span>}
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
