import { useMemo, useState } from 'react';
import { useBulkCreateScenarios, useExperiments, useSystemDescriptions } from '../api/queries';
import ErrorBanner from './ErrorBanner';
import Modal from './Modal';
import { useScenarioGroups } from './ScenarioPicker';
import { CARD_FIELDS } from './scenarioCard';
import {
  bulkPayload,
  draftErrors,
  draftKey,
  emptyDraftScenario,
  nextScenarioId,
  rowsBySource,
  sourceOptions,
  systemEntries,
  visibleRows,
} from './labelScenarioSelection';

const DRAFT_FIELDS = [...CARD_FIELDS, ['narrative', 'Narrative']];
const inputClass = 'mt-1 block w-full rounded-md border border-gray-300 px-2 py-1 text-xs font-normal shadow-sm';
const smallButton = 'rounded-md border border-gray-300 px-2 py-1 text-xs font-medium text-gray-700 hover:bg-gray-100';

function plural(count, word) {
  return `${count} ${count === 1 ? word : `${word}s`}`;
}

function FieldMessages({ messages }) {
  if (!messages) return null;
  return <p className="mt-1 text-xs font-normal text-red-600">{[].concat(messages).join(' ')}</p>;
}

function DraftScenarioFields({ draft, errors, onChange, onRemove }) {
  return (
    <div className="space-y-2 rounded-md border border-dashed border-gray-300 p-2">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-gray-700">New scenario</span>
        <button type="button" onClick={onRemove} className="text-xs font-medium text-red-700 hover:underline">
          Remove
        </button>
      </div>
      <label className="block text-xs font-medium text-gray-700">
        Scenario ID
        <input
          value={draft.scenario_id}
          onChange={(event) => onChange({ ...draft, scenario_id: event.target.value })}
          className={inputClass}
        />
        <FieldMessages messages={errors.scenario_id} />
      </label>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {DRAFT_FIELDS.map(([field, label]) => (
          <label
            key={field}
            className={`block text-xs font-medium text-gray-700 ${field === 'narrative' ? 'sm:col-span-2' : ''}`}
          >
            {label}
            {field === 'narrative' ? (
              <textarea
                rows={3}
                value={draft[field]}
                onChange={(event) => onChange({ ...draft, [field]: event.target.value })}
                className={inputClass}
              />
            ) : (
              <input
                value={draft[field]}
                onChange={(event) => onChange({ ...draft, [field]: event.target.value })}
                className={inputClass}
              />
            )}
            <FieldMessages messages={errors[field]} />
          </label>
        ))}
      </div>
      <FieldMessages messages={errors.non_field_errors} />
    </div>
  );
}

function DraftSystemPanel({ system, errors, onChange, onRemove }) {
  const scenarioErrors = errors.scenarios && typeof errors.scenarios === 'object' ? errors.scenarios : {};
  return (
    <div className="space-y-2">
      <label className="block text-xs font-medium text-gray-700">
        System title
        <input value={system.title} onChange={(event) => onChange({ title: event.target.value })} className={inputClass} />
        <FieldMessages messages={errors.title} />
      </label>
      <label className="block text-xs font-medium text-gray-700">
        System description
        <textarea
          rows={4}
          value={system.body}
          onChange={(event) => onChange({ body: event.target.value })}
          className={inputClass}
        />
        <FieldMessages messages={errors.body} />
      </label>
      {system.scenarios.map((draft, index) => (
        <DraftScenarioFields
          key={draft.key}
          draft={draft}
          errors={draftErrors(scenarioErrors, [index])}
          onChange={(next) =>
            onChange({ scenarios: system.scenarios.map((item) => (item.key === draft.key ? next : item)) })
          }
          onRemove={() => onChange({ scenarios: system.scenarios.filter((item) => item.key !== draft.key) })}
        />
      ))}
      {Array.isArray(errors.scenarios) && typeof errors.scenarios[0] === 'string' && (
        <FieldMessages messages={errors.scenarios} />
      )}
      <div className="flex gap-2">
        <button
          type="button"
          onClick={() =>
            onChange({
              scenarios: [
                ...system.scenarios,
                emptyDraftScenario(nextScenarioId(system.scenarios.map((item) => item.scenario_id))),
              ],
            })
          }
          className={smallButton}
        >
          + Add scenario
        </button>
        <button type="button" onClick={onRemove} className="rounded-md px-2 py-1 text-xs font-medium text-red-700 hover:underline">
          Remove system
        </button>
      </div>
    </div>
  );
}

// Picks the scenarios a phase-3-only experiment labels. Existing corpus rows
// are only ticked here; new systems and scenarios are drafted and saved to the
// corpus together on Apply.
export default function LabelScenariosModal({ initialIds, excludeExperimentId, onApply, onClose }) {
  const { groups, isLoading, error } = useScenarioGroups();
  const systemsQuery = useSystemDescriptions();
  const experimentsQuery = useExperiments();
  const bulkCreate = useBulkCreateScenarios();

  const [selected, setSelected] = useState(() => new Set(initialIds.map(Number)));
  const [source, setSource] = useState('');
  const [search, setSearch] = useState('');
  const [active, setActive] = useState(null);
  const [drafts, setDrafts] = useState({ systems: [], scenarios: [] });
  const [copiedFrom, setCopiedFrom] = useState(null);

  const systems = useMemo(() => systemsQuery.data?.results ?? [], [systemsQuery.data]);
  const rows = useMemo(() => visibleRows(groups, { source, search }), [groups, source, search]);
  const entries = useMemo(() => systemEntries(systems, rows, { source, search }), [systems, rows, source, search]);
  const systemOfScenario = useMemo(
    () => new Map(groups.flatMap((group) => group.rows.map((row) => [row.id, row.system_description]))),
    [groups]
  );
  const copyOptions = (experimentsQuery.data?.results ?? []).filter(
    (experiment) =>
      experiment.id !== excludeExperimentId &&
      experiment.phases?.length === 1 &&
      experiment.phases[0] === 3 &&
      (experiment.label_scenarios ?? []).length > 0
  );
  const saveErrors = bulkCreate.error?.response?.data ?? {};

  // Falls back to the first listed system, so the right pane is never empty on open or after filtering.
  const activeEntry =
    typeof active === 'string' ? null : (entries.find((entry) => entry.id === active) ?? entries[0] ?? null);
  const activeDraftIndex = typeof active === 'string' ? drafts.systems.findIndex((system) => system.key === active) : -1;

  const draftScenarioCount =
    drafts.scenarios.length + drafts.systems.reduce((total, system) => total + system.scenarios.length, 0);
  const selectedSystems = new Set([...selected].map((id) => systemOfScenario.get(id)).filter((id) => id != null));
  for (const draft of drafts.scenarios) selectedSystems.add(draft.systemId);
  for (const system of drafts.systems) if (system.scenarios.length > 0) selectedSystems.add(system.key);
  const scenarioCount = selected.size + draftScenarioCount;
  const hasDrafts = drafts.systems.length > 0 || drafts.scenarios.length > 0;
  const bannerError =
    bulkCreate.isError && (typeof saveErrors.detail === 'string' || saveErrors.non_field_errors) ? bulkCreate.error : null;

  function copySelectionFrom(experimentId) {
    const experiment = copyOptions.find((option) => String(option.id) === experimentId) ?? null;
    setCopiedFrom(experiment);
    if (!experiment) return;
    const ids = experiment.label_scenarios.map(Number);
    setSelected(new Set(ids));
    // Show a system that holds copied scenarios, so the new ticks are on screen.
    const firstSystem = ids.map((id) => systemOfScenario.get(id)).find((id) => id != null);
    if (firstSystem != null) setActive(firstSystem);
  }

  function toggle(ids, checked) {
    setSelected((current) => {
      const next = new Set(current);
      for (const id of ids) {
        if (checked) next.add(id);
        else next.delete(id);
      }
      return next;
    });
  }

  function addDraftToExisting(entry) {
    const taken = [
      ...entry.rows.map((row) => row.scenario_id),
      ...drafts.scenarios.filter((draft) => draft.systemId === entry.id).map((draft) => draft.scenario_id),
    ];
    setDrafts((current) => ({
      ...current,
      scenarios: [...current.scenarios, { ...emptyDraftScenario(nextScenarioId(taken)), systemId: entry.id }],
    }));
  }

  function updateExistingDraft(key, next) {
    setDrafts((current) => ({
      ...current,
      scenarios: current.scenarios.map((draft) => (draft.key === key ? next : draft)),
    }));
  }

  function removeExistingDraft(key) {
    setDrafts((current) => ({ ...current, scenarios: current.scenarios.filter((draft) => draft.key !== key) }));
  }

  function addDraftSystem() {
    const key = draftKey();
    setDrafts((current) => ({ ...current, systems: [...current.systems, { key, title: '', body: '', scenarios: [] }] }));
    setActive(key);
  }

  function updateDraftSystem(index, changes) {
    setDrafts((current) => ({
      ...current,
      systems: current.systems.map((system, i) => (i === index ? { ...system, ...changes } : system)),
    }));
  }

  function removeDraftSystem(index) {
    setDrafts((current) => ({ ...current, systems: current.systems.filter((_, i) => i !== index) }));
    setActive(null);
  }

  async function handleApply() {
    if (!hasDrafts) {
      onApply([...selected]);
      return;
    }
    try {
      const { scenario_ids: created } = await bulkCreate.mutateAsync(bulkPayload(drafts));
      onApply([...selected, ...created]);
    } catch {
      // Shown next to each draft via saveErrors.
    }
  }

  return (
    <Modal title="Choose scenarios to label" onClose={onClose} wide centered>
      {isLoading && <p className="text-xs text-gray-500">Loading scenarios…</p>}
      {error && <ErrorBanner error={error} />}
      {!isLoading && !error && (
        <div className="space-y-3">
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
            <select
              aria-label="Source"
              value={source}
              onChange={(event) => setSource(event.target.value)}
              className="rounded-md border border-gray-300 px-2 py-1 text-xs shadow-sm"
            >
              <option value="">All sources</option>
              {sourceOptions(groups).map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <input
              type="search"
              aria-label="Search scenarios"
              placeholder="Search systems and scenario text…"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              className="rounded-md border border-gray-300 px-2 py-1 text-xs shadow-sm"
            />
            <select
              aria-label="Load scenarios from an experiment"
              value={copiedFrom ? String(copiedFrom.id) : ''}
              onChange={(event) => copySelectionFrom(event.target.value)}
              className="rounded-md border border-gray-300 px-2 py-1 text-xs shadow-sm"
            >
              <option value="">Load scenarios from an experiment…</option>
              {copyOptions.map((experiment) => (
                <option key={experiment.id} value={experiment.id}>
                  {experiment.name}
                </option>
              ))}
            </select>
          </div>
          {copiedFrom && (
            <p className="text-xs text-gray-500">
              Loaded {copiedFrom.label_scenarios.length}{' '}
              {copiedFrom.label_scenarios.length === 1 ? 'scenario' : 'scenarios'} from “{copiedFrom.name}”.
            </p>
          )}

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-[14rem_1fr]">
            <div className="space-y-2">
              <ul
                aria-label="Systems"
                className="max-h-96 divide-y divide-gray-100 overflow-y-auto rounded-md border border-gray-200"
              >
                {entries.map((entry) => {
                  const chosen = entry.rows.filter((row) => selected.has(row.id)).length;
                  return (
                    <li key={entry.id}>
                      <button
                        type="button"
                        onClick={() => setActive(entry.id)}
                        aria-current={activeEntry?.id === entry.id}
                        className={`flex w-full items-start justify-between gap-2 px-2 py-1.5 text-left text-xs ${
                          activeEntry?.id === entry.id ? 'bg-brand-50' : 'hover:bg-gray-50'
                        }`}
                      >
                        <span className="min-w-0">
                          <span className="block truncate font-medium text-gray-900">{entry.title}</span>
                          {entry.origin === 'generated' && <span className="text-gray-500">generated</span>}
                        </span>
                        <span className="shrink-0 text-gray-500">
                          {chosen}/{entry.rows.length}
                        </span>
                      </button>
                    </li>
                  );
                })}
                {drafts.systems.map((system) => (
                  <li key={system.key}>
                    <button
                      type="button"
                      onClick={() => setActive(system.key)}
                      aria-current={active === system.key}
                      className={`flex w-full justify-between gap-2 px-2 py-1.5 text-left text-xs ${
                        active === system.key ? 'bg-brand-50' : 'hover:bg-gray-50'
                      }`}
                    >
                      <span className="truncate font-medium text-gray-900">{system.title || 'Untitled system'}</span>
                      <span className="shrink-0 text-gray-500">new</span>
                    </button>
                  </li>
                ))}
              </ul>
              <button type="button" onClick={addDraftSystem} className={`w-full ${smallButton}`}>
                + New system
              </button>
            </div>

            <div className="max-h-[28rem] space-y-3 overflow-y-auto">
              {!activeEntry && activeDraftIndex < 0 && (
                <p className="text-xs text-gray-500">No systems match. Clear the filters, or add a new system.</p>
              )}

              {activeEntry && (
                <>
                  <h3 className="text-sm font-semibold text-gray-900">{activeEntry.title}</h3>
                  {activeEntry.rows.length === 0 && (
                    <p className="text-xs text-gray-500">No scenarios match for this system.</p>
                  )}
                  {rowsBySource(activeEntry.rows).map((group) => {
                    const ids = group.rows.map((row) => row.id);
                    const chosen = ids.filter((id) => selected.has(id)).length;
                    return (
                      <div key={group.key} className="rounded-md border border-gray-200">
                        <label className="flex items-center gap-2 bg-gray-50 px-2 py-1.5 text-xs font-medium text-gray-900">
                          <input
                            type="checkbox"
                            aria-label={`Select all in ${group.title}`}
                            checked={chosen > 0 && chosen === ids.length}
                            ref={(element) => {
                              if (element) element.indeterminate = chosen > 0 && chosen < ids.length;
                            }}
                            onChange={(event) => toggle(ids, event.target.checked)}
                          />
                          {group.title} · {plural(ids.length, 'scenario')}
                        </label>
                        <ul className="divide-y divide-gray-100">
                          {group.rows.map((row) => (
                            <li key={row.id} className="px-2 py-1.5">
                              <label className="flex items-start gap-2 text-xs text-gray-700">
                                <input
                                  type="checkbox"
                                  checked={selected.has(row.id)}
                                  onChange={(event) => toggle([row.id], event.target.checked)}
                                  className="mt-0.5"
                                />
                                <span className="min-w-0">
                                  <span className="font-mono font-medium text-gray-900">{row.scenario_id}</span>
                                  <span className="block truncate text-gray-500">
                                    {[row.threat, row.outcome].filter(Boolean).join(' — ') || row.narrative}
                                  </span>
                                </span>
                              </label>
                            </li>
                          ))}
                        </ul>
                      </div>
                    );
                  })}
                  {activeEntry.origin !== 'generated' && (
                    <>
                      {drafts.scenarios.map((draft, index) =>
                        draft.systemId === activeEntry.id ? (
                          <DraftScenarioFields
                            key={draft.key}
                            draft={draft}
                            errors={draftErrors(saveErrors, ['scenarios', index])}
                            onChange={(next) => updateExistingDraft(draft.key, next)}
                            onRemove={() => removeExistingDraft(draft.key)}
                          />
                        ) : null
                      )}
                      <button type="button" onClick={() => addDraftToExisting(activeEntry)} className={smallButton}>
                        + Add scenario
                      </button>
                    </>
                  )}
                </>
              )}

              {activeDraftIndex >= 0 && (
                <DraftSystemPanel
                  system={drafts.systems[activeDraftIndex]}
                  errors={draftErrors(saveErrors, ['systems', activeDraftIndex])}
                  onChange={(changes) => updateDraftSystem(activeDraftIndex, changes)}
                  onRemove={() => removeDraftSystem(activeDraftIndex)}
                />
              )}

              {hasDrafts && (
                <p className="text-xs text-gray-500">New systems and scenarios are saved to the corpus when you apply.</p>
              )}
            </div>
          </div>

          {bannerError && <ErrorBanner error={bannerError} />}

          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-gray-200 pt-3">
            <span className="text-xs text-gray-600">
              {`${plural(scenarioCount, 'scenario')} from ${plural(selectedSystems.size, 'system')} selected`}
            </span>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={onClose}
                className="rounded-md border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-100"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleApply}
                disabled={bulkCreate.isPending}
                className="rounded-md bg-brand-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-60"
              >
                {bulkCreate.isPending ? 'Saving…' : 'Apply'}
              </button>
            </div>
          </div>
        </div>
      )}
    </Modal>
  );
}
