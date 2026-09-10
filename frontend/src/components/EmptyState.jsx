// A generic "nothing here yet" panel. `action` is a ready-made node (almost
// always a <Link>) rather than an href/label pair, so a caller stays in
// control of routing instead of this component guessing how to navigate.
export default function EmptyState({ title, message, action }) {
  return (
    <div
      role="status"
      className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-gray-300 bg-white px-6 py-12 text-center"
    >
      <h2 className="text-sm font-semibold text-gray-900">{title}</h2>
      {message && <p className="max-w-sm text-sm text-gray-500">{message}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}
