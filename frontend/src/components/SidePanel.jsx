import { useEffect } from 'react';

// A right-edge slide-over - deliberately NOT a modal: no backdrop, no focus
// trap, so whatever is behind it (an editor, a list) stays fully visible
// and usable while this is open. `open={false}` only HIDES it (the native
// `hidden` attribute, never unmounted) - the caller's own state underneath
// (a live poll, an in-progress form) keeps running, and reopening shows
// exactly what was last there. `hidden` lives on THIS OUTER element, which
// carries no `display` utility of its own (only positioning) - the actual
// `flex` layout is on the inner wrapper instead, so there is nothing for a
// same-element `display` class to fight the browser's default
// `[hidden]{display:none}` with. The caller is responsible for giving its
// own main content room (e.g. `md:pr-[...]`) so this panel never sits on
// top of something the user still needs to reach.
export default function SidePanel({ title, open, onClose, children }) {
  useEffect(() => {
    if (!open) return undefined;
    function handleKey(event) {
      if (event.key === 'Escape') onClose();
    }
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [open, onClose]);

  return (
    <div
      role="dialog"
      aria-label={title}
      hidden={!open}
      data-testid="side-panel"
      className="fixed inset-y-0 right-0 z-40 w-full border-l border-gray-200 bg-white shadow-xl md:w-[45%]"
    >
      <div className="flex h-full flex-col">
        <div className="flex shrink-0 items-center justify-between border-b border-gray-200 px-4 py-3">
          <h2 className="text-base font-semibold text-gray-900">{title}</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded-md px-2 py-1 text-lg leading-none text-gray-500 hover:bg-gray-100 hover:text-gray-900"
          >
            ×
          </button>
        </div>
        <div className="flex-1 overflow-y-auto p-4">{children}</div>
      </div>
    </div>
  );
}
