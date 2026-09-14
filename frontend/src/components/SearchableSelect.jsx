import { useEffect, useId, useMemo, useRef, useState } from 'react';

function optionMatches(option, words) {
  if (words.length === 0) return true;
  const haystack = `${option.label} ${option.description ?? ''}`.toLowerCase();
  return words.every((word) => haystack.includes(word));
}

// Splits `options` into what a search for `query` should show. A flat
// option (no `children`) is kept if it matches. A GROUP (`children`: several
// versions of one named thing - see pages/ExperimentBuilder.jsx's
// `groupTemplatesByName`) is kept in full if the group itself matches
// (e.g. by name), or - if only some of its children match (e.g. by
// version) - kept with just those children and `forceExpand: true`, so a
// version-only search still surfaces (and opens) the group it belongs to.
function filterGroups(options, query) {
  const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const result = [];
  for (const option of options) {
    const children = option.children?.length > 0 ? option.children : null;
    if (!children) {
      if (optionMatches(option, words)) result.push({ ...option, children: null, forceExpand: false });
      continue;
    }
    if (optionMatches(option, words)) {
      result.push({ ...option, children, visibleChildren: children, forceExpand: false });
      continue;
    }
    const matchingChildren = children.filter((child) => optionMatches(child, words));
    if (matchingChildren.length > 0) {
      result.push({ ...option, children, visibleChildren: matchingChildren, forceExpand: true });
    }
  }
  return result;
}

// The option (or child) `value` currently resolves to, searching one level
// of `children` too - what decides the closed-state text and which row (if
// any) reads as selected.
function resolveSelected(options, value) {
  for (const option of options) {
    if (option.value === value) return option;
    const child = option.children?.find((entry) => entry.value === value);
    if (child) return child;
  }
  return null;
}

// A generic single-select combobox for a long `options` list: closed, it
// shows the selected label (full text - CSS only ellipsizes on overflow,
// via `title`); open, a search box narrows the list by label+description
// (every typed word must match, case-insensitive - same rule ModelPicker
// uses). `options` are `{ value, label, description?, closedLabel? }`, or a
// GROUP of several versions of one thing: `{ value, label, children: [{
// value, label, closedLabel? }, ...] }` - its row shows a ▸/▾ toggle;
// clicking the row itself selects the group's OWN `value` (its caller's
// choice of "current" version), the toggle expands/collapses the version
// rows, each selectable on its own. `closedLabel`, when given, is what the
// closed field shows in place of `label` once that option is selected (a
// group's row reads just its name; its closed text can read "name (vN)").
// `value`/`onChange` behave like a native <select>'s. Modelled on
// components/ModelPicker.jsx's combobox pattern, kept small and
// content-agnostic.
export default function SearchableSelect({
  id,
  label,
  value,
  onChange,
  options,
  placeholder = '',
  emptyLabel,
  disabled = false,
}) {
  const generatedId = useId();
  const fieldId = id ?? generatedId;

  const [isOpen, setIsOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [highlightedIndex, setHighlightedIndex] = useState(0);
  // Per-group manual expand/collapse, keyed by the group's own value -
  // absent means "use the default" (see `isExpanded` below). Persists
  // across opening/closing the field, same as ScenarioPicker's own
  // `openOverrides`.
  const [expandedOverrides, setExpandedOverrides] = useState({});
  const containerRef = useRef(null);
  const searchInputRef = useRef(null);

  const filteredGroups = useMemo(() => filterGroups(options, query), [options, query]);

  function isExpanded(group) {
    if (group.forceExpand) return true;
    if (group.value in expandedOverrides) return expandedOverrides[group.value];
    // Default open: the current selection is one of this group's own versions.
    return group.children?.some((child) => child.value === value) ?? false;
  }

  // The flat, keyboard-navigable row list actually on screen right now - a
  // group contributes one row for itself, plus one per visible child when
  // expanded; a plain option contributes just its own row.
  const navigable = useMemo(() => {
    const rows = [];
    for (const group of filteredGroups) {
      rows.push({ option: group, isParent: Boolean(group.children), isChild: false });
      if (group.children && isExpanded(group)) {
        for (const child of group.visibleChildren ?? group.children) {
          rows.push({ option: child, isParent: false, isChild: true });
        }
      }
    }
    return rows;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filteredGroups, expandedOverrides, value]);

  const selectedOption = resolveSelected(options, value);
  const showClear = Boolean(value) && Boolean(emptyLabel) && !disabled;

  useEffect(() => {
    setHighlightedIndex(0);
  }, [query, isOpen]);

  useEffect(() => {
    if (isOpen) searchInputRef.current?.focus();
  }, [isOpen]);

  // Same click-outside pattern as ModelPicker: everything (trigger, clear
  // button, search box, listbox) lives inside `containerRef`.
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

  function openList() {
    if (disabled) return;
    setQuery('');
    setIsOpen(true);
  }

  function handleSelect(option) {
    onChange(option.value);
    setQuery('');
    setIsOpen(false);
  }

  function handleClear(event) {
    event.stopPropagation();
    onChange('');
  }

  function handleTriggerKeyDown(event) {
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      openList();
    }
  }

  function handleSearchKeyDown(event) {
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      setHighlightedIndex((current) => Math.min(current + 1, navigable.length - 1));
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      setHighlightedIndex((current) => Math.max(current - 1, 0));
    } else if (event.key === 'ArrowRight') {
      const row = navigable[highlightedIndex];
      if (row?.isParent) {
        event.preventDefault();
        setExpandedOverrides((current) => ({ ...current, [row.option.value]: true }));
      }
    } else if (event.key === 'ArrowLeft') {
      const row = navigable[highlightedIndex];
      if (row?.isParent) {
        event.preventDefault();
        setExpandedOverrides((current) => ({ ...current, [row.option.value]: false }));
      }
    } else if (event.key === 'Enter') {
      event.preventDefault();
      if (navigable[highlightedIndex]) handleSelect(navigable[highlightedIndex].option);
    } else if (event.key === 'Escape') {
      setIsOpen(false);
      setQuery('');
    } else if (event.key === 'Tab') {
      setIsOpen(false);
    }
  }

  const closedText = selectedOption ? (selectedOption.closedLabel ?? selectedOption.label) : emptyLabel || placeholder;

  return (
    <div ref={containerRef} data-searchable-select="">
      {label && (
        <label htmlFor={fieldId} className="block text-xs font-medium text-gray-700">
          {label}
        </label>
      )}
      <div className="relative mt-1">
        <button
          type="button"
          id={fieldId}
          role="combobox"
          aria-haspopup="listbox"
          aria-expanded={isOpen}
          aria-controls={`${fieldId}-listbox`}
          disabled={disabled}
          onClick={() => setIsOpen((current) => !current)}
          onKeyDown={handleTriggerKeyDown}
          className={`flex h-9 w-full items-center rounded-md border border-gray-300 bg-white px-2 text-left text-sm shadow-sm disabled:cursor-not-allowed disabled:bg-gray-100 disabled:text-gray-400 ${
            showClear ? 'pr-12' : 'pr-7'
          }`}
        >
          <span className={`truncate ${selectedOption ? 'text-gray-900' : 'text-gray-500'}`} title={selectedOption ? closedText : undefined}>
            {closedText}
          </span>
        </button>
        <span aria-hidden="true" className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-gray-400">
          ▾
        </span>
        {showClear && (
          <button
            type="button"
            aria-label={`Clear ${label || 'selection'}`}
            onClick={handleClear}
            className="absolute right-6 top-1/2 -translate-y-1/2 rounded px-1 text-sm leading-none text-gray-400 hover:text-gray-700"
          >
            ×
          </button>
        )}

        {isOpen && !disabled && (
          <div className="absolute z-10 mt-1 w-full rounded-md border border-gray-300 bg-white shadow-lg">
            <div className="border-b border-gray-200 p-2">
              <input
                ref={searchInputRef}
                type="search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={handleSearchKeyDown}
                aria-label={label ? `Search ${label}` : 'Search options'}
                placeholder="Search…"
                className="block w-full rounded-md border border-gray-300 px-2 py-1 text-sm shadow-sm"
              />
            </div>
            <ul id={`${fieldId}-listbox`} role="listbox" className="max-h-72 overflow-y-auto">
              {navigable.length === 0 && <li className="px-3 py-2 text-sm text-gray-500">No matches.</li>}
              {navigable.map((row, index) => {
                const { option, isParent, isChild } = row;
                const highlighted = index === highlightedIndex;
                const rowClass = `cursor-pointer px-3 py-2 text-sm ${isChild ? 'pl-9' : ''} ${
                  highlighted ? 'bg-brand-50' : 'hover:bg-gray-50'
                }`;

                if (isParent) {
                  const expanded = isExpanded(option);
                  // While expanded, the group's resolved version already has
                  // its OWN row below (same underlying value) - the header
                  // row stops being independently selectable so there is
                  // never two rows both claiming to be "the" selected one.
                  return (
                    <li
                      key={option.value}
                      role="option"
                      aria-selected={!expanded && option.value === value}
                      data-testid={`${fieldId}-option-${option.value}`}
                      onMouseDown={(event) => event.preventDefault()}
                      onClick={() => {
                        if (!expanded) handleSelect(option);
                      }}
                      className={`flex items-center gap-1 ${rowClass}`}
                    >
                      <button
                        type="button"
                        aria-label={`${expanded ? 'Collapse' : 'Expand'} ${option.label} versions`}
                        aria-expanded={expanded}
                        onClick={(event) => {
                          event.stopPropagation();
                          setExpandedOverrides((current) => ({ ...current, [option.value]: !expanded }));
                        }}
                        className="shrink-0 px-1 text-xs text-gray-400 hover:text-gray-700"
                      >
                        {expanded ? '▾' : '▸'}
                      </button>
                      <span className="min-w-0 flex-1">
                        <p className="truncate text-gray-900">{option.label}</p>
                        {option.description && <p className="text-xs text-gray-500">{option.description}</p>}
                      </span>
                    </li>
                  );
                }

                // Children live under `-version-`, not `-option-` - a
                // group's own resolved value is always one of its own
                // children's values too, so sharing one testid namespace
                // would collide once the group is expanded.
                return (
                  <li
                    key={option.value}
                    role="option"
                    aria-selected={option.value === value}
                    data-testid={isChild ? `${fieldId}-version-${option.value}` : `${fieldId}-option-${option.value}`}
                    onMouseDown={(event) => event.preventDefault()}
                    onClick={() => handleSelect(option)}
                    className={rowClass}
                  >
                    <p className="text-gray-900">{option.label}</p>
                    {option.description && <p className="text-xs text-gray-500">{option.description}</p>}
                  </li>
                );
              })}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}
