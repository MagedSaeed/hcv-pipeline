import { useEffect, useMemo, useRef, useState } from 'react';

// OpenRouter catalogue row -> https://openrouter.ai/<id> (the model's own
// page there - clicking it is how a researcher checks pricing/limits in
// more depth than this widget shows inline).
function openRouterUrl(modelId) {
  return `https://openrouter.ai/${modelId}`;
}

// "anthropic/claude-sonnet-4" -> "anthropic". Models with no "/" (rare)
// have no provider to filter by.
function providerOf(modelId) {
  if (!modelId || !modelId.includes('/')) return null;
  return modelId.slice(0, modelId.indexOf('/'));
}

function isFreeModel(model) {
  return Number(model.pricing?.prompt) === 0 && Number(model.pricing?.completion) === 0;
}

// Pricing is USD per token as strings; "0" = free, negative = variable/
// unknown - never a number to sort or display as a real price.
function formatPricePerMillion(raw) {
  if (raw === undefined || raw === null || raw === '') return 'varies';
  const price = Number(raw);
  if (!Number.isFinite(price) || price < 0) return 'varies';
  if (price === 0) return 'free';
  return `$${(price * 1e6).toFixed(2)}`;
}

function formatContextLength(length) {
  const n = Number(length);
  if (!Number.isFinite(n) || n <= 0) return null;
  if (n >= 1000) {
    const thousands = n / 1000;
    const rounded = Number.isInteger(thousands) ? thousands : Math.round(thousands * 10) / 10;
    return `${rounded}K ctx`;
  }
  return `${n} ctx`;
}

// Unknown/variable pricing sorts last on an ascending price sort - it is
// not "cheap", it is unquantified.
function priceSortValue(raw) {
  const n = Number(raw);
  return !Number.isFinite(n) || n < 0 ? Infinity : n;
}

function compareModels(a, b, sortBy) {
  if (sortBy === 'input-price-asc') return priceSortValue(a.pricing?.prompt) - priceSortValue(b.pricing?.prompt);
  if (sortBy === 'output-price-asc') return priceSortValue(a.pricing?.completion) - priceSortValue(b.pricing?.completion);
  if (sortBy === 'context-desc') {
    const av = Number.isFinite(Number(a.context_length)) ? Number(a.context_length) : -Infinity;
    const bv = Number.isFinite(Number(b.context_length)) ? Number(b.context_length) : -Infinity;
    return bv - av;
  }
  return (a.name || a.id).localeCompare(b.name || b.id);
}

const MAX_RESULTS = 100;

// Searchable, filterable model combobox over the OpenRouter catalogue
// (`useModels`). Falls back to a plain text input - identical to the old
// ModelField - only when the catalogue itself failed to load; while it did
// load, a value can only ever be one of its own ids, exactly as the
// `<select>` this replaces already enforced.
export default function ModelPicker({ id, label, value, onChange, modelsQuery, fieldError }) {
  const [isOpen, setIsOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [provider, setProvider] = useState('');
  const [freeOnly, setFreeOnly] = useState(false);
  const [sortBy, setSortBy] = useState('name');
  const [highlightedIndex, setHighlightedIndex] = useState(0);
  const containerRef = useRef(null);

  const allModels = useMemo(() => modelsQuery.data?.results ?? [], [modelsQuery.data]);

  const filtered = useMemo(() => {
    const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
    return allModels.filter((model) => {
      if (provider && providerOf(model.id) !== provider) return false;
      if (freeOnly && !isFreeModel(model)) return false;
      if (words.length === 0) return true;
      const haystack = `${model.name ?? ''} ${model.id}`.toLowerCase();
      return words.every((word) => haystack.includes(word));
    });
  }, [allModels, query, provider, freeOnly]);

  const sorted = useMemo(() => [...filtered].sort((a, b) => compareModels(a, b, sortBy)), [filtered, sortBy]);
  const capped = sorted.length > MAX_RESULTS;
  const visible = capped ? sorted.slice(0, MAX_RESULTS) : sorted;

  const providers = useMemo(() => {
    const set = new Set();
    allModels.forEach((model) => {
      const p = providerOf(model.id);
      if (p) set.add(p);
    });
    return [...set].sort();
  }, [allModels]);

  const selectedModel = allModels.find((model) => model.id === value) ?? null;

  // Closing on an outside click - every filter control and option row lives
  // inside `containerRef`, so this never fires for an interaction with the
  // picker itself.
  useEffect(() => {
    if (!isOpen) return undefined;
    function handleDocumentMouseDown(event) {
      if (containerRef.current && !containerRef.current.contains(event.target)) {
        setIsOpen(false);
      }
    }
    document.addEventListener('mousedown', handleDocumentMouseDown);
    return () => document.removeEventListener('mousedown', handleDocumentMouseDown);
  }, [isOpen]);

  if (modelsQuery.isError) {
    return (
      <div>
        <label htmlFor={id} className="block text-xs font-medium text-gray-700">
          {label}
        </label>
        <input
          id={id}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          placeholder="openai/gpt-4o-mini"
          className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-1.5 text-sm shadow-sm"
        />
        <p className="mt-1 text-xs text-amber-700">Model catalogue unavailable — enter a model id by hand.</p>
        {fieldError && <p className="mt-1 text-xs text-red-700">{fieldError}</p>}
      </div>
    );
  }

  function handleSelect(model) {
    onChange(model.id);
    setQuery('');
    setIsOpen(false);
  }

  function handleKeyDown(event) {
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      setIsOpen(true);
      setHighlightedIndex((current) => Math.min(current + 1, visible.length - 1));
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      setHighlightedIndex((current) => Math.max(current - 1, 0));
    } else if (event.key === 'Enter') {
      if (isOpen && visible[highlightedIndex]) {
        event.preventDefault();
        handleSelect(visible[highlightedIndex]);
      }
    } else if (event.key === 'Escape') {
      setIsOpen(false);
      setQuery('');
    }
  }

  const displayValue = isOpen
    ? query
    : selectedModel
      ? `${selectedModel.name || selectedModel.id} (${selectedModel.id})`
      : '';

  return (
    <div ref={containerRef} className="relative">
      <label htmlFor={id} className="block text-xs font-medium text-gray-700">
        {label}
      </label>
      <input
        id={id}
        type="text"
        role="combobox"
        aria-expanded={isOpen}
        aria-controls={`${id}-listbox`}
        autoComplete="off"
        disabled={!modelsQuery.isSuccess}
        value={displayValue}
        placeholder={modelsQuery.isSuccess ? 'Select a model…' : 'Loading models…'}
        onFocus={() => setIsOpen(true)}
        onClick={() => setIsOpen(true)}
        onChange={(event) => {
          setQuery(event.target.value);
          setIsOpen(true);
          setHighlightedIndex(0);
        }}
        onKeyDown={handleKeyDown}
        className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-1.5 text-sm shadow-sm"
      />
      {selectedModel && !isOpen && (
        <p className="mt-1 text-xs text-gray-600">
          In {formatPricePerMillion(selectedModel.pricing?.prompt)} · Out{' '}
          {formatPricePerMillion(selectedModel.pricing?.completion)} ·{' '}
          <a
            href={openRouterUrl(selectedModel.id)}
            target="_blank"
            rel="noreferrer"
            className="text-brand-700 hover:underline"
          >
            OpenRouter ↗
          </a>
        </p>
      )}
      {fieldError && <p className="mt-1 text-xs text-red-700">{fieldError}</p>}

      {isOpen && modelsQuery.isSuccess && (
        <div className="absolute z-10 mt-1 w-full rounded-md border border-gray-300 bg-white shadow-lg">
          <div className="flex flex-wrap items-center gap-2 border-b border-gray-200 p-2">
            <select
              aria-label={`${label} provider filter`}
              value={provider}
              onChange={(event) => {
                setProvider(event.target.value);
                setHighlightedIndex(0);
              }}
              className="rounded-md border border-gray-300 px-2 py-1 text-xs"
            >
              <option value="">All providers</option>
              {providers.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
            <label className="flex items-center gap-1 text-xs text-gray-700">
              <input
                type="checkbox"
                checked={freeOnly}
                onChange={(event) => {
                  setFreeOnly(event.target.checked);
                  setHighlightedIndex(0);
                }}
              />
              Free only
            </label>
            <select
              aria-label={`${label} sort`}
              value={sortBy}
              onChange={(event) => {
                setSortBy(event.target.value);
                setHighlightedIndex(0);
              }}
              className="rounded-md border border-gray-300 px-2 py-1 text-xs"
            >
              <option value="name">Name</option>
              <option value="input-price-asc">Input price: low to high</option>
              <option value="output-price-asc">Output price: low to high</option>
              <option value="context-desc">Context length: high to low</option>
            </select>
          </div>
          <ul id={`${id}-listbox`} role="listbox" className="max-h-72 overflow-y-auto">
            {visible.length === 0 && <li className="px-3 py-2 text-sm text-gray-500">No models match.</li>}
            {visible.map((model, index) => (
              <li
                key={model.id}
                role="option"
                aria-selected={model.id === value}
                data-testid={`model-option-${model.id}`}
                onClick={() => handleSelect(model)}
                className={`flex cursor-pointer items-center justify-between gap-3 px-3 py-2 text-sm ${
                  index === highlightedIndex ? 'bg-brand-50' : 'hover:bg-gray-50'
                }`}
              >
                <div>
                  <p className="font-medium text-gray-900">{model.name || model.id}</p>
                  <p className="text-xs text-gray-500">{model.id}</p>
                </div>
                <div className="flex shrink-0 items-center gap-2 text-xs text-gray-600">
                  <span>In {formatPricePerMillion(model.pricing?.prompt)}</span>
                  <span>Out {formatPricePerMillion(model.pricing?.completion)}</span>
                  {formatContextLength(model.context_length) && <span>{formatContextLength(model.context_length)}</span>}
                  <a
                    href={openRouterUrl(model.id)}
                    target="_blank"
                    rel="noreferrer"
                    onClick={(event) => event.stopPropagation()}
                    className="text-brand-700 hover:underline"
                  >
                    OpenRouter ↗
                  </a>
                </div>
              </li>
            ))}
          </ul>
          {capped && (
            <p className="border-t border-gray-200 px-3 py-2 text-xs text-gray-500">
              Showing the first {MAX_RESULTS} matches — refine your search to see more.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
