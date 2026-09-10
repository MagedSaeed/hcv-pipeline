import { RUN_STATUSES } from '../api/queries';

// The single place every run-status colour lives. Nothing else in this app
// is allowed to decide what colour a status gets - a page that wants a
// status shown renders <StatusBadge status={...} /> rather than picking a
// Tailwind class itself, so there is exactly one mapping to keep correct.
//
// pending/queued: neutral (nothing has happened yet, nothing to alarm on).
// running: active/informational (in progress right now).
// completed: positive.
// failed: negative - something errored.
// cancelled: its own colour, deliberately NOT the failed one. An operator
// who cancelled a run themselves must not read the badge as "this crashed" -
// see backend/pipeline/models.py's PipelineRun.Status/PhaseRun.Status,
// which keep CANCELLED a distinct terminal state from FAILED for the same
// reason.
const STYLES = {
  [RUN_STATUSES.PENDING]: 'bg-gray-100 text-gray-700',
  [RUN_STATUSES.QUEUED]: 'bg-gray-100 text-gray-700',
  [RUN_STATUSES.RUNNING]: 'bg-blue-100 text-blue-700',
  [RUN_STATUSES.COMPLETED]: 'bg-green-100 text-green-700',
  [RUN_STATUSES.FAILED]: 'bg-red-100 text-red-700',
  [RUN_STATUSES.CANCELLED]: 'bg-amber-100 text-amber-800',
};

const LABELS = {
  [RUN_STATUSES.PENDING]: 'Pending',
  [RUN_STATUSES.QUEUED]: 'Queued',
  [RUN_STATUSES.RUNNING]: 'Running',
  [RUN_STATUSES.COMPLETED]: 'Completed',
  [RUN_STATUSES.FAILED]: 'Failed',
  [RUN_STATUSES.CANCELLED]: 'Cancelled',
};

export default function StatusBadge({ status }) {
  const style = STYLES[status] ?? 'bg-gray-100 text-gray-700';
  const label = LABELS[status] ?? status;

  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${style}`}
    >
      {label}
    </span>
  );
}
