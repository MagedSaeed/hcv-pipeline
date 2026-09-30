import { useEffect, useId } from 'react';

// A centred pop-up over a dimmed backdrop. Escape or a backdrop click closes
// it; the caller owns the open state and simply unmounts it on `onClose`.
// `centered` also centres it vertically; a taller dialog still scrolls from its top.
export default function Modal({ title, onClose, children, wide = false, centered = false }) {
  const titleId = useId();

  useEffect(() => {
    function handleKey(event) {
      if (event.key === 'Escape') onClose();
    }
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-gray-900/40 px-4 py-10"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className={`w-full ${wide ? 'max-w-4xl' : 'max-w-xl'} ${centered ? 'my-auto' : ''} rounded-lg bg-white shadow-xl`}
      >
        <div className="flex items-center justify-between border-b border-gray-200 px-5 py-3">
          <h2 id={titleId} className="text-base font-semibold text-gray-900">
            {title}
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded-md px-2 py-1 text-lg leading-none text-gray-500 hover:bg-gray-100 hover:text-gray-900"
          >
            ×
          </button>
        </div>
        <div className="px-5 py-4">{children}</div>
      </div>
    </div>
  );
}
