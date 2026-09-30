import { CARD_FIELDS } from './scenarioCard';

// Prompt-test output can't be labelled in an experiment, so its groups are never offered.
function selectableGroups(groups) {
  return groups.filter((group) => group.source !== 'test');
}

function searchWords(search) {
  return search.toLowerCase().split(/\s+/).filter(Boolean);
}

export function sourceOptions(groups) {
  return selectableGroups(groups).map((group) => ({ value: group.key, label: group.title }));
}

export function visibleRows(groups, { source = '', search = '' } = {}) {
  const words = searchWords(search);
  return selectableGroups(groups)
    .filter((group) => !source || group.key === source)
    .flatMap((group) =>
      group.rows
        .filter((row) => words.every((word) => row.searchText.includes(word)))
        .map((row) => ({ ...row, groupKey: group.key, groupTitle: group.title }))
    );
}

// With no source chosen, systems without scenarios stay listed so new ones can be added to them.
export function systemEntries(systems, rows, { source = '', search = '' } = {}) {
  const bySystem = new Map();
  for (const row of rows) {
    if (!bySystem.has(row.system_description)) bySystem.set(row.system_description, []);
    bySystem.get(row.system_description).push(row);
  }
  const words = searchWords(search);
  return systems
    .filter((system) => !system.is_scratch)
    .filter((system) => {
      if (bySystem.has(system.id)) return true;
      if (source) return false;
      return words.every((word) => system.title.toLowerCase().includes(word));
    })
    .map((system) => ({ ...system, rows: bySystem.get(system.id) ?? [] }));
}

export function rowsBySource(rows) {
  const bySource = new Map();
  for (const row of rows) {
    if (!bySource.has(row.groupKey)) bySource.set(row.groupKey, { key: row.groupKey, title: row.groupTitle, rows: [] });
    bySource.get(row.groupKey).rows.push(row);
  }
  return [...bySource.values()];
}

export function nextScenarioId(takenIds) {
  let highest = 0;
  for (const id of takenIds) {
    const match = /^sc_(\d+)$/.exec(id);
    if (match) highest = Math.max(highest, Number(match[1]));
  }
  return `sc_${String(highest + 1).padStart(2, '0')}`;
}

let draftCounter = 0;
export function draftKey() {
  draftCounter += 1;
  return `draft-${draftCounter}`;
}

export function emptyDraftScenario(scenarioId) {
  return {
    key: draftKey(),
    scenario_id: scenarioId,
    ...Object.fromEntries(CARD_FIELDS.map(([field]) => [field, ''])),
    narrative: '',
  };
}

function withoutKey({ key: _key, ...fields }) {
  return fields;
}

export function bulkPayload(drafts) {
  return {
    systems: drafts.systems.map(({ title, body, scenarios }) => ({ title, body, scenarios: scenarios.map(withoutKey) })),
    scenarios: drafts.scenarios.map(({ systemId, ...rest }) => ({ system_description: systemId, ...withoutKey(rest) })),
  };
}

// The bulk endpoint nests errors by index, as lists or index-keyed objects.
export function draftErrors(body, path) {
  let node = body;
  for (const step of path) node = node?.[step];
  return node && typeof node === 'object' && !Array.isArray(node) ? node : {};
}
