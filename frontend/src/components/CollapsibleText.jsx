import { useState } from 'react';

const COLLAPSE_CHARS = 300;
const COLLAPSE_LINES = 5;

// Long text collapsed to a few lines with a "Show all"/"Show less" toggle. The
// full text stays in the DOM; short text gets no toggle at all.
export default function CollapsibleText({ text, label, empty = '(empty)', mono = true }) {
  const [expanded, setExpanded] = useState(false);
  const value = text || '';
  const isLong = value.length > COLLAPSE_CHARS || value.split('\n').length > COLLAPSE_LINES;
  const Tag = mono ? 'pre' : 'p';
  const style = mono
    ? 'mt-1 rounded bg-gray-50 p-2 text-xs text-gray-700'
    : 'text-sm text-gray-600';

  return (
    <div>
      {label && <p className="text-xs font-medium uppercase tracking-wide text-gray-500">{label}</p>}
      <Tag
        className={`whitespace-pre-wrap ${style} ${isLong && !expanded ? 'max-h-24 overflow-hidden' : ''}`}
      >
        {value || empty}
      </Tag>
      {isLong && (
        <button
          type="button"
          onClick={() => setExpanded((current) => !current)}
          className="mt-1 text-xs font-medium text-brand-700 hover:text-brand-800"
        >
          {expanded ? 'Show less' : 'Show all'}
        </button>
      )}
    </div>
  );
}
