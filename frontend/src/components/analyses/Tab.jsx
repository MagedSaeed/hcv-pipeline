export default function Tab({ selected, onSelect, children }) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={selected}
      onClick={onSelect}
      className={`border-b-2 px-1 pb-2 text-sm font-medium ${
        selected ? 'border-brand-600 text-gray-900' : 'border-transparent text-gray-500 hover:text-gray-900'
      }`}
    >
      {children}
    </button>
  );
}
