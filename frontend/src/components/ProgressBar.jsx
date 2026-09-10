// Renders the {done, total, failed, percent} shape every level of the
// pipeline's `progress` field returns (backend/pipeline/models.py's
// `_progress_dict` - experiments, pipeline runs and phase runs all embed
// exactly this). Purely presentational: it trusts `percent` rather than
// recomputing it, and clamps only against rendering, never against what the
// API actually reported.
export default function ProgressBar({ progress, label }) {
  const percent = Math.min(100, Math.max(0, progress?.percent ?? 0));
  const hasFailures = (progress?.failed ?? 0) > 0;

  return (
    <div className="w-full">
      {(label || progress) && (
        <div className="mb-1 flex items-center justify-between text-xs text-gray-500">
          <span>{label}</span>
          <span>
            {progress ? `${progress.done}/${progress.total}` : null} ({percent}%)
            {hasFailures && <span className="ml-1 text-red-600">{progress.failed} failed</span>}
          </span>
        </div>
      )}
      <div
        role="progressbar"
        aria-valuenow={percent}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={label || 'Progress'}
        className="h-2 w-full overflow-hidden rounded-full bg-gray-200"
      >
        <div
          className={`h-2 rounded-full ${hasFailures ? 'bg-amber-500' : 'bg-brand-600'}`}
          style={{ width: `${percent}%` }}
        />
      </div>
    </div>
  );
}
