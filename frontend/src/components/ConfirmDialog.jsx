// An inline confirmation panel, not a native/browser dialog - consistent
// with the rest of this app (SystemDescriptionRow's edit/cancel toggle is
// inline too, not a modal) and trivially testable with React Testing
// Library, which cannot drive `window.confirm`. A caller renders this in
// place of its delete/destructive button once the user has asked to
// destroy something, and unmounts it again on `onCancel`.
export default function ConfirmDialog({
  title,
  message,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  onConfirm,
  onCancel,
  pending = false,
}) {
  return (
    <div
      role="alertdialog"
      aria-modal="true"
      aria-labelledby="confirm-dialog-title"
      className="space-y-3 rounded-md border border-red-300 bg-red-50 p-4"
    >
      <p id="confirm-dialog-title" className="text-sm font-semibold text-red-900">
        {title}
      </p>
      {message && <p className="text-sm text-red-800">{message}</p>}
      <div className="flex gap-2">
        <button
          type="button"
          onClick={onConfirm}
          disabled={pending}
          className="rounded-md bg-red-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-red-700 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {pending ? 'Working…' : confirmLabel}
        </button>
        <button
          type="button"
          onClick={onCancel}
          disabled={pending}
          className="rounded-md border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-100"
        >
          {cancelLabel}
        </button>
      </div>
    </div>
  );
}
