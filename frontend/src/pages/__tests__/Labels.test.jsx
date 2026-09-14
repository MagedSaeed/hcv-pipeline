import { beforeEach, describe, expect, it } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { authState, server } from '../../test/server';
import { renderApp } from '../../test/renderApp';
import { paginate, paginated } from '../../test/mockApi';

// `/api/labels/` is the one endpoint whose OWN fixture here (PAGINATION_
// LABELS below) actually exceeds PAGE_SIZE=50 - `useAllLabels` (unchanged
// by task 10.1; it was the one hook this suite already got right) has to
// follow `next` to see all of it. `/api/scenarios/`/`/api/system-
// descriptions/` also now fetch every page as of task 10.1 (`useScenarios`/
// `useSystemDescriptions`), but this file's own fixtures for those stay
// small, so `paginated` (a faithful single-page envelope) is still the
// right mock for them - Corpus.test.jsx is the one that proves
// scenarios/system-descriptions pagination with a >50-row fixture. Every
// envelope below is built through the shared `paginate`/`paginated`
// (test/mockApi.js) rather than a one-off like the `paginateLabels` this
// file used to hand-roll.

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

// Run 601 / variant A: 17 scenarios feeding PAGINATION_LABELS below (17 x 3
// HCVs = 51 labels - one past PAGE_SIZE=50). Ordered scenario-major,
// HCV-minor there, so the 51st (last) label - scenario page_17's HCV-03 -
// lands on page 2 once the mock slices by 50, and is only visible to a
// client that actually followed `next` there. Declared before SCENARIOS so
// it can be spread straight into it.
const PAGINATION_SCENARIOS = Array.from({ length: 17 }, (_, index) =>
  scenarioRow({
    id: 8001 + index,
    system_description: 11,
    scenario_id: `page_${String(index + 1).padStart(2, '0')}`,
    _variant: 'A',
    _pipelineRunId: 601,
  })
);

// Run 602 / variant A: 55 scenarios, deliberately carrying NO labels at
// all - unlike PAGINATION_SCENARIOS above (which proves `/api/labels/`
// pagination), this fixture isolates the OTHER confirmed task 10.1 finding:
// Labels' scenario ROWS themselves come from `useScenarios({variant, run})`
// (Labels.jsx's VariantMatrix), which - before this task - read only page
// one. A run with 55 scenarios and no labels proves the ROWS are complete
// (55 table rows, including the 55th) independent of any label-pagination
// logic.
const ROW_PAGINATION_SCENARIOS = Array.from({ length: 55 }, (_, index) =>
  scenarioRow({
    id: 9001 + index,
    system_description: 11,
    scenario_id: `row_${String(index + 1).padStart(2, '0')}`,
    _variant: 'A',
    _pipelineRunId: 602,
  })
);

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
  // Run 601's own 17 pagination-fixture scenarios (see PAGINATION_SCENARIOS above).
  ...PAGINATION_SCENARIOS,
  // Run 602's own 55 row-pagination-fixture scenarios (see
  // ROW_PAGINATION_SCENARIOS above).
  ...ROW_PAGINATION_SCENARIOS,
];

const PHASE_RUNS = [
  { id: 301, pipeline_run: 101, phase: 3, variant: 'A', model: 'm', temperature: 0, prompt_template: 1, taxonomy: 9, source_phase_run: 201, experiment: null, n_systems: null, n_scenarios_per_system: null, status: 'completed', error: '', progress: { done: 2, total: 2, failed: 0, percent: 100 }, total_children: 2, completed_children: 2, failed_children: 0, leak_scan_ran: true, guarded_by: [9], started_at: '2026-01-01T00:00:00Z', finished_at: '2026-01-01T00:01:00Z' },
  { id: 302, pipeline_run: 101, phase: 3, variant: 'B', model: 'm', temperature: 0, prompt_template: 2, taxonomy: 9, source_phase_run: 202, experiment: null, n_systems: null, n_scenarios_per_system: null, status: 'completed', error: '', progress: { done: 3, total: 3, failed: 0, percent: 100 }, total_children: 3, completed_children: 3, failed_children: 0, leak_scan_ran: false, guarded_by: [], started_at: '2026-01-01T00:00:00Z', finished_at: '2026-01-01T00:01:00Z' },
  { id: 303, pipeline_run: 102, phase: 3, variant: 'A', model: 'm', temperature: 0, prompt_template: 1, taxonomy: 9, source_phase_run: 204, experiment: null, n_systems: null, n_scenarios_per_system: null, status: 'completed', error: '', progress: { done: 1, total: 1, failed: 0, percent: 100 }, total_children: 1, completed_children: 1, failed_children: 0, leak_scan_ran: true, guarded_by: [9], started_at: '2026-01-01T00:00:00Z', finished_at: '2026-01-01T00:01:00Z' },
  // Iterate-mode (ad-hoc): `pipeline_run: null`, like an operator tuning a
  // labelling prompt outside any repetition. `label_adhoc` below sits on
  // this phase run - no value of `?run=` may ever surface it.
  { id: 399, pipeline_run: null, phase: 3, variant: 'A', model: 'm', temperature: 0, prompt_template: 1, taxonomy: 9, source_phase_run: 205, experiment: 1, n_systems: 1, n_scenarios_per_system: 1, status: 'completed', error: '', progress: { done: 1, total: 1, failed: 0, percent: 100 }, total_children: 1, completed_children: 1, failed_children: 0, leak_scan_ran: true, guarded_by: [9], started_at: '2026-01-01T00:00:00Z', finished_at: '2026-01-01T00:01:00Z' },
  // Run 601 / variant A - the pagination fixture's own phase run (see
  // PAGINATION_LABELS below).
  { id: 701, pipeline_run: 601, phase: 3, variant: 'A', model: 'm', temperature: 0, prompt_template: 1, taxonomy: 9, source_phase_run: 206, experiment: null, n_systems: null, n_scenarios_per_system: null, status: 'completed', error: '', progress: { done: 17, total: 17, failed: 0, percent: 100 }, total_children: 17, completed_children: 17, failed_children: 0, leak_scan_ran: true, guarded_by: [9], started_at: '2026-01-01T00:00:00Z', finished_at: '2026-01-01T00:01:00Z' },
  // Run 602 / variant A - ROW_PAGINATION_SCENARIOS' own phase run.
  { id: 702, pipeline_run: 602, phase: 3, variant: 'A', model: 'm', temperature: 0, prompt_template: 1, taxonomy: 9, source_phase_run: 207, experiment: null, n_systems: null, n_scenarios_per_system: null, status: 'completed', error: '', progress: { done: 55, total: 55, failed: 0, percent: 100 }, total_children: 55, completed_children: 55, failed_children: 0, leak_scan_ran: true, guarded_by: [9], started_at: '2026-01-01T00:00:00Z', finished_at: '2026-01-01T00:01:00Z' },
];

// Feeds run 601 / variant A's phase run (701) - see PAGINATION_SCENARIOS
// above. 17 scenarios x 3 HCVs = 51 labels - one past PAGE_SIZE (50).
// Declared before LABELS so it can be spread straight into it.
const PAGINATION_LABELS = PAGINATION_SCENARIOS.flatMap((scenario, sIndex) =>
  HCVS.map((hcv, hIndex) => ({
    id: 80000 + sIndex * 10 + hIndex,
    phase_run: 701,
    scenario: scenario.id,
    scenario_id: scenario.scenario_id,
    hcv: hcv.id,
    hcv_id: hcv.hcv_id,
    hcv_name: hcv.name,
    evidence: `Pagination evidence ${scenario.scenario_id} ${hcv.hcv_id}.`,
    rationale: `Pagination rationale ${scenario.scenario_id} ${hcv.hcv_id}.`,
    source: 'llm',
    llm_call: 90000 + sIndex * 10 + hIndex,
    created_by: null,
    created_at: '2026-01-01T00:00:00Z',
  }))
);

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
  // Ad-hoc (phase_run 399, pipeline_run: null) - unreachable via ANY
  // `?run=`. Doubles as a canary for the labels `?run=` filter itself:
  // it sits on scenario 1001 / HCV-02 - a cell the "visibly empty" test
  // below asserts stays empty for run 101 - so if `?run=` were ever
  // dropped from the labels request, this ad-hoc row (querying by
  // `?variant=A` alone would still include it) would leak into that cell
  // and the assertion would fail.
  { id: 9301, phase_run: 399, scenario: 1001, scenario_id: 'sc_01', hcv: 902, hcv_id: 'HCV-02', hcv_name: 'Social isolation', evidence: 'Ad-hoc iterate-mode evidence.', rationale: 'Ad-hoc iterate-mode rationale.', source: 'llm', llm_call: 5301, created_by: null, created_at: '2026-01-01T00:00:00Z' },
  // Deliberately cross-wired: attached to phase_run 302 (run 101, variant
  // B) but pointed at scenario 1002, which belongs to variant A. Nothing
  // in the DB schema forbids this (only app-level convention does), and
  // it exists purely as a canary for the labels `?variant=` filter: under
  // the correct `?run=101&variant=A` query this is excluded (wrong
  // variant), so the "visibly empty" test's scenario-1002/HCV-02 check
  // stays green; drop `?variant=` from that request (while keeping
  // `?run=`) and this row leaks straight into that same cell.
  { id: 9302, phase_run: 302, scenario: 1002, scenario_id: 'sc_02', hcv: 902, hcv_id: 'HCV-02', hcv_name: 'Social isolation', evidence: 'Cross-variant decoy evidence.', rationale: 'Cross-variant decoy rationale.', source: 'llm', llm_call: 5302, created_by: null, created_at: '2026-01-01T00:00:00Z' },
  // Run 601's own 51 pagination-fixture labels (see PAGINATION_LABELS above).
  ...PAGINATION_LABELS,
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

function phaseRunById(id) {
  return PHASE_RUNS.find((phaseRun) => phaseRun.id === id);
}

// Mirrors pipeline/views.py::LabelViewSet.get_queryset exactly: `?run=` and
// `?variant=` are NOT fields on Label itself - both reach through the
// labelling PhaseRun that produced the row (`phase_run__pipeline_run_id` /
// `phase_run__variant`). An ad-hoc PhaseRun's `pipeline_run` is `null`, so
// `String(phaseRunRow.pipeline_run) === run` is never true for it no matter
// what `run` is - `?phase_run=` (already handled below) is its only route in,
// same as the real API.
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
  const run = url.searchParams.get('run');
  if (run !== null && run !== '') {
    result = result.filter((row) => {
      const phaseRunRow = phaseRunById(row.phase_run);
      return phaseRunRow != null && phaseRunRow.pipeline_run != null && String(phaseRunRow.pipeline_run) === run;
    });
  }
  const variant = url.searchParams.get('variant');
  if (variant) {
    result = result.filter((row) => phaseRunById(row.phase_run)?.variant === variant);
  }
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
      return HttpResponse.json(paginate(applyScenarioFilters(SCENARIOS, url).map(toScenarioWire), url));
    }),
    http.get('/api/phase-runs/', ({ request }) => {
      const url = new URL(request.url);
      return HttpResponse.json(paginated(applyPhaseRunFilters(PHASE_RUNS, url)));
    }),
    http.get('/api/labels/', ({ request }) => {
      const url = new URL(request.url);
      // A non-integer `?run=` is a 400 on the real API (`core.params.
      // integer_param`), not a silently-ignored filter - matched here so a
      // client that ever sent a malformed value would see the real failure
      // mode in a test, not a mock that is more permissive than the API.
      const run = url.searchParams.get('run');
      if (run !== null && run !== '' && Number.isNaN(Number(run))) {
        return HttpResponse.json({ run: ['A valid integer is required.'] }, { status: 400 });
      }
      return HttpResponse.json(paginate(applyLabelFilters(LABELS, url), url));
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
  await user.click(screen.getByRole('button', { name: /^apply$/i }));
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

  it('fetches every page of a variant\'s labels, not just the first 50 (fails if only page 1 is read)', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/labels'] });

    // Run 601 / variant A has 51 labels (PAGINATION_LABELS) - one past the
    // mock's own PAGE_SIZE of 50. The very last one, scenario page_17's
    // HCV-03, is only ever returned on page 2.
    await applyRunFilter(user, { run: 601, variant: 'A' });
    const matrix = await screen.findByTestId('variant-A-matrix');

    // Every one of the 17 scenario rows renders regardless (scenarios are
    // not paginated in this fixture) - the row existing is not what proves
    // pagination was followed; the CELL being filled is.
    expect(within(matrix).getAllByRole('row')).toHaveLength(1 + 17);

    const lastScenario = PAGINATION_SCENARIOS[PAGINATION_SCENARIOS.length - 1];
    const cell = within(matrix).getByTestId(`cell-${lastScenario.id}-HCV-03`);
    const marker = within(cell).getByRole('button');

    await user.hover(marker);
    const tooltip = await screen.findByRole('tooltip');
    expect(within(tooltip).getByText(/Pagination evidence page_17 HCV-03/)).toBeInTheDocument();
  });

  it('fetches every page of a variant\'s scenario ROWS, not just the first 50 (fails if only page 1 is read)', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/labels'] });

    // Run 602 / variant A has 55 scenarios and zero labels - isolates the
    // scenario-ROW pagination bug (Labels.jsx's VariantMatrix reading
    // useScenarios) from the label-CELL pagination proven by the test
    // above. One page (50) could never show the 51st-55th rows at all.
    await applyRunFilter(user, { run: 602, variant: 'A' });
    const matrix = await screen.findByTestId('variant-A-matrix');

    expect(within(matrix).getAllByRole('row')).toHaveLength(1 + 55); // header + 55 scenarios
    expect(within(matrix).getByRole('rowheader', { name: /System Alpha \/ row_55/ })).toBeInTheDocument();
  });

  it("preselects the run from a URL param, the way Run detail's own link arrives", async () => {
    renderApp({ initialEntries: ['/labels?run=101'] });

    // No "Apply filters" click needed - the matrices render straight away.
    expect(await screen.findByRole('rowheader', { name: /System Alpha \/ sc_01/ })).toBeInTheDocument();
    expect(screen.getByLabelText(/run \(repetition id\)/i)).toHaveValue(101);
  });

  it("uses each variant's own PINNED taxonomy for its columns, not the currently active one", async () => {
    const user = userEvent.setup();
    // TAXONOMY (id 9) is what every phase run in this file actually pins -
    // deactivate it and make a DIFFERENT taxonomy (id 20, a later version)
    // active instead. Before task 10.2, this screen read its columns from
    // whichever taxonomy is ACTIVE - it would have silently rendered
    // variant A's real, pinned-taxonomy-9 labels under taxonomy 20's own
    // HCV set instead (dropping any label on an HCV taxonomy 20 doesn't
    // contain).
    const activeButUnused = {
      id: 20,
      vocabulary_id: 'hcv-21',
      version: '2',
      notes: '',
      is_active: true,
      hcv_count: 21,
      created_at: '2026-03-01T00:00:00Z',
    };
    server.use(
      http.get('/api/taxonomies/', () => HttpResponse.json(paginated([{ ...TAXONOMY, is_active: false }, activeButUnused]))),
      http.get('/api/taxonomies/:id/hcvs/', ({ params }) => {
        if (Number(params.id) !== TAXONOMY.id) return HttpResponse.json(paginated([]));
        return HttpResponse.json(paginated(HCVS));
      })
    );

    renderApp({ initialEntries: ['/labels'] });
    await applyRunFilter(user, { run: 101, variant: 'A' });

    const matrix = await screen.findByTestId('variant-A-matrix');
    // Still the PINNED taxonomy's own columns and labels - unaffected by
    // which one is active.
    expect(within(matrix).getByText('HCV-01')).toBeInTheDocument();
    expect(within(within(matrix).getByTestId('cell-1001-HCV-01')).getByRole('button')).toBeInTheDocument();
    // And it says so plainly, rather than silently using taxonomy 20.
    expect(within(matrix).getByText(/hcv-21 v1/i)).toBeInTheDocument();
    expect(within(matrix).getByText(/not the currently active hcv-21 v2/i)).toBeInTheDocument();
  });

  it("says plainly when a variant's own phase runs pinned different taxonomies, rather than merging them", async () => {
    const user = userEvent.setup();
    const mismatchScenario = scenarioRow({
      id: 8501,
      system_description: 11,
      scenario_id: 'mismatch_01',
      _variant: 'A',
      _pipelineRunId: 603,
    });
    const mismatchPhaseRuns = [
      { id: 801, pipeline_run: 603, phase: 3, variant: 'A', model: 'm', temperature: 0, prompt_template: 1, taxonomy: 9, source_phase_run: 210, experiment: null, n_systems: null, n_scenarios_per_system: null, status: 'completed', error: '', progress: { done: 1, total: 1, failed: 0, percent: 100 }, total_children: 1, completed_children: 1, failed_children: 0, leak_scan_ran: true, guarded_by: [9], started_at: '2026-01-01T00:00:00Z', finished_at: '2026-01-01T00:01:00Z' },
      { id: 802, pipeline_run: 603, phase: 3, variant: 'A', model: 'm', temperature: 0, prompt_template: 1, taxonomy: 21, source_phase_run: 211, experiment: null, n_systems: null, n_scenarios_per_system: null, status: 'completed', error: '', progress: { done: 1, total: 1, failed: 0, percent: 100 }, total_children: 1, completed_children: 1, failed_children: 0, leak_scan_ran: true, guarded_by: [21], started_at: '2026-01-01T00:00:00Z', finished_at: '2026-01-01T00:01:00Z' },
    ];
    server.use(
      http.get('/api/scenarios/', ({ request }) => {
        const url = new URL(request.url);
        return HttpResponse.json(
          paginate(applyScenarioFilters([...SCENARIOS, mismatchScenario], url).map(toScenarioWire), url)
        );
      }),
      http.get('/api/phase-runs/', ({ request }) => {
        const url = new URL(request.url);
        return HttpResponse.json(paginate(applyPhaseRunFilters([...PHASE_RUNS, ...mismatchPhaseRuns], url), url));
      })
    );

    renderApp({ initialEntries: ['/labels'] });
    await applyRunFilter(user, { run: 603, variant: 'A' });

    const matrix = await screen.findByTestId('variant-A-matrix');
    expect(within(matrix).getByText(/used more than one taxonomy version/i)).toBeInTheDocument();
    // Nothing merged - no table at all for this variant.
    expect(within(matrix).queryByRole('table')).not.toBeInTheDocument();
  });
});
