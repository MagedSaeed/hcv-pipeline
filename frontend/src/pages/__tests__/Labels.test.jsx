import { beforeEach, describe, expect, it } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { authState, server } from '../../test/server';
import { renderApp } from '../../test/renderApp';

function paginated(results) {
  return { count: results.length, next: null, previous: null, results };
}

// A deliberately small taxonomy (3 HCVs, not the real 21) - Labels.jsx must
// treat this as "the whole taxonomy" and render every one of them as a
// column regardless of whether any label landed on it, so a small, fully
// enumerable fixture is what actually lets a test assert that (a real
// 21-row fixture would make "assert every column present" unwieldy without
// proving anything a 3-row one doesn't already prove).
const TAXONOMY = { id: 9, vocabulary_id: 'hcv-21', version: '1', notes: '', is_active: true, hcv_count: 3, created_at: '2026-01-01T00:00:00Z' };
const HCVS = [
  { id: 901, hcv_id: 'HCV-01', name: 'Physical exposure', definition: 'd1', condition_classes: ['physical'], order: 1 },
  { id: 902, hcv_id: 'HCV-02', name: 'Social isolation', definition: 'd2', condition_classes: ['social'], order: 2 },
  { id: 903, hcv_id: 'HCV-03', name: 'Displacement risk', definition: 'd3', condition_classes: ['physical'], order: 3 },
];

const SYSTEMS = [
  { id: 11, title: 'System Alpha', slug: 'system-alpha', body: 'alpha body', origin: 'generated', is_editable: false, created_by: null, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' },
  { id: 12, title: 'System Beta', slug: 'system-beta', body: 'beta body', origin: 'generated', is_editable: false, created_by: null, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' },
  { id: 13, title: 'System Gamma (run 102)', slug: 'system-gamma', body: 'gamma body', origin: 'generated', is_editable: false, created_by: null, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' },
];

const CARD_BLANKS = { actor: '', trigger: '', standing_state: '', asset: '', threat: '', outcome: '' };

function scenarioRow(overrides) {
  return {
    ...CARD_BLANKS,
    narrative: '',
    intended_hcvs: [],
    abox: null,
    card: CARD_BLANKS,
    origin: 'generated',
    is_editable: false,
    order: 1,
    created_by: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  };
}

// Run 101 variant A has TWO scenarios; run 101 variant B has THREE - a
// deliberate mismatch. If Labels.jsx ever tried to zip the two variants'
// rows together by index (the exact merged view spec §10 forbids), B's
// third row would either vanish or be misattributed to a nonexistent A
// row - either way a test asserting B's third scenario appears, independent
// of A's row count, would catch it.
const SCENARIOS = [
  scenarioRow({ id: 1001, system_description: 11, scenario_id: 'sc_01', _variant: 'A', _pipelineRunId: 101 }),
  scenarioRow({ id: 1002, system_description: 11, scenario_id: 'sc_02', _variant: 'A', _pipelineRunId: 101 }),
  scenarioRow({ id: 2001, system_description: 12, scenario_id: 'sc_01', _variant: 'B', _pipelineRunId: 101 }),
  scenarioRow({ id: 2002, system_description: 12, scenario_id: 'sc_02', _variant: 'B', _pipelineRunId: 101 }),
  scenarioRow({ id: 2003, system_description: 12, scenario_id: 'sc_03', _variant: 'B', _pipelineRunId: 101 }),
  // A decoy from a different run - must never appear once `run=101` is applied.
  scenarioRow({ id: 3001, system_description: 13, scenario_id: 'sc_01', _variant: 'A', _pipelineRunId: 102 }),
];

const PHASE_RUNS = [
  { id: 301, pipeline_run: 101, phase: 3, variant: 'A', model: 'm', temperature: 0, prompt_template: 1, taxonomy: 9, source_phase_run: 201, experiment: null, n_systems: null, n_scenarios_per_system: null, status: 'completed', error: '', progress: { done: 2, total: 2, failed: 0, percent: 100 }, total_children: 2, completed_children: 2, failed_children: 0, leak_scan_ran: true, guarded_by: [9], started_at: '2026-01-01T00:00:00Z', finished_at: '2026-01-01T00:01:00Z' },
  { id: 302, pipeline_run: 101, phase: 3, variant: 'B', model: 'm', temperature: 0, prompt_template: 2, taxonomy: 9, source_phase_run: 202, experiment: null, n_systems: null, n_scenarios_per_system: null, status: 'completed', error: '', progress: { done: 3, total: 3, failed: 0, percent: 100 }, total_children: 3, completed_children: 3, failed_children: 0, leak_scan_ran: false, guarded_by: [], started_at: '2026-01-01T00:00:00Z', finished_at: '2026-01-01T00:01:00Z' },
  { id: 303, pipeline_run: 102, phase: 3, variant: 'A', model: 'm', temperature: 0, prompt_template: 1, taxonomy: 9, source_phase_run: 204, experiment: null, n_systems: null, n_scenarios_per_system: null, status: 'completed', error: '', progress: { done: 1, total: 1, failed: 0, percent: 100 }, total_children: 1, completed_children: 1, failed_children: 0, leak_scan_ran: true, guarded_by: [9], started_at: '2026-01-01T00:00:00Z', finished_at: '2026-01-01T00:01:00Z' },
];

// HCV-02 gets NO label anywhere in run 101 - on purpose, for both variants -
// the exact "an HCV with no labels in a variant" case spec §10 requires to
// stay visible rather than silently disappearing from the matrix.
const LABELS = [
  { id: 9001, phase_run: 301, scenario: 1001, scenario_id: 'sc_01', hcv: 901, hcv_id: 'HCV-01', hcv_name: 'Physical exposure', evidence: 'Flood eyewitness account inside sc_01 narrative.', rationale: 'Direct account of physical exposure to a hazard.', source: 'llm', llm_call: 5001, created_by: null, created_at: '2026-01-01T00:00:00Z' },
  { id: 9002, phase_run: 301, scenario: 1002, scenario_id: 'sc_02', hcv: 903, hcv_id: 'HCV-03', hcv_name: 'Displacement risk', evidence: 'sc_02 describes a forced relocation.', rationale: 'Household is displaced from its dwelling.', source: 'llm', llm_call: 5002, created_by: null, created_at: '2026-01-01T00:00:00Z' },
  { id: 9101, phase_run: 302, scenario: 2001, scenario_id: 'sc_01', hcv: 901, hcv_id: 'HCV-01', hcv_name: 'Physical exposure', evidence: 'Variant B sc_01 evidence text.', rationale: 'Variant B sc_01 rationale text.', source: 'llm', llm_call: 5101, created_by: null, created_at: '2026-01-01T00:00:00Z' },
  { id: 9102, phase_run: 302, scenario: 2003, scenario_id: 'sc_03', hcv: 903, hcv_id: 'HCV-03', hcv_name: 'Displacement risk', evidence: 'Variant B sc_03 evidence text.', rationale: 'Variant B sc_03 rationale text.', source: 'llm', llm_call: 5102, created_by: null, created_at: '2026-01-01T00:00:00Z' },
  // A decoy from run 102's phase run (303) - must never appear once `run=101` is applied.
  { id: 9201, phase_run: 303, scenario: 3001, scenario_id: 'sc_01', hcv: 901, hcv_id: 'HCV-01', hcv_name: 'Physical exposure', evidence: 'Run 102 decoy evidence.', rationale: 'Run 102 decoy rationale.', source: 'llm', llm_call: 5201, created_by: null, created_at: '2026-01-01T00:00:00Z' },
];

function toScenarioWire(row) {
  // eslint-disable-next-line no-unused-vars
  const { _variant, _pipelineRunId, ...wire } = row;
  return wire;
}

function applyScenarioFilters(rows, url) {
  let result = rows;
  const variant = url.searchParams.get('variant');
  if (variant) result = result.filter((row) => row._variant === variant);
  const run = url.searchParams.get('run');
  if (run !== null && run !== '') result = result.filter((row) => String(row._pipelineRunId) === run);
  return result;
}

function applyPhaseRunFilters(rows, url) {
  let result = rows;
  const pipelineRun = url.searchParams.get('pipeline_run');
  if (pipelineRun !== null && pipelineRun !== '') result = result.filter((row) => String(row.pipeline_run) === pipelineRun);
  const phase = url.searchParams.get('phase');
  if (phase !== null && phase !== '') result = result.filter((row) => String(row.phase) === phase);
  const variant = url.searchParams.get('variant');
  if (variant) result = result.filter((row) => row.variant === variant);
  return result;
}

function applyLabelFilters(rows, url) {
  let result = rows;
  const phaseRun = url.searchParams.get('phase_run');
  if (phaseRun !== null && phaseRun !== '') result = result.filter((row) => String(row.phase_run) === phaseRun);
  const hcv = url.searchParams.get('hcv');
  if (hcv) {
    result = Number.isNaN(Number(hcv)) ? result.filter((row) => row.hcv_id === hcv) : result.filter((row) => String(row.hcv) === hcv);
  }
  const scenario = url.searchParams.get('scenario');
  if (scenario !== null && scenario !== '') result = result.filter((row) => String(row.scenario) === scenario);
  const source = url.searchParams.get('source');
  if (source) result = result.filter((row) => row.source === source);
  return result;
}

beforeEach(() => {
  authState.currentUserEmail = 'researcher@example.com';

  server.use(
    http.get('/api/taxonomies/', () => HttpResponse.json(paginated([TAXONOMY]))),
    http.get('/api/taxonomies/:id/hcvs/', ({ params }) => {
      if (Number(params.id) !== TAXONOMY.id) return HttpResponse.json(paginated([]));
      return HttpResponse.json(paginated(HCVS));
    }),
    http.get('/api/system-descriptions/', () => HttpResponse.json(paginated(SYSTEMS))),
    http.get('/api/scenarios/', ({ request }) => {
      const url = new URL(request.url);
      return HttpResponse.json(paginated(applyScenarioFilters(SCENARIOS, url).map(toScenarioWire)));
    }),
    http.get('/api/phase-runs/', ({ request }) => {
      const url = new URL(request.url);
      return HttpResponse.json(paginated(applyPhaseRunFilters(PHASE_RUNS, url)));
    }),
    http.get('/api/labels/', ({ request }) => {
      const url = new URL(request.url);
      return HttpResponse.json(paginated(applyLabelFilters(LABELS, url)));
    })
  );
});

async function applyRunFilter(user, { run, variant, hcv } = {}) {
  if (run !== undefined) {
    const field = await screen.findByLabelText(/run \(repetition id\)/i);
    await user.clear(field);
    if (run !== '') await user.type(field, String(run));
  }
  if (variant !== undefined) await user.selectOptions(screen.getByLabelText(/^variant$/i), variant);
  if (hcv !== undefined) await user.selectOptions(screen.getByLabelText(/^hcv$/i), hcv);
  await user.click(screen.getByRole('button', { name: /apply filters/i }));
}

describe('Labels', () => {
  it('renders one scenario x HCV matrix per variant, not a merged view', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/labels'] });

    await applyRunFilter(user, { run: 101 });

    const variantA = await screen.findByTestId('variant-A-matrix');
    const variantB = await screen.findByTestId('variant-B-matrix');

    // Each variant is its own <table>, with its own row set.
    expect(within(variantA).getByRole('table')).toBeInTheDocument();
    expect(within(variantB).getByRole('table')).toBeInTheDocument();

    // A has 2 scenario rows, B has 3 - proving they were never zipped
    // together by index into one shared row set.
    expect(within(variantA).getAllByRole('row')).toHaveLength(1 + 2); // header + 2 scenarios
    expect(within(variantB).getAllByRole('row')).toHaveLength(1 + 3); // header + 3 scenarios

    // B's third scenario (sc_03) has no A counterpart at all - if the two
    // variants were merged/aligned by row index, this content could only
    // appear misattributed to A's row 3, which doesn't exist.
    expect(within(variantB).getByRole('rowheader', { name: /System Beta \/ sc_03/ })).toBeInTheDocument();
    expect(within(variantA).queryByRole('rowheader', { name: /sc_03/ })).not.toBeInTheDocument();
  });

  it('shows evidence and rationale on hover, not by default', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/labels'] });

    await applyRunFilter(user, { run: 101 });
    await screen.findByTestId('variant-A-matrix');

    const cell = screen.getByTestId('cell-1001-HCV-01');
    const marker = within(cell).getByRole('button');

    // Not shown before hovering.
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
    expect(screen.queryByText(/Flood eyewitness account/)).not.toBeInTheDocument();

    await user.hover(marker);
    const tooltip = await screen.findByRole('tooltip');
    expect(within(tooltip).getByText(/Flood eyewitness account inside sc_01 narrative/)).toBeInTheDocument();
    expect(within(tooltip).getByText(/Direct account of physical exposure to a hazard/)).toBeInTheDocument();

    await user.unhover(marker);
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
  });

  it('the run filter excludes another run entirely (fails if the filter is dropped)', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/labels'] });

    // Nothing shown until a run is picked.
    expect(screen.queryByTestId('variant-A-matrix')).not.toBeInTheDocument();

    await applyRunFilter(user, { run: 101 });
    expect(await screen.findByRole('rowheader', { name: /System Alpha \/ sc_01/ })).toBeInTheDocument();
    // Run 102's own scenario/system never appears under run=101.
    expect(screen.queryByText(/System Gamma/)).not.toBeInTheDocument();

    await applyRunFilter(user, { run: 102 });
    expect(await screen.findByRole('rowheader', { name: /System Gamma \(run 102\) \/ sc_01/ })).toBeInTheDocument();
    // And run 101's material disappears once scoped to 102.
    expect(screen.queryByText(/System Alpha/)).not.toBeInTheDocument();
    expect(screen.queryByText(/System Beta/)).not.toBeInTheDocument();
  });

  it('the variant filter shows only the selected variant (fails if the filter is dropped)', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/labels'] });

    await applyRunFilter(user, { run: 101 });
    await screen.findByTestId('variant-A-matrix');
    expect(screen.getByTestId('variant-B-matrix')).toBeInTheDocument();

    await applyRunFilter(user, { variant: 'A' });
    expect(await screen.findByTestId('variant-A-matrix')).toBeInTheDocument();
    expect(screen.queryByTestId('variant-B-matrix')).not.toBeInTheDocument();

    await applyRunFilter(user, { variant: '' });
    expect(await screen.findByTestId('variant-B-matrix')).toBeInTheDocument();
  });

  it('the HCV filter narrows to one column (fails if the filter is dropped)', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/labels'] });

    await applyRunFilter(user, { run: 101, variant: 'A' });
    const matrix = await screen.findByTestId('variant-A-matrix');
    expect(within(matrix).getByText('HCV-01')).toBeInTheDocument();
    expect(within(matrix).getByText('HCV-03')).toBeInTheDocument();

    await applyRunFilter(user, { hcv: 'HCV-01' });
    const narrowed = await screen.findByTestId('variant-A-matrix');
    expect(within(narrowed).getByText('HCV-01')).toBeInTheDocument();
    expect(within(narrowed).queryByText('HCV-02')).not.toBeInTheDocument();
    expect(within(narrowed).queryByText('HCV-03')).not.toBeInTheDocument();

    await applyRunFilter(user, { hcv: '' });
    const restored = await screen.findByTestId('variant-A-matrix');
    expect(within(restored).getByText('HCV-01')).toBeInTheDocument();
    expect(within(restored).getByText('HCV-03')).toBeInTheDocument();
  });

  it('an HCV with no labels in a variant is visibly empty rather than absent', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/labels'] });

    await applyRunFilter(user, { run: 101 });
    const variantA = await screen.findByTestId('variant-A-matrix');
    const variantB = await screen.findByTestId('variant-B-matrix');

    // HCV-02 got zero labels in EITHER variant - the column must still be
    // present in both, not dropped for having nothing in it.
    expect(within(variantA).getByText('HCV-02')).toBeInTheDocument();
    expect(within(variantB).getByText('HCV-02')).toBeInTheDocument();

    // Every cell under that column, for every scenario row, must render as
    // explicitly empty - no filled marker anywhere in that column.
    for (const scenarioId of [1001, 1002]) {
      const cell = within(variantA).getByTestId(`cell-${scenarioId}-HCV-02`);
      expect(within(cell).queryByRole('button')).not.toBeInTheDocument();
    }
    for (const scenarioId of [2001, 2002, 2003]) {
      const cell = within(variantB).getByTestId(`cell-${scenarioId}-HCV-02`);
      expect(within(cell).queryByRole('button')).not.toBeInTheDocument();
    }

    // Meanwhile HCV-01 (which DID get labels) has at least one filled cell
    // in each variant - proving the empty/filled distinction is real, not
    // every cell rendering the same regardless of data.
    expect(within(within(variantA).getByTestId('cell-1001-HCV-01')).getByRole('button')).toBeInTheDocument();
    expect(within(within(variantB).getByTestId('cell-2001-HCV-01')).getByRole('button')).toBeInTheDocument();
  });

  it('computes and displays no statistic anywhere on the page', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/labels'] });

    await applyRunFilter(user, { run: 101 });
    await screen.findByTestId('variant-A-matrix');
    await screen.findByTestId('variant-B-matrix');

    const text = screen.getByTestId('labels-page').textContent;

    // No percentage, fraction, "N of M", or coverage/comparison language -
    // exactly the kind of finding this screen must leave to the researcher.
    expect(text).not.toMatch(/%/);
    expect(text).not.toMatch(/\d+\s*\/\s*\d+/);
    expect(text).not.toMatch(/\d+\s+of\s+\d+/i);
    expect(text).not.toMatch(/coverage/i);
    expect(text).not.toMatch(/\bdelta\b/i);
    expect(text).not.toMatch(/\bdifference\b/i);
    expect(text).not.toMatch(/\bmore than\b/i);
    expect(text).not.toMatch(/\bfewer than\b/i);
    expect(text).not.toMatch(/\baverage\b/i);
    expect(text).not.toMatch(/\bscore\b/i);
    expect(text).not.toMatch(/\bcount\b/i);
    // Bare counts ("2 scenarios", "3 labels", "5 HCVs") are exactly the
    // "well-meaning addition" the brief warns about - not a ratio or a
    // percentage, but still a number presented as a finding.
    expect(text).not.toMatch(/\d+\s+scenarios?\b/i);
    expect(text).not.toMatch(/\d+\s+labels?\b/i);
    expect(text).not.toMatch(/\d+\s+hcvs?\b/i);
  });
});
