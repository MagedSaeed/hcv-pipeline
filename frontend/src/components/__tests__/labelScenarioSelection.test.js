import { describe, expect, it } from 'vitest';
import {
  bulkPayload,
  draftErrors,
  nextScenarioId,
  rowsBySource,
  sourceOptions,
  systemEntries,
  visibleRows,
} from '../labelScenarioSelection';

const row = (id, system, text) => ({ id, system_description: system, scenario_id: `sc_0${id}`, searchText: text });
const groups = [
  { key: 'seed', source: 'seed', title: 'Seed & hand-written scenarios', rows: [row(1, 1, 'remote login'), row(2, 2, 'badge')] },
  { key: 'run-20', source: 'experiment', title: 'Baseline · repetition 2 · Variant B', rows: [row(3, 1, 'spoofed sensor')] },
  { key: 'run-10', source: 'test', title: 'Prompt test run · Variant A', rows: [row(4, 3, 'scratch')] },
];
const systems = [
  { id: 1, title: 'Oldsmar', origin: 'seed', is_scratch: false },
  { id: 2, title: 'Riverbend', origin: 'manual', is_scratch: false },
  { id: 3, title: 'Scratch', origin: 'generated', is_scratch: true },
  { id: 4, title: 'Empty plant', origin: 'manual', is_scratch: false },
];

describe('labelScenarioSelection', () => {
  it('offers hand-written and experiment sources, never prompt test runs', () => {
    expect(sourceOptions(groups)).toEqual([
      { value: 'seed', label: 'Seed & hand-written scenarios' },
      { value: 'run-20', label: 'Baseline · repetition 2 · Variant B' },
    ]);
  });

  it('filters rows by source and search, tagging each with its group', () => {
    expect(visibleRows(groups).map((r) => r.id)).toEqual([1, 2, 3]);
    expect(visibleRows(groups, { source: 'run-20' })).toEqual([
      expect.objectContaining({ id: 3, groupKey: 'run-20', groupTitle: 'Baseline · repetition 2 · Variant B' }),
    ]);
    expect(visibleRows(groups, { search: 'SPOOFED' }).map((r) => r.id)).toEqual([3]);
  });

  it('lists every non-scratch system for all sources, and only matching ones for a run', () => {
    const all = systemEntries(systems, visibleRows(groups));
    expect(all.map((s) => [s.title, s.rows.length])).toEqual([
      ['Oldsmar', 2],
      ['Riverbend', 1],
      ['Empty plant', 0],
    ]);
    const run = systemEntries(systems, visibleRows(groups, { source: 'run-20' }), { source: 'run-20' });
    expect(run.map((s) => s.title)).toEqual(['Oldsmar']);
  });

  it('groups one system rows by source in arrival order', () => {
    const oldsmar = systemEntries(systems, visibleRows(groups))[0];
    expect(rowsBySource(oldsmar.rows).map((g) => [g.title, g.rows.length])).toEqual([
      ['Seed & hand-written scenarios', 1],
      ['Baseline · repetition 2 · Variant B', 1],
    ]);
  });

  it('continues scenario ids after the highest sc_NN', () => {
    expect(nextScenarioId([])).toBe('sc_01');
    expect(nextScenarioId(['sc_01', 'sc_09', 'custom'])).toBe('sc_10');
  });

  it('builds the bulk body without client-only keys', () => {
    const drafts = {
      systems: [{ key: 's1', title: 'New', body: 'b', scenarios: [{ key: 'd1', scenario_id: 'sc_01', threat: 't' }] }],
      scenarios: [{ key: 'd2', systemId: 1, scenario_id: 'sc_03', threat: 'x' }],
    };
    expect(bulkPayload(drafts)).toEqual({
      systems: [{ title: 'New', body: 'b', scenarios: [{ scenario_id: 'sc_01', threat: 't' }] }],
      scenarios: [{ system_description: 1, scenario_id: 'sc_03', threat: 'x' }],
    });
  });

  it('reads one draft errors out of the nested error body', () => {
    const body = { systems: [{}, { title: ['Required.'], scenarios: [{}, { scenario_id: ['Dup.'] }] }] };
    expect(draftErrors(body, ['systems', 1])).toEqual(body.systems[1]);
    expect(draftErrors(body, ['systems', 1, 'scenarios', 1])).toEqual({ scenario_id: ['Dup.'] });
    expect(draftErrors(body, ['scenarios', 0])).toEqual({});
  });

  it('reads errors keyed by index, the shape the API returns', () => {
    const body = { scenarios: { 1: { scenario_id: ['Dup.'] } } };
    expect(draftErrors(body, ['scenarios', 1])).toEqual({ scenario_id: ['Dup.'] });
    expect(draftErrors(body, ['scenarios', 0])).toEqual({});
  });
});
