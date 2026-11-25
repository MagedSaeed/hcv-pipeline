import { useState } from 'react';

const STORAGE_KEY = 'analyses:list-collapsed';

function readCollapsed() {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === 'true';
  } catch {
    return false;
  }
}

// A short list beside the selected item, each scrolling in its own box. The list can
// collapse to a narrow strip; that choice is remembered across steps.
export default function MasterDetail({
  listLabel,
  detailLabel,
  items,
  selectedKey,
  onSelect,
  renderItem,
  renderCompact,
  itemLabel,
  children,
}) {
  const [collapsed, setCollapsed] = useState(readCollapsed);

  function toggle() {
    const next = !collapsed;
    setCollapsed(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, String(next));
    } catch {
      // Private browsing can refuse storage; the choice then lasts for this page only.
    }
  }

  return (
    // Both widths use the same track form, so the browser can animate between them.
    <div
      data-collapsed={collapsed}
      className={`grid h-[34rem] gap-4 transition-[grid-template-columns] duration-300 ease-in-out motion-reduce:transition-none ${
        collapsed ? 'grid-cols-[3.5rem_minmax(0,1fr)]' : 'grid-cols-[17rem_minmax(0,1fr)]'
      }`}
    >
      <div className="flex min-h-0 flex-col overflow-hidden rounded-xl border border-gray-200 bg-white">
        <div className={`flex border-b border-gray-100 px-2 py-1.5 ${collapsed ? 'justify-center' : 'justify-end'}`}>
          <button
            type="button"
            aria-expanded={!collapsed}
            aria-label={collapsed ? 'Expand list' : 'Collapse list'}
            title={collapsed ? 'Expand list' : 'Collapse list'}
            onClick={toggle}
            className="rounded px-2 py-0.5 text-sm text-gray-500 hover:bg-gray-100 hover:text-gray-900"
          >
            <span aria-hidden="true">{collapsed ? '»' : '«'}</span>
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-x-hidden overflow-y-auto">
          {/* Items keep their full width while the column animates, so text is clipped, not rewrapped. */}
          <ul
            key={collapsed ? 'strip' : 'list'}
            aria-label={listLabel}
            className={`animate-fade-in divide-y divide-gray-100 motion-reduce:animate-none ${
              collapsed ? 'w-full' : 'w-[17rem]'
            }`}
          >
            {items.map((item) => {
              const selected = item.key === selectedKey;
              return (
                <li key={item.key}>
                  <button
                    type="button"
                    aria-current={selected ? 'true' : undefined}
                    aria-label={collapsed ? itemLabel(item) : undefined}
                    title={collapsed ? itemLabel(item) : undefined}
                    onClick={() => onSelect(item.key)}
                    className={`block w-full ${collapsed ? 'px-1 py-2.5 text-center' : 'px-4 py-3 text-left'} ${
                      selected ? 'bg-brand-50' : 'hover:bg-gray-50'
                    }`}
                  >
                    {collapsed ? renderCompact(item) : renderItem(item)}
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      </div>
      <section aria-label={detailLabel} className="overflow-y-auto rounded-xl border border-gray-200 bg-white p-5">
        {children}
      </section>
    </div>
  );
}
