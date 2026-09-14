import { beforeEach, describe, expect, it } from 'vitest';
import { configure, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { authState, currentUser, server } from '../../test/server';
import { renderApp } from '../../test/renderApp';
import { paginate, paginated, requireCsrf, requireStaff } from '../../test/mockApi';
import { pickOption } from '../../test/pickOption';

// The new "System (scenarios)" select renders every system's TITLE as an
// <option> - the same text a system's own row heading shows. Left at the
// default ignore list ('script, style'), an unscoped `getByText`/`findByText`
// for a row's title becomes ambiguous the moment that title's <option> is
// also on the page. Excluding 'option' here (file-scoped, not setup.js)
// restores every existing unscoped text query without touching each one.
configure({ defaultIgnore: 'script, style, option' });

// Mirrors backend/corpus/tests/test_corpus_filters.py's own `corpus_runs`
// fixture shape: two repetitions (pipeline runs 101/102, behind phase runs
// 201/202) plus one ad-hoc iterate-mode phase run (203, pipeline_run=null -
// what makes it scratch). `_variant`/`_pipelineRunId` are TEST-ONLY
// bookkeeping used by the fake filtering below to stand in for
// `?variant=`/`?run=`, which the real API resolves server-side through
// `phase_run__variant`/`generation_run__pipeline_run_id` - corpus.
// serializers.SystemDescriptionSerializer/ScenarioSerializer never put
// `variant`/`pipeline_run` on the wire (verified by reading them), so those
// two stay stripped by `toWire` below.
//
// `generation_run`/`is_scratch`, by contrast, are now REAL wire fields
// (task 10.5, corpus/serializers.py) - the PhaseRun that produced a
// generated row, and whether that PhaseRun has no `pipeline_run` of its
// own. Before this task, corpus rows carried neither, and the ONLY way this
// screen could tell scratch material apart from a real run's output was
// `?phase_run=` plus a follow-up lookup of the PhaseRun itself
// (`usePhaseRun`) - unfiltered Corpus, or any OTHER filter, showed scratch
// as an ordinary generated row. Every row below sets both explicitly,
// exactly as the real serializer would derive them, so a test can prove the
// screen reads them directly rather than only through that one filter.
const SYSTEM_ROWS = [
  { id: 1, title: 'Seed system', slug: 'seed-system', body: 'original seed body', origin: 'seed', generation_run: null, is_scratch: false, is_editable: true, created_by: 1, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z', _variant: null, _pipelineRunId: null },
  { id: 2, title: 'Manual system', slug: 'manual-system', body: 'typed in by hand', origin: 'manual', generation_run: null, is_scratch: false, is_editable: true, created_by: 1, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z', _variant: null, _pipelineRunId: null },
  { id: 3, title: 'System A', slug: 'system-a', body: 'model output A', origin: 'generated', generation_run: 201, is_scratch: false, is_editable: false, created_by: null, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z', _variant: 'A', _pipelineRunId: 101 },
  { id: 4, title: 'System B', slug: 'system-b', body: 'model output B', origin: 'generated', generation_run: 202, is_scratch: false, is_editable: false, created_by: null, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z', _variant: 'B', _pipelineRunId: 102 },
  { id: 5, title: 'System adhoc', slug: 'system-adhoc', body: 'tuning output', origin: 'generated', generation_run: 203, is_scratch: true, is_editable: false, created_by: null, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z', _variant: 'A', _pipelineRunId: null },
  // 55 filler rows (ids 6-60) so the UNFILTERED system-descriptions list
  // passes PAGE_SIZE=50 (task 10.1: Corpus must show the complete corpus,
  // not a first page of it). `_pipelineRunId: null`/`generation_run: null`
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
    generation_run: null,
    is_scratch: false,
    is_editable: true,
    created_by: 1,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    _variant: null,
    _pipelineRunId: null,
  })),
];

const CARD_BLANKS = { actor: '', trigger: '', standing_state: '', asset: '', threat: '', outcome: '' };

const SCENARIO_ROWS = [
  { id: 1, system_description: 1, scenario_id: 'sc_01', ...CARD_BLANKS, narrative: 'Manual narrative', intended_hcvs: [], abox: null, card: CARD_BLANKS, origin: 'manual', generation_run: null, is_scratch: false, is_editable: true, order: 1, created_by: 1, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z', _variant: null, _pipelineRunId: null },
  { id: 2, system_description: 3, scenario_id: 'sc_01', ...CARD_BLANKS, narrative: 'Generated narrative A', intended_hcvs: [], abox: null, card: CARD_BLANKS, origin: 'generated', generation_run: 201, is_scratch: false, is_editable: false, order: 1, created_by: null, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z', _variant: 'A', _pipelineRunId: 101 },
  { id: 3, system_description: 4, scenario_id: 'sc_01', ...CARD_BLANKS, narrative: 'Generated narrative B', intended_hcvs: [], abox: null, card: CARD_BLANKS, origin: 'generated', generation_run: 202, is_scratch: false, is_editable: false, order: 1, created_by: null, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z', _variant: 'B', _pipelineRunId: 102 },
  { id: 4, system_description: 5, scenario_id: 'sc_01', ...CARD_BLANKS, narrative: 'Adhoc narrative', intended_hcvs: [], abox: null, card: CARD_BLANKS, origin: 'generated', generation_run: 203, is_scratch: true, is_editable: false, order: 1, created_by: null, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z', _variant: 'A', _pipelineRunId: null },
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
    generation_run: null,
    is_scratch: false,
    is_editable: true,
    order: 1,
    created_by: 1,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    _variant: null,
    _pipelineRunId: null,
  })),
];

const PHASE_RUNS_SEED = {
  201: { id: 201, pipeline_run: 101, phase: 2, variant: 'A', model: 'm', temperature: 0, prompt_template: 1, taxonomy: 1, source_phase_run: null, experiment: null, n_systems: null, n_scenarios_per_system: null, status: 'completed', error: '', progress: { done: 1, total: 1, failed: 0, percent: 100 }, total_children: 1, completed_children: 1, failed_children: 0, leak_scan_ran: true, guarded_by: [1], started_at: '2026-01-01T00:00:00Z', finished_at: '2026-01-01T00:01:00Z' },
  203: { id: 203, pipeline_run: null, phase: 2, variant: 'A', model: 'm', temperature: 0, prompt_template: 1, taxonomy: 1, source_phase_run: null, experiment: null, n_systems: 1, n_scenarios_per_system: 1, status: 'completed', error: '', progress: { done: 1, total: 1, failed: 0, percent: 100 }, total_children: 1, completed_children: 1, failed_children: 0, leak_scan_ran: true, guarded_by: [1], started_at: '2026-01-01T00:00:00Z', finished_at: '2026-01-01T00:01:00Z' },
  // An ad-hoc run still QUEUED - the DELETE handler below must refuse this
  // one with the real API's own 409 shape (`code: "run_in_flight"`,
  // pipeline/views.py::PhaseRunViewSet.perform_destroy), not silently
  // succeed.
  204: { id: 204, pipeline_run: null, phase: 2, variant: 'A', model: 'm', temperature: 0, prompt_template: 1, taxonomy: 1, source_phase_run: null, experiment: null, n_systems: 1, n_scenarios_per_system: 1, status: 'queued', error: '', progress: { done: 0, total: 1, failed: 0, percent: 0 }, total_children: 1, completed_children: 0, failed_children: 0, leak_scan_ran: false, guarded_by: [], started_at: null, finished_at: null },
};

// Feeds the new "Experiment run"/"Generation run" selects (live-filters
// round) - Corpus.jsx now reads usePipelineRuns/useExperiments/
// usePhaseRuns({phase:2})/usePromptTemplates({phase:2}) to label those
// options, so this suite errors on an unhandled request without them.
const PIPELINE_RUNS = [
  { id: 101, experiment: 7, repetition_index: 1, status: 'completed', config: {}, progress: { done: 1, total: 1, failed: 0, percent: 100 }, started_at: '2026-01-01T00:00:00Z', finished_at: '2026-01-01T00:05:00Z' },
  { id: 102, experiment: 7, repetition_index: 2, status: 'completed', config: {}, progress: { done: 1, total: 1, failed: 0, percent: 100 }, started_at: '2026-01-02T00:00:00Z', finished_at: '2026-01-02T00:05:00Z' },
];
const EXPERIMENTS = [{ id: 7, name: 'Oldsmar pilot' }];
const PROMPTS = [
  { id: 1, phase: 2, name: 'default-generation-variant-a', version: 1, variant: 'A', system_prompt: '', user_prompt: '', output_schema: {}, is_active: true, created_by: null, created_at: '2026-01-01T00:00:00Z' },
];

let systems;
let scenarios;
let phaseRuns;

function resetFixtures() {
  systems = SYSTEM_ROWS.map((row) => ({ ...row }));
  scenarios = SCENARIO_ROWS.map((row) => ({ ...row }));
  phaseRuns = Object.fromEntries(
    Object.entries(PHASE_RUNS_SEED).map(([id, run]) => [id, { ...run }])
  );
}

// Strips the test-only `_variant`/`_pipelineRunId` bookkeeping fields
// before a row goes over the wire, matching exactly what corpus.serializers
// actually expose - `generation_run`/`is_scratch` are REAL fields now
// (task 10.5) and stay on the wire, unlike those two.
function toWire(row) {
  // eslint-disable-next-line no-unused-vars
  const { _variant, _pipelineRunId, ...wire } = row;
  return wire;
}

// Replicates corpus.views.{SystemDescriptionViewSet,ScenarioViewSet}
// .get_queryset's AND-combination of `?origin=&variant=&run=&phase_run=`
// (`&system=` additionally for scenarios) - not a permissive pass-through,
// so a test asserting these filters combine actually proves Corpus.jsx
// sends them all in one request. `?phase_run=` now matches on the row's own
// (real) `generation_run` field, exactly as the backend's `generation_run=`
// filter would, rather than a test-only shadow of it.
function applyFilters(rows, url) {
  let result = rows;
  const origin = url.searchParams.get('origin');
  if (origin) result = result.filter((row) => row.origin === origin);
  const variant = url.searchParams.get('variant');
  if (variant) result = result.filter((row) => row._variant === variant);
  const run = url.searchParams.get('run');
  if (run !== null && run !== '') result = result.filter((row) => String(row._pipelineRunId) === run);
  const phaseRun = url.searchParams.get('phase_run');
  if (phaseRun !== null && phaseRun !== '') result = result.filter((row) => String(row.generation_run) === phaseRun);
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
      const run = phaseRuns[Number(params.id)];
      if (!run) return HttpResponse.json({ detail: 'Not found.' }, { status: 404 });
      return HttpResponse.json(run);
    }),
    // Backs the "Generation run" select - every phase-2 run, live off the
    // same mutable `phaseRuns` fixture the detail/delete handlers use.
    http.get('/api/phase-runs/', ({ request }) => {
      const url = new URL(request.url);
      const phase = url.searchParams.get('phase');
      const rows = Object.values(phaseRuns).filter((run) => (phase ? String(run.phase) === phase : true));
      return HttpResponse.json(paginate(rows, request));
    }),
    // Backs the "Experiment run" select.
    http.get('/api/pipeline-runs/', ({ request }) => HttpResponse.json(paginate(PIPELINE_RUNS, request))),
    http.get('/api/experiments/', ({ request }) => HttpResponse.json(paginate(EXPERIMENTS, request))),
    http.get('/api/prompts/', ({ request }) => {
      const url = new URL(request.url);
      const phase = url.searchParams.get('phase');
      const rows = PROMPTS.filter((prompt) => (phase ? String(prompt.phase) === phase : true));
      return HttpResponse.json(paginate(rows, request));
    }),
    // Staff-only (pipeline/views.py::PhaseRunViewSet's StaffWrites) - task
    // 10.5's "allow deleting an iterate run". Refuses with the real 409
    // shape while QUEUED/RUNNING (`code: "run_in_flight"`), and otherwise
    // cascades to every system/scenario this run generated, matching
    // `generation_run, on_delete=CASCADE` (corpus.models).
    http.delete('/api/phase-runs/:id/', async ({ request, params }) => {
      const staffRefusal = requireStaff(currentUser());
      if (staffRefusal) return staffRefusal;
      const csrfRefusal = requireCsrf(request);
      if (csrfRefusal) return csrfRefusal;
      const id = Number(params.id);
      const run = phaseRuns[id];
      if (!run) return HttpResponse.json({ detail: 'Not found.' }, { status: 404 });
      if (run.status === 'queued' || run.status === 'running') {
        return HttpResponse.json(
          { detail: `Phase run ${id} is ${run.status} and cannot be deleted while a worker may still hold it.`, code: 'run_in_flight' },
          { status: 409 }
        );
      }
      delete phaseRuns[id];
      systems = systems.filter((system) => system.generation_run !== id);
      scenarios = scenarios.filter((scenario) => scenario.generation_run !== id);
      return new HttpResponse(null, { status: 204 });
    })
  );
});

// Every control below applies on the fly (no Apply button any more) - each
// select is driven by its option VALUE (a numeric id), never its visible
// label text, so these calls stay agnostic to the exact label wording. The
// run/generation-run/system fields are now SearchableSelect comboboxes
// (live-filters round) - `pickOption` opens one, waits for its option to
// actually be there (their lists populate from their own queries), then
// clicks it, rather than racing the fetch.
async function applyFilterForm(user, { origin, run, variant, phaseRun, system } = {}) {
  if (origin !== undefined) await user.selectOptions(screen.getByLabelText(/^origin$/i), origin);
  if (run !== undefined) await pickOption(user, /experiment run/i, String(run));
  if (variant !== undefined) await user.selectOptions(screen.getByLabelText(/^variant$/i), variant);
  if (phaseRun !== undefined) await pickOption(user, /generation run/i, String(phaseRun));
  if (system !== undefined) await pickOption(user, /^system \(scenarios\)$/i, String(system));
}

// Generated rows (and the Run/Variant filters, which only ever apply to
// them) are hidden until this is ticked - unchecked by default (live-
// filters round, user feedback: "it makes sense only to show seed and
// manual; the generated ones are accessible from their runs").
async function includeGenerated(user) {
  await user.click(await screen.findByLabelText(/include generated systems and scenarios/i));
}

describe('Corpus', () => {
  // Corpus was merged into the Resources page as its "Systems & scenarios"
  // tab (task 11) - the old `/corpus` route now just forwards to
  // `/resources?tab=corpus` (App.jsx) so any existing bookmark/link keeps
  // working. Every other test below renders the tab's URL directly; this
  // one is the one place that proves the redirect itself is wired.
  it('redirects the old /corpus route into the Resources page\'s "Systems & scenarios" tab', async () => {
    renderApp({ initialEntries: ['/corpus'] });

    expect(await screen.findByRole('heading', { name: /^resources$/i })).toBeInTheDocument();
    expect(await screen.findByText('Seed system')).toBeInTheDocument();
  });

  // User feedback: "it makes sense only to show seed and manual; the
  // generated ones are accessible from their runs. However, you can have a
  // check (false by default) to include generated ones too."
  it('hides generated rows and their filters by default; the checkbox reveals both', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/resources?tab=corpus'] });

    await screen.findByText('Seed system');
    expect(screen.getByText('Manual system')).toBeInTheDocument();
    expect(screen.queryByText('System A')).not.toBeInTheDocument();
    expect(screen.queryByText('System B')).not.toBeInTheDocument();
    expect(screen.queryByText('System adhoc')).not.toBeInTheDocument();
    // Counts in the headings match what is actually shown - 2 essential
    // seed/manual rows + 55 manual filler rows, never the 3 generated ones.
    const systemsHeading = screen.getByRole('heading', { name: /^system descriptions/i });
    expect(systemsHeading).toHaveTextContent('(57)');

    const checkbox = screen.getByLabelText(/include generated systems and scenarios/i);
    expect(checkbox).not.toBeChecked();
    // Only applicable-to-generated-rows controls are gone: the Run group
    // and Variant, never Origin/Search/System (which still apply either way).
    expect(screen.queryByLabelText(/experiment run/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/generation run/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/^variant$/i)).not.toBeInTheDocument();
    expect(screen.getByLabelText(/^system \(scenarios\)$/i)).toBeInTheDocument();
    // The Origin select itself only offers All/Seed/Manual while unchecked
    // (`option` text stays off-limits to `getByText` in this file - see
    // this file's own `configure` above - so its options are read by role).
    const origin = screen.getByLabelText(/^origin$/i);
    expect(within(origin).queryByRole('option', { name: /generated/i })).not.toBeInTheDocument();

    await user.click(checkbox);
    expect(await screen.findByText('System A')).toBeInTheDocument();
    expect(screen.getByText('System B')).toBeInTheDocument();
    expect(screen.getByText('System adhoc')).toBeInTheDocument();
    expect(systemsHeading).toHaveTextContent('(60)');
    expect(await screen.findByLabelText(/experiment run/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/generation run/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/^variant$/i)).toBeInTheDocument();
    expect(within(origin).getByRole('option', { name: /generated — all/i })).toBeInTheDocument();

    // Clear resets the checkbox back off, hiding generated rows again.
    await user.click(screen.getByRole('button', { name: /^clear$/i }));
    expect(checkbox).not.toBeChecked();
    await waitFor(() => expect(screen.queryByText('System A')).not.toBeInTheDocument());
  });

  it('lists system descriptions and scenarios with origin badges', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/resources?tab=corpus'] });

    await screen.findByText('Seed system');
    const seedRow = screen.getByText('Seed system').closest('li');
    expect(within(seedRow).getByText('Seed')).toBeInTheDocument();

    await includeGenerated(user);
    const generatedRow = await screen.findByText('System A');
    expect(within(generatedRow.closest('li')).getByText('Generated')).toBeInTheDocument();

    expect(screen.getByText('Manual narrative')).toBeInTheDocument();
    const scenarioRow = screen.getByText('Generated narrative A').closest('li');
    expect(within(scenarioRow).getByText('Generated')).toBeInTheDocument();
  });

  it('a generated row is read-only and the UI states why', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/resources?tab=corpus'] });

    await screen.findByText('Seed system');
    await includeGenerated(user);
    await screen.findByText('System A');
    const generatedRow = screen.getByText('System A').closest('li');
    expect(within(generatedRow).queryByRole('button', { name: /edit/i })).not.toBeInTheDocument();
    expect(generatedRow).toHaveTextContent(/can’t be edited/i);
    expect(generatedRow).toHaveTextContent(/change the prompt or inputs and generate again/i);
  });

  it('a seed row is editable and saves', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/resources?tab=corpus'] });

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
    renderApp({ initialEntries: ['/resources?tab=corpus'] });

    await screen.findByText('Seed system');
    await includeGenerated(user);
    await applyFilterForm(user, { run: 101 });

    expect(await screen.findByText('System A')).toBeInTheDocument();
    expect(screen.queryByText('System adhoc')).not.toBeInTheDocument();
    expect(screen.queryByText('System B')).not.toBeInTheDocument();
    expect(screen.queryByText('Seed system')).not.toBeInTheDocument();
  });

  it('the phase-run filter reaches the ad-hoc row and labels it as scratch, never archived', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/resources?tab=corpus'] });

    await screen.findByText('Seed system');
    await includeGenerated(user);
    await applyFilterForm(user, { phaseRun: 203 });

    expect(await screen.findByText('System adhoc')).toBeInTheDocument();
    expect(screen.queryByText('System A')).not.toBeInTheDocument();
    // `role="status"` uniquely identifies the top banner here (no
    // EmptyState renders alongside it, since #203 DOES have matching
    // corpus rows) - scoped within it since task 10.5's own per-row
    // scratch note (SCRATCH_READONLY_NOTE) now ALSO says "never archived"
    // on every scratch row, which would otherwise make an unscoped
    // `getByText(/never archived/i)` ambiguous.
    const banner = await screen.findByRole('status');
    expect(banner).toHaveTextContent(/prompt test/i);
    expect(banner).toHaveTextContent(/never included in exports/i);

    // A phase run that DID launch under a real repetition gets no such label.
    await applyFilterForm(user, { phaseRun: 201 });
    await screen.findByText('System A');
    expect(screen.queryByText(/prompt test/i, { ignore: 'option, script, style' })).not.toBeInTheDocument();
  });

  // Task 10.5's own confirmed gap: before this, a scratch row read as an
  // ordinary generated row everywhere EXCEPT under `?phase_run=`. Corpus
  // rows now carry `generation_run`/`is_scratch` directly (corpus/
  // serializers.py), so this must hold with NO phase-run filter applied at
  // all - the exact case the old implementation could not catch.
  it('marks scratch rows with a per-row badge wherever they appear, not only under the phase-run filter', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/resources?tab=corpus'] });

    await screen.findByText('Seed system');
    await includeGenerated(user);
    await screen.findByText('System adhoc');
    const adhocRow = screen.getByText('System adhoc').closest('li');
    expect(within(adhocRow).getByText(/^test run$/i)).toBeInTheDocument();
    expect(adhocRow).toHaveTextContent(/made by a prompt test/i);

    // A real run's own generated row must never read as scratch.
    const systemARow = screen.getByText('System A').closest('li');
    expect(within(systemARow).queryByText(/^test run$/i)).not.toBeInTheDocument();
    const seedRow = screen.getByText('Seed system').closest('li');
    expect(within(seedRow).queryByText(/^test run$/i)).not.toBeInTheDocument();

    // Same holds for scenarios.
    await screen.findByText('Adhoc narrative');
    const adhocScenarioRow = screen.getByText('Adhoc narrative').closest('li');
    expect(within(adhocScenarioRow).getByText(/^test run$/i)).toBeInTheDocument();
    const scenarioARow = screen.getByText('Generated narrative A').closest('li');
    expect(within(scenarioARow).queryByText(/^test run$/i)).not.toBeInTheDocument();
  });

  it('a staff user can delete an iterate run from the phase-run filtered view, which removes its generated material', async () => {
    authState.currentUserEmail = 'admin@example.com'; // staff - see test/server.js SEED_USERS
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/resources?tab=corpus'] });

    await screen.findByText('Seed system');
    await includeGenerated(user);
    await applyFilterForm(user, { phaseRun: 203 });
    await screen.findByText('System adhoc');

    await user.click(screen.getByRole('button', { name: /delete this run/i }));
    const dialog = screen.getByRole('alertdialog');
    expect(dialog).toHaveTextContent(/goes with it/i);
    await user.click(within(dialog).getByRole('button', { name: /^confirm$/i }));

    await waitFor(() => expect(screen.queryByText('System adhoc')).not.toBeInTheDocument());
    expect(phaseRuns[203]).toBeUndefined();
    expect(systems.some((system) => system.id === 5)).toBe(false);
    expect(scenarios.some((scenario) => scenario.id === 4)).toBe(false);
  });

  it('a 409 while the run is still in flight shows the API\'s own message, and deletes nothing', async () => {
    authState.currentUserEmail = 'admin@example.com';
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/resources?tab=corpus'] });

    await screen.findByText('Seed system');
    await includeGenerated(user);
    await applyFilterForm(user, { phaseRun: 204 }); // still queued - see PHASE_RUNS_SEED
    // Not `findByRole('status')` - no corpus row is generated by #204, so
    // both "no X match" EmptyStates (also `role="status"`) render alongside
    // this banner, making that query ambiguous.
    await screen.findByText(/this is the output of a prompt test/i);

    await user.click(screen.getByRole('button', { name: /delete this run/i }));
    const dialog = screen.getByRole('alertdialog');
    await user.click(within(dialog).getByRole('button', { name: /^confirm$/i }));

    expect(await screen.findByText(/cannot be deleted while a worker may still hold it/i)).toBeInTheDocument();
    expect(phaseRuns[204]).toBeDefined();
  });

  it('a non-staff user viewing scratch material has no delete action available', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/resources?tab=corpus'] }); // researcher@example.com - not staff, see beforeEach

    await screen.findByText('Seed system');
    await includeGenerated(user);
    await applyFilterForm(user, { phaseRun: 203 });
    await screen.findByText('System adhoc');

    expect(screen.queryByRole('button', { name: /delete this run/i })).not.toBeInTheDocument();
  });

  it('origin and variant filters combine, narrowing further together than either alone', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/resources?tab=corpus'] });

    await screen.findByText('Seed system');
    await includeGenerated(user);

    await applyFilterForm(user, { origin: 'generated' });
    expect(await screen.findByText('System A')).toBeInTheDocument();
    expect(screen.getByText('System B')).toBeInTheDocument();
    expect(screen.getByText('System adhoc')).toBeInTheDocument();
    expect(screen.queryByText('Seed system')).not.toBeInTheDocument();
    expect(screen.queryByText('Manual system')).not.toBeInTheDocument();

    // Adding variant=A on top must narrow further, not replace the origin
    // filter - System B (variant B) must now disappear too.
    await user.selectOptions(screen.getByLabelText(/^variant$/i), 'A');

    expect(await screen.findByText('System A')).toBeInTheDocument();
    expect(screen.getByText('System adhoc')).toBeInTheDocument();
    expect(screen.queryByText('System B')).not.toBeInTheDocument();
  });

  it('the scenario system filter combines with variant', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/resources?tab=corpus'] });

    await screen.findByText('Seed system');
    await includeGenerated(user);
    await applyFilterForm(user, { system: 3, variant: 'A' });

    expect(await screen.findByText('Generated narrative A')).toBeInTheDocument();
    expect(screen.queryByText('Generated narrative B')).not.toBeInTheDocument();
    expect(screen.queryByText('Manual narrative')).not.toBeInTheDocument();
    expect(screen.queryByText('Adhoc narrative')).not.toBeInTheDocument();

    // Same system, wrong variant - must exclude it, proving `variant`
    // narrows rather than being ignored once `system` is also present.
    await user.selectOptions(screen.getByLabelText(/^variant$/i), 'B');

    await waitFor(() => expect(screen.queryByText('Generated narrative A')).not.toBeInTheDocument());
    expect(screen.queryByText('Generated narrative B')).not.toBeInTheDocument();
  });

  it('applies a filter selection immediately, with no Apply button anywhere on the page', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/resources?tab=corpus'] });

    await screen.findByText('Seed system');
    expect(screen.queryByRole('button', { name: /^apply$/i })).not.toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText(/^origin$/i), 'seed');

    expect(await screen.findByText('Seed system')).toBeInTheDocument();
    expect(screen.queryByText('Manual system')).not.toBeInTheDocument();
  });

  it('the search box narrows results after a short pause, with no Apply needed', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/resources?tab=corpus'] });

    await screen.findByText('Seed system');
    await user.type(screen.getByLabelText(/^search$/i), 'typed in by hand');

    await waitFor(() => expect(screen.queryByText('Seed system')).not.toBeInTheDocument());
    expect(screen.getByText('Manual system')).toBeInTheDocument();
  });

  it('fetches every page of the unfiltered corpus, not just the first 50 (fails if only page 1 is read)', async () => {
    renderApp({ initialEntries: ['/resources?tab=corpus'] });

    // 60 system-description rows and 59 scenario rows total - one page (50)
    // could never show either "last" row below.
    await screen.findByText('Seed system');
    expect(await screen.findByText('Filler system 60')).toBeInTheDocument();
    expect(await screen.findByText('Filler narrative 59')).toBeInTheDocument();
  });
});


describe('Generated rows', () => {
  it('open "How was this generated?" with the run, prompt, model and the exact prompt sent', async () => {
    const user = userEvent.setup();
    authState.currentUserEmail = 'admin@example.com';
    server.use(
      http.get('/api/system-descriptions/', () =>
        HttpResponse.json(
          paginated([
            { id: 900, title: 'Generated grid', slug: 'generated-grid', body: 'Body.', origin: 'generated', generation_run: 77, is_scratch: false, is_editable: false, created_by: null, created_at: '2026-09-10T00:00:00Z', updated_at: '2026-09-10T00:00:00Z' },
          ])
        )
      ),
      http.get('/api/scenarios/', () => HttpResponse.json(paginated([]))),
      http.get('/api/phase-runs/77/', () =>
        HttpResponse.json({ id: 77, pipeline_run: 501, phase: 2, variant: 'B', model: 'google/gemini-2.5-flash', temperature: 1, prompt_template: 12, taxonomy: 2, source_phase_run: null, experiment: null, n_systems: null, n_scenarios_per_system: null, status: 'completed', error: '', progress: { done: 1, total: 1, failed: 0, percent: 100 }, total_children: 1, completed_children: 1, failed_children: 0, leak_scan_ran: false, guarded_by: [], started_at: null, finished_at: '2026-09-10T00:05:00Z' })
      ),
      http.get('/api/prompts/12/', () =>
        HttpResponse.json({ id: 12, phase: 2, name: 'default-generation-variant-b', version: 3, variant: 'B', system_prompt: '', user_prompt: 'x', output_schema: {}, is_active: true, created_by: null, created_at: '2026-09-10T00:00:00Z' })
      ),
      http.get('/api/pipeline-runs/501/', () =>
        HttpResponse.json({ id: 501, experiment: 4, repetition_index: 2, status: 'completed', config: { phases: [2, 3], seed_systems: [{ id: 1, title: 'Oldsmar' }], fewshots: [{ id: 3, name: 'oldsmar' }], n_systems: 1, n_scenarios_per_system: 3 }, progress: { done: 1, total: 1, failed: 0, percent: 100 }, started_at: null, finished_at: null })
      ),
      http.get('/api/experiments/4/', () => HttpResponse.json({ id: 4, name: 'Pilot' })),
      http.get('/api/phase-runs/77/calls/', () =>
        HttpResponse.json(paginated([{ id: 1, sequence: 1, rendered_system_prompt: 'SYS', rendered_user_prompt: 'THE EXACT USER PROMPT', raw_response: '{}', prompt_tokens: 1, completion_tokens: 2, cost: 0, finish_reason: 'stop' }]))
      )
    );
    renderApp({ initialEntries: ['/resources?tab=corpus'] });

    await includeGenerated(user);
    await user.click(await screen.findByRole('button', { name: /how was this generated/i }));
    const dialog = await screen.findByRole('dialog');
    expect(await within(dialog).findByRole('link', { name: 'default-generation-variant-b v3' })).toHaveAttribute('href', '/prompts/12');
    expect(await within(dialog).findByRole('link', { name: /repetition 2/i })).toHaveAttribute('href', '/pipeline-runs/501');
    expect(within(dialog).getByText(/google\/gemini-2.5-flash/)).toBeInTheDocument();
    expect(within(dialog).getByText('1 × 3')).toBeInTheDocument();
    expect(within(dialog).getByText('Oldsmar')).toBeInTheDocument();
    await user.click(await within(dialog).findByRole('button', { name: /exact prompt sent/i }));
    expect(within(dialog).getByText('THE EXACT USER PROMPT')).toBeInTheDocument();
  });
});


describe('Scratch rows', () => {
  it('can be listed on their own and deleted by deleting their test run', async () => {
    const user = userEvent.setup();
    authState.currentUserEmail = 'admin@example.com';
    let deletedRun = null;
    const systems = [
      { id: 901, title: 'Scratch grid', slug: 's', body: '', origin: 'generated', generation_run: 35, is_scratch: true, is_editable: false, created_by: null, created_at: '2026-09-10T00:00:00Z', updated_at: '2026-09-10T00:00:00Z' },
      { id: 902, title: 'Experiment grid', slug: 'e', body: '', origin: 'generated', generation_run: 36, is_scratch: false, is_editable: false, created_by: null, created_at: '2026-09-10T00:00:00Z', updated_at: '2026-09-10T00:00:00Z' },
    ];
    server.use(
      http.get('/api/system-descriptions/', () => HttpResponse.json(paginated(systems))),
      http.get('/api/scenarios/', () => HttpResponse.json(paginated([]))),
      http.delete('/api/phase-runs/:id/', ({ params, request }) => {
        const refusal = requireCsrf(request);
        if (refusal) return refusal;
        deletedRun = Number(params.id);
        return new HttpResponse(null, { status: 204 });
      })
    );
    renderApp({ initialEntries: ['/resources?tab=corpus'] });

    await includeGenerated(user);
    expect(await screen.findByText('Experiment grid')).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText(/^origin$/i), 'generated-scratch');
    await waitFor(() => expect(screen.queryByText('Experiment grid')).not.toBeInTheDocument());
    expect(screen.getByText('Scratch grid')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /^delete$/i }));
    await user.click(await screen.findByRole('button', { name: /delete test run/i }));
    await waitFor(() => expect(deletedRun).toBe(35));
  });
});
