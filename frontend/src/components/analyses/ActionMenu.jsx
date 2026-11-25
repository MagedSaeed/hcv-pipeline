import { useEffect, useId, useRef, useState } from 'react';

// A three-dot menu, fully usable by keyboard.
export default function ActionMenu({ label, items }) {
  const [open, setOpen] = useState(false);
  const menuId = useId();
  const containerRef = useRef(null);
  const triggerRef = useRef(null);
  const itemRefs = useRef([]);

  useEffect(() => {
    if (!open) return undefined;
    itemRefs.current[0]?.focus();
    function handleOutside(event) {
      if (!containerRef.current?.contains(event.target)) setOpen(false);
    }
    document.addEventListener('mousedown', handleOutside);
    return () => document.removeEventListener('mousedown', handleOutside);
  }, [open]);

  function handleKeyDown(event) {
    const moves = { ArrowDown: 1, ArrowUp: -1 };
    if (event.key in moves) {
      event.preventDefault();
      const index = itemRefs.current.indexOf(document.activeElement);
      itemRefs.current[(index + moves[event.key] + items.length) % items.length]?.focus();
    } else if (event.key === 'Escape') {
      event.stopPropagation();
      setOpen(false);
      triggerRef.current?.focus();
    } else if (event.key === 'Tab') {
      setOpen(false);
    }
  }

  function select(item) {
    setOpen(false);
    triggerRef.current?.focus();
    item.onSelect();
  }

  if (items.length === 0) return null;

  return (
    <div ref={containerRef} className="relative shrink-0 print:hidden">
      <button
        ref={triggerRef}
        type="button"
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        onClick={() => setOpen((current) => !current)}
        className="rounded-md px-2.5 py-1.5 text-lg leading-none text-gray-500 hover:bg-gray-100 hover:text-gray-900"
      >
        <span aria-hidden="true">⋯</span>
      </button>
      {open && (
        <div
          id={menuId}
          role="menu"
          aria-label={label}
          onKeyDown={handleKeyDown}
          className="absolute right-0 z-20 mt-1 w-max max-w-[80vw] rounded-md border border-gray-200 bg-white py-1 shadow-lg"
        >
          {items.map((item, index) => (
            <button
              key={item.label}
              ref={(node) => {
                itemRefs.current[index] = node;
              }}
              type="button"
              role="menuitem"
              tabIndex={-1}
              onClick={() => select(item)}
              className={`block w-full px-4 py-2 text-left text-sm hover:bg-gray-50 focus:bg-gray-100 focus:outline-none ${
                item.danger ? 'text-red-700' : 'text-gray-700'
              }`}
            >
              {item.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
