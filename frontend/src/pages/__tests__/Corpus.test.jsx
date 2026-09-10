import { beforeEach, describe, expect, it } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { authState, server } from '../../test/server';
import { renderApp } from '../../test/renderApp';
import { paginate, requireCsrf } from '../../test/mockApi';

// Mirrors backend/corpus/tests/test_corpus_filters.py's own `corpus_runs`
// fixture shape: two repetitions (pipeline runs 101/102, behind phase runs
// 201/202) plus one ad-hoc iterate-mode phase run (203, pipeline_run=null -
// exactly the shape `?run=` can never reach). `_variant`/`_pipelineRunId`/
// `_generationRunId` are TEST-ONLY bookkeeping used by the fake filtering
// below; corpus.serializers.SystemDescriptionSerializer/ScenarioSerializer
// never actually put `variant`/`pipeline_run`/`generation_run` on the wire
// (verified by reading them), so every row is stripped down to the real
// field set before being sent as a response - a handler that leaked those
// fields would make the frontend's use of `usePhaseRun` to find out
// whether `?phase_run=` is scratch material entirely unnecessary, hiding
// exactly the gap that hook exists to close.
const SYSTEM_ROWS = [
  { id: 1, title: 'Seed system', slug: 'seed-system', body: 'original seed body', origin: 'seed', is_editable: true, created_by: 1, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z', _variant: null, _pipelineRunId: null, _generationRunId: null },
  { id: 2, title: 'Manual system', slug: 'manual-system', body: 'typed in by hand', origin: 'manual', is_editable: true, created_by: 1, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z', _variant: null, _pipelineRunId: null, _generationRunId: null },
  { id: 3, title: 'System A', slug: 'system-a', body: 'model output A', origin: 'generated', is_editable: false, created_by: null, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z', _variant: 'A', _pipelineRunId: 101, _generationRunId: 201 },
  { id: 4, title: 'System B', slug: 'system-b', body: 'model output B', origin: 'generated', is_editable: false, created_by: null, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z', _variant: 'B', _pipelineRunId: 102, _generationRunId: 202 },
  { id: 5, title: 'System adhoc', slug: 'system-adhoc', body: 'tuning output', origin: 'generated', is_editable: false, created_by: null, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z', _variant: 'A', _pipelineRunId: null, _generationRunId: 203 },
  // 55 filler rows (ids 6-60) so the UNFILTERED system-descriptions list
  // passes PAGE_SIZE=50 (task 10.1: Corpus must show the complete corpus,
  // not a first page of it). `_pipelineRunId`/`_generationRunId: null`
  // keeps them out of every `?run=`/`?phase_run=` filtered assertion above;
  // `origin: 'manual'` keeps them out of the `origin=generated` filter test
  // too - they only ever surface on an UNFILTERED (or `origin=manual`)
  // request, which is exactly the request the new pagination test below
  // makes.
  ...Array.from({ length: 55 }, (_, index) => ({
    id: 6 + index,
    title: `Filler system ${6 + index}`,
    slug: `filler-system-${6 + index}`,
    body: 'filler body',
    origin: 'manual',
    is_editable: true,
    created_by: 1,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    _variant: null,
    _pipelineRunId: null,
    _generationRunId: null,
  })),
];

const CARD_BLANKS = { actor: '', trigger: '', standing_state: '', asset: '', threat: '', outcome: '' };

const SCENARIO_ROWS = [
  { id: 1, system_description: 1, scenario_id: 'sc_01', ...CARD_BLANKS, narrative: 'Manual narrative', intended_hcvs: [], abox: null, card: CARD_BLANKS, origin: 'manual', is_editable: true, order: 1, created_by: 1, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z', _variant: null, _pipelineRunId: null, _generationRunId: null },
  { id: 2, system_description: 3, scenario_id: 'sc_01', ...CARD_BLANKS, narrative: 'Generated narrative A', intended_hcvs: [], abox: null, card: CARD_BLANKS, origin: 'generated', is_editable: false, order: 1, created_by: null, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z', _variant: 'A', _pipelineRunId: 101, _generationRunId: 201 },
  { id: 3, system_description: 4, scenario_id: 'sc_01', ...CARD_BLANKS, narrative: 'Generated narrative B', intended_hcvs: [], abox: null, card: CARD_BLANKS, origin: 'generated', is_editable: false, order: 1, created_by: null, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z', _variant: 'B', _pipelineRunId: 102, _generationRunId: 202 },
  { id: 4, system_description: 5, scenario_id: 'sc_01', ...CARD_BLANKS, narrative: 'Adhoc narrative', intended_hcvs: [], abox: null, card: CARD_BLANKS, origin: 'generated', is_editable: false, order: 1, created_by: null, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z', _variant: 'A', _pipelineRunId: null, _generationRunId: 203 },
  // 55 filler rows, same reasoning as SYSTEM_ROWS' own filler above - the
  // unfiltered scenarios list must also pass PAGE_SIZE=50.
  ...Array.from({ length: 55 }, (_, index) => ({
    id: 5 + index,
    system_description: 1,
    scenario_id: `filler_${String(5 + index).padStart(2, '0')}`,
    ...CARD_BLANKS,
    narrative: `Filler narrative ${5 + index}`,
    intended_hcvs: [],
    abox: null,
    card: CARD_BLANKS,
    origin: 'manual',
    is_editable: true,
    order: 1,
    created_by: 1,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    _variant: null,
    _pipelineRunId: null,
    _generationRunId: null,
  })),
];

const PHASE_RUNS = {
  201: { id: 201, pipeline_run: 101, phase: 2, variant: 'A', model: 'm', temperature: 0, prompt_template: 1, taxonomy: 1, source_phase_run: null, experiment: null, n_systems: null, n_scenarios_per_system: null, status: 'completed', error: '', progress: { done: 1, total: 1, failed: 0, percent: 100 }, total_children: 1, completed_children: 1, failed_children: 0, leak_scan_ran: true, guarded_by: [1], started_at: '2026-01-01T00:00:00Z', finished_at: '2026-01-01T00:01:00Z' },
  203: { id: 203, pipeline_run: null, phase: 2, variant: 'A', model: 'm', temperature: 0, prompt_template: 1, taxonomy: 1, source_phase_run: null, experiment: null, n_systems: 1, n_scenarios_per_system: 1, status: 'completed', error: '', progress: { done: 1, total: 1, failed: 0, percent: 100 }, total_children: 1, completed_children: 1, failed_children: 0, leak_scan_ran: true, guarded_by: [1], started_at: '2026-01-01T00:00:00Z', finished_at: '2026-01-01T00:01:00Z' },
};

let systems;
let scenarios;

function resetFixtures() {
  systems = SYSTEM_ROWS.map((row) => ({ ...row }));
  scenarios = SCENARIO_ROWS.map((row) => ({ ...row }));
}

// Strips the test-only `_variant`/`_pipelineRunId`/`_generationRunId`
// bookkeeping fields before a row goes over the wire, matching exactly what
// corpus.serializers actually expose.
function toWire(row) {
  // eslint-disable-next-line no-unused-vars
  const { _variant, _pipelineRunId, _generationRunId, ...wire } = row;
  return wire;
}

// Replicates corpus.views.{SystemDescriptionViewSet,ScenarioViewSet}
// .get_queryset's AND-combination of `?origin=&variant=&run=&phase_run=`
// (`&system=` additionally for scenarios) - not a permissive pass-through,
// so a test asserting these filters combine actually proves Corpus.jsx
// sends them all in one request.
function applyFilters(rows, url) {
  let result = rows;
  const origin = url.searchParams.get('origin');
  if (origin) result = result.filter((row) => row.origin === origin);
  const variant = url.searchParams.get('variant');
  if (variant) result = result.filter((row) => row._variant === variant);
  const run = url.searchParams.get('run');
  if (run !== null && run !== '') result = result.filter((row) => String(row._pipelineRunId) === run);
  const phaseRun = url.searchParams.get('phase_run');
  if (phaseRun !== null && phaseRun !== '') result = result.filter((row) => String(row._generationRunId) === phaseRun);
  const system = url.searchParams.get('system');
  if (system !== null && system !== '') result = result.filter((row) => String(row.system_description) === system);
  return result;
}

beforeEach(() => {
  authState.currentUserEmail = 'researcher@example.com';
  resetFixtures();

  server.use(
    // Built through the shared `paginate` (test/mockApi.js), not a
    // single-page envelope - `useSystemDescriptions`/`useScenarios` now
    // fetch every page (task 10.1), and this handler's own >50-row filler
    // fixture above is what gives that something real to prove against.
    http.get('/api/system-descriptions/', ({ request }) => {
      const url = new URL(request.url);
      return HttpResponse.json(paginate(applyFilters(systems, url).map(toWire), request));
    }),
    http.patch('/api/system-descriptions/:id/', async ({ request, params }) => {
      const csrfRefusal = requireCsrf(request);
      if (csrfRefusal) return csrfRefusal;
      const id = Number(params.id);
      const body = await request.json();
      const row = systems.find((system) => system.id === id);
      Object.assign(row, body, { updated_at: '2026-09-10T00:00:00Z' });
      return HttpResponse.json(toWire(row));
    }),
    http.get('/api/scenarios/', ({ request }) => {
      const url = new URL(request.url);
      return HttpResponse.json(paginate(applyFilters(scenarios, url).map(toWire), request));
    }),
    http.patch('/api/scenarios/:id/', async ({ request, params }) => {
      const csrfRefusal = requireCsrf(request);
      if (csrfRefusal) return csrfRefusal;
      const id = Number(params.id);
      const body = await request.json();
      const row = scenarios.find((scenario) => scenario.id === id);
      Object.assign(row, body, { updated_at: '2026-09-10T00:00:00Z' });
      return HttpResponse.json(toWire(row));
    }),
    http.get('/api/phase-runs/:id/', ({ params }) => {
      const run = PHASE_RUNS[Number(params.id)];
      if (!run) return HttpResponse.json({ detail: 'Not found.' }, { status: 404 });
      return HttpResponse.json(run);
    })
  );
});

async function setNumberField(user, field, value) {
  await user.clear(field);
  await user.type(field, String(value));
}

async function applyFilterForm(user, { origin, run, variant, phaseRun, system } = {}) {
  if (origin !== undefined) await user.selectOptions(screen.getByLabelText(/^origin$/i), origin);
  if (run !== undefined) await setNumberField(user, screen.getByLabelText(/run \(repetition id\)/i), run);
  if (variant !== undefined) await user.selectOptions(screen.getByLabelText(/^variant$/i), variant);
  if (phaseRun !== undefined) await setNumberField(user, screen.getByLabelText(/phase run id/i), phaseRun);
  if (system !== undefined) await setNumberField(user, screen.getByLabelText(/system id \(scenarios\)/i), system);
  await user.click(screen.getByRole('button', { name: /apply filters/i }));
}

describe('Corpus', () => {
  it('lists system descriptions and scenarios with origin badges', async () => {
    renderApp({ initialEntries: ['/corpus'] });

    await screen.findByText('Seed system');
    const seedRow = screen.getByText('Seed system').closest('li');
    expect(within(seedRow).getByText('Seed')).toBeInTheDocument();

    const generatedRow = screen.getByText('System A').closest('li');
    expect(within(generatedRow).getByText('Generated')).toBeInTheDocument();

    expect(screen.getByText('Manual narrative')).toBeInTheDocument();
    const scenarioRow = screen.getByText('Generated narrative A').closest('li');
    expect(within(scenarioRow).getByText('Generated')).toBeInTheDocument();
  });

  it('a generated row is read-only and the UI states why', async () => {
    renderApp({ initialEntries: ['/corpus'] });

    await screen.findByText('System A');
    const generatedRow = screen.getByText('System A').closest('li');
    expect(within(generatedRow).queryByRole('button', { name: /edit/i })).not.toBeInTheDocument();
    expect(generatedRow).toHaveTextContent(/immutable/i);
    expect(generatedRow).toHaveTextContent(/change the prompt or inputs and generate again/i);
  });

  it('a seed row is editable and saves', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/corpus'] });

    await screen.findByText('Seed system');
    const seedRow = screen.getByText('Seed system').closest('li');
    await user.click(within(seedRow).getByRole('button', { name: /edit/i }));

    const bodyField = within(seedRow).getByLabelText(/^body$/i);
    await user.clear(bodyField);
    await user.type(bodyField, 'revised body text');
    await user.click(within(seedRow).getByRole('button', { name: /^save$/i }));

    expect(await screen.findByText('revised body text')).toBeInTheDocument();
    expect(systems.find((system) => system.id === 1).body).toBe('revised body text');
  });

  it('the run filter reaches only the pipeline-run-backed row, never the ad-hoc one', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/corpus'] });

    await screen.findByText('Seed system');
    await applyFilterForm(user, { run: 101 });

    expect(await screen.findByText('System A')).toBeInTheDocument();
    expect(screen.queryByText('System adhoc')).not.toBeInTheDocument();
    expect(screen.queryByText('System B')).not.toBeInTheDocument();
    expect(screen.queryByText('Seed system')).not.toBeInTheDocument();
  });

  it('the phase-run filter reaches the ad-hoc row and labels it as scratch, never archived', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/corpus'] });

    await screen.findByText('Seed system');
    await applyFilterForm(user, { phaseRun: 203 });

    expect(await screen.findByText('System adhoc')).toBeInTheDocument();
    expect(screen.queryByText('System A')).not.toBeInTheDocument();
    expect(await screen.findByRole('status')).toHaveTextContent(/scratch/i);
    expect(screen.getByText(/never archived/i)).toBeInTheDocument();

    // A phase run that DID launch under a real repetition gets no such label.
    await applyFilterForm(user, { phaseRun: 201 });
    await screen.findByText('System A');
    expect(screen.queryByText(/scratch/i)).not.toBeInTheDocument();
  });

  it('origin and variant filters combine, narrowing further together than either alone', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/corpus'] });

    await screen.findByText('Seed system');

    await applyFilterForm(user, { origin: 'generated' });
    expect(await screen.findByText('System A')).toBeInTheDocument();
    expect(screen.getByText('System B')).toBeInTheDocument();
    expect(screen.getByText('System adhoc')).toBeInTheDocument();
    expect(screen.queryByText('Seed system')).not.toBeInTheDocument();
    expect(screen.queryByText('Manual system')).not.toBeInTheDocument();

    // Adding variant=A on top must narrow further, not replace the origin
    // filter - System B (variant B) must now disappear too.
    await user.selectOptions(screen.getByLabelText(/^variant$/i), 'A');
    await user.click(screen.getByRole('button', { name: /apply filters/i }));

    expect(await screen.findByText('System A')).toBeInTheDocument();
    expect(screen.getByText('System adhoc')).toBeInTheDocument();
    expect(screen.queryByText('System B')).not.toBeInTheDocument();
  });

  it('the scenario system filter combines with variant', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/corpus'] });

    await screen.findByText('Seed system');
    await applyFilterForm(user, { system: 3, variant: 'A' });

    expect(await screen.findByText('Generated narrative A')).toBeInTheDocument();
    expect(screen.queryByText('Generated narrative B')).not.toBeInTheDocument();
    expect(screen.queryByText('Manual narrative')).not.toBeInTheDocument();
    expect(screen.queryByText('Adhoc narrative')).not.toBeInTheDocument();

    // Same system, wrong variant - must exclude it, proving `variant`
    // narrows rather than being ignored once `system` is also present.
    await user.selectOptions(screen.getByLabelText(/^variant$/i), 'B');
    await user.click(screen.getByRole('button', { name: /apply filters/i }));

    expect(screen.queryByText('Generated narrative A')).not.toBeInTheDocument();
    expect(screen.queryByText('Generated narrative B')).not.toBeInTheDocument();
  });

  it('fetches every page of the unfiltered corpus, not just the first 50 (fails if only page 1 is read)', async () => {
    renderApp({ initialEntries: ['/corpus'] });

    // 60 system-description rows and 59 scenario rows total - one page (50)
    // could never show either "last" row below.
    await screen.findByText('Seed system');
    expect(await screen.findByText('Filler system 60')).toBeInTheDocument();
    expect(await screen.findByText('Filler narrative 59')).toBeInTheDocument();
  });
});
