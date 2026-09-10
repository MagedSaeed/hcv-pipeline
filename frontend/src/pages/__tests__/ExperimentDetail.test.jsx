import { beforeEach, describe, expect, it } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { delay, http, HttpResponse } from 'msw';
import { authState, currentUser, server } from '../../test/server';
import { renderApp } from '../../test/renderApp';
import { paginated, requireCsrf, requireStaff } from '../../test/mockApi';
import { apiClient } from '../../api/client';

// Task 10.3: this screen is the one place an already-created experiment
// can always be reopened, its (current, live) configuration read, launched,
// and edited - the launch form itself (repetitions field, OPTIONS help
// text, the queued-repetitions result, the missing-key 503 mapping) moved
// here wholesale from ExperimentBuilder.jsx's own old inline "Launch"
// section, which only ever existed for the one render right after create.

const TEMPLATES = [
  { id: 1, phase: 2, name: 'gen-a', version: 1, system_prompt: '', user_prompt: 'A', output_schema: {}, is_active: true, created_by: 3, created_at: '2026-01-01T00:00:00Z' },
  { id: 2, phase: 2, name: 'gen-b', version: 1, system_prompt: '', user_prompt: 'B {{ taxonomy }}', output_schema: {}, is_active: true, created_by: 3, created_at: '2026-01-01T00:00:00Z' },
  { id: 3, phase: 3, name: 'label', version: 1, system_prompt: '', user_prompt: 'L', output_schema: {}, is_active: true, created_by: 3, created_at: '2026-01-01T00:00:00Z' },
];

const SYSTEMS = [
  { id: 10, title: 'Oldsmar water plant', slug: 'oldsmar', body: 'A plant.', origin: 'seed', is_editable: true, created_by: 1, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' },
];

const FEWSHOTS = [
  { id: 20, phase: 2, name: 'shot-1', input_text: 'in', input_file: null, output_text: 'out', output_file: null, resolved_input: 'in', resolved_output: 'out', order: 1, is_active: true },
];

const EXPERIMENT = {
  id: 77,
  name: 'Baseline comparison',
  description: 'Variant A vs B.',
  taxonomy: 2,
  phase2_model: 'anthropic/claude-sonnet-4',
  phase2_temperature: 1,
  phase2_prompt_a: 1,
  phase2_prompt_b: 2,
  phase3_model: 'openai/gpt-4o-mini',
  phase3_temperature: 0,
  phase3_prompt: 3,
  seed_systems: [10],
  fewshots: [20],
  generate_systems: true,
  generate_scenarios: true,
  target_system: null,
  n_systems: 3,
  n_scenarios_per_system: 5,
  variants: ['A', 'B'],
  progress: { done: 0, total: 0, failed: 0, percent: 0 },
  run_count: 0,
  created_by: 3,
  created_at: '2026-09-10T00:00:00Z',
};

function runOptionsResponse(helpText) {
  return {
    name: 'Experiment Run',
    description: '',
    renders: ['application/json'],
    parses: ['application/json'],
    actions: {
      POST: {
        n_repetitions: {
          type: 'integer',
          required: false,
          read_only: false,
          label: 'N repetitions',
          help_text: helpText,
          min_value: 1,
          max_value: 100,
        },
      },
    },
  };
}

const DEFAULT_HELP_TEXT = 'Default fixture text for n_repetitions, unrelated to the real backend wording.';

// `POST /api/experiments/{id}/run/` is staff-only (spec §9); every test
// installs its own resolver (the response differs per test), so the guard
// wraps each one - see ExperimentBuilder.test.jsx's identical
// `guardedExperimentWrite` for why a standalone layered handler would never
// actually run here.
function guardedRunWrite(resolver) {
  return async (info) => {
    const staffRefusal = requireStaff(currentUser());
    if (staffRefusal) return staffRefusal;
    const csrfRefusal = requireCsrf(info.request);
    if (csrfRefusal) return csrfRefusal;
    return resolver(info);
  };
}

function installDetailFixtures(experiment = EXPERIMENT) {
  server.use(
    http.get('/api/experiments/77/', () => HttpResponse.json(experiment)),
    http.get('/api/pipeline-runs/', () => HttpResponse.json(paginated([]))),
    http.get('/api/prompts/', ({ request }) => {
      const phaseParam = new URL(request.url).searchParams.get('phase');
      const rows = phaseParam ? TEMPLATES.filter((t) => t.phase === Number(phaseParam)) : TEMPLATES;
      return HttpResponse.json(paginated(rows));
    }),
    http.get('/api/system-descriptions/', () => HttpResponse.json(paginated(SYSTEMS))),
    http.get('/api/fewshots/', () => HttpResponse.json(paginated(FEWSHOTS))),
    http.options('/api/experiments/77/run/', () => HttpResponse.json(runOptionsResponse(DEFAULT_HELP_TEXT)))
  );
}

beforeEach(() => {
  authState.currentUserEmail = 'admin@example.com'; // staff - see test/server.js SEED_USERS
  installDetailFixtures();
});

describe('ExperimentDetail', () => {
  it("shows the experiment's CURRENT configuration, labelled current, never as what a repetition used", async () => {
    renderApp({ initialEntries: ['/experiments/77'] });

    expect(await screen.findByRole('heading', { name: 'Baseline comparison' })).toBeInTheDocument();
    const config = screen.getByTestId('current-config');
    expect(within(config).getByText(/not necessarily what any repetition/i)).toBeInTheDocument();
    expect(within(config).getByText('gen-a (v1)')).toBeInTheDocument();
    expect(within(config).getByText('gen-b (v1)')).toBeInTheDocument();
    expect(within(config).getByText('label (v1)')).toBeInTheDocument();
    expect(within(config).getByText(/oldsmar water plant/i)).toBeInTheDocument();
    expect(within(config).getByText(/shot-1/i)).toBeInTheDocument();
  });

  it('links to Edit, which reuses the builder form', async () => {
    renderApp({ initialEntries: ['/experiments/77'] });

    await screen.findByRole('heading', { name: 'Baseline comparison' });
    const editLink = screen.getByRole('link', { name: /^edit$/i });
    expect(editLink).toHaveAttribute('href', '/experiments/77/edit');
  });

  it('lists its own runs, each linking to Run detail', async () => {
    server.use(
      http.get('/api/pipeline-runs/', () =>
        HttpResponse.json(
          paginated([
            { id: 501, experiment: 77, repetition_index: 1, status: 'completed', config: {}, progress: { done: 32, total: 32, failed: 0, percent: 100 }, started_at: '2026-09-10T00:00:00Z', finished_at: '2026-09-10T00:10:00Z' },
          ])
        )
      )
    );
    renderApp({ initialEntries: ['/experiments/77'] });

    await screen.findByRole('heading', { name: 'Baseline comparison' });
    const link = await screen.findByRole('link', { name: /repetition 1/i });
    expect(link).toHaveAttribute('href', '/pipeline-runs/501');
  });

  it('the repetitions field defaults to 1, with its explanation sourced from the live OPTIONS response', async () => {
    const sentinelHelpText = 'SENTINEL — this text exists only in the OPTIONS mock, never hand-copied into the component.';
    server.use(http.options('/api/experiments/77/run/', () => HttpResponse.json(runOptionsResponse(sentinelHelpText))));

    renderApp({ initialEntries: ['/experiments/77'] });

    const repetitions = await screen.findByLabelText(/^repetitions$/i);
    expect(repetitions).toHaveValue(1);
    expect(await screen.findByText(sentinelHelpText)).toBeInTheDocument();
  });

  it('shows a neutral loading notice for the explanation while OPTIONS is in flight', async () => {
    server.use(
      http.options('/api/experiments/77/run/', async () => {
        await delay(50);
        return HttpResponse.json(runOptionsResponse('Resolved after the delay.'));
      })
    );

    renderApp({ initialEntries: ['/experiments/77'] });

    const repetitions = await screen.findByLabelText(/^repetitions$/i);
    expect(repetitions).toHaveValue(1);
    expect(screen.getByText(/loading the explanation/i)).toBeInTheDocument();
    expect(await screen.findByText('Resolved after the delay.')).toBeInTheDocument();
  });

  it('still lets repetitions be used if the OPTIONS explanation fails to load', async () => {
    const user = userEvent.setup();
    server.use(http.options('/api/experiments/77/run/', () => HttpResponse.json({ detail: 'Server error.' }, { status: 500 })));

    renderApp({ initialEntries: ['/experiments/77'] });

    const repetitions = await screen.findByLabelText(/^repetitions$/i);
    await user.clear(repetitions);
    await user.type(repetitions, '5');
    expect(repetitions).toHaveValue(5);
    expect(await screen.findByText(/could not load the explanation/i)).toBeInTheDocument();
  });

  it('launching shows the queued repetitions and links to them', async () => {
    const user = userEvent.setup();
    server.use(
      http.post(
        '/api/experiments/77/run/',
        guardedRunWrite(async ({ request }) => {
          const body = await request.json();
          expect(body.n_repetitions).toBe(2);
          return HttpResponse.json(
            {
              runs: [
                { id: 501, repetition_index: 1 },
                { id: 502, repetition_index: 2 },
              ],
              detail: 'Cancelling marks a run dead so it can be deleted; it does NOT interrupt a worker that may already be executing it.',
            },
            { status: 202 }
          );
        })
      )
    );

    renderApp({ initialEntries: ['/experiments/77'] });
    const repetitions = await screen.findByLabelText(/^repetitions$/i);
    await user.clear(repetitions);
    await user.type(repetitions, '2');
    await user.click(screen.getByRole('button', { name: /^launch$/i }));

    expect(await screen.findByText(/does not interrupt a worker/i)).toBeInTheDocument();
    const link1 = await screen.findByRole('link', { name: /repetition 1/i });
    const link2 = screen.getByRole('link', { name: /repetition 2/i });
    expect(link1).toHaveAttribute('href', '/pipeline-runs/501');
    expect(link2).toHaveAttribute('href', '/pipeline-runs/502');
  });

  it('launching with no API key branches on the 503 code, not the English message', async () => {
    const user = userEvent.setup();
    server.use(
      http.post(
        '/api/experiments/77/run/',
        guardedRunWrite(() =>
          // Deliberately unusual wording, carrying no "key"/"openrouter"
          // substring at all - the "configure a key" guidance must appear
          // because `code` is missing_api_key, not because it parsed this
          // sentence.
          HttpResponse.json({ detail: 'Cannot proceed right now.', code: 'missing_api_key' }, { status: 503 })
        )
      )
    );

    renderApp({ initialEntries: ['/experiments/77'] });
    await screen.findByLabelText(/^repetitions$/i);
    await user.click(screen.getByRole('button', { name: /^launch$/i }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Cannot proceed right now.');
    expect(screen.getByRole('link', { name: /go to settings/i })).toHaveAttribute('href', '/settings');
  });

  // Task 10.4: the backend's new launch-time dry render refuses a template
  // that fails as its own variant with a standard DRF field error keyed on
  // `phase2_prompt_a`/`phase2_prompt_b` - shown next to the SAME field the
  // create/edit form uses, even though it arrived from the launch action.
  it('a field-shaped launch refusal (phase2_prompt_a/b) lands next to the right field, not in a generic banner', async () => {
    const user = userEvent.setup();
    server.use(
      http.post(
        '/api/experiments/77/run/',
        guardedRunWrite(() =>
          HttpResponse.json(
            { phase2_prompt_a: ["'gen-a' fails to render as variant A: 'taxonomy' is undefined."] },
            { status: 400 }
          )
        )
      )
    );

    renderApp({ initialEntries: ['/experiments/77'] });
    await screen.findByLabelText(/^repetitions$/i);
    await user.click(screen.getByRole('button', { name: /^launch$/i }));

    const config = screen.getByTestId('current-config');
    expect(await within(config).findByText(/fails to render as variant a/i)).toBeInTheDocument();
    // Not duplicated in a generic banner - the body was entirely field-shaped.
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  // Coordinator review (final polish, item 3): the phase2_prompt_a case
  // above was pinned, but nothing pinned its sibling field - removing the
  // `phase2_prompt_b` `<FieldNote>` still passed every existing test.
  it('a field-shaped launch refusal on phase2_prompt_b lands next to that field too, not in a generic banner', async () => {
    const user = userEvent.setup();
    server.use(
      http.post(
        '/api/experiments/77/run/',
        guardedRunWrite(() =>
          HttpResponse.json(
            { phase2_prompt_b: ["'gen-b' fails to render as variant B: 'taxonomy' is undefined."] },
            { status: 400 }
          )
        )
      )
    );

    renderApp({ initialEntries: ['/experiments/77'] });
    await screen.findByLabelText(/^repetitions$/i);
    await user.click(screen.getByRole('button', { name: /^launch$/i }));

    const config = screen.getByTestId('current-config');
    expect(await within(config).findByText(/fails to render as variant b/i)).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  // Coordinator review (final polish, item 1): before this, a launch
  // refusal keyed on anything OTHER than phase2_prompt_a/b - `seed_systems`,
  // `n_repetitions`, a non-field error - showed NOTHING at all; Launch just
  // appeared to silently do nothing. `showLaunchBanner` now also fires for
  // any field error this screen has no inline slot for, naming the field.
  it('a launch refusal keyed on a field with no inline slot (seed_systems) still shows visible text, naming the field', async () => {
    const user = userEvent.setup();
    server.use(
      http.post(
        '/api/experiments/77/run/',
        guardedRunWrite(() =>
          HttpResponse.json(
            { seed_systems: ["['Leaked system'] were generated by a previous run."] },
            { status: 400 }
          )
        )
      )
    );

    renderApp({ initialEntries: ['/experiments/77'] });
    await screen.findByLabelText(/^repetitions$/i);
    await user.click(screen.getByRole('button', { name: /^launch$/i }));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(/seed_systems/i);
    expect(alert).toHaveTextContent(/generated by a previous run/i);
  });

  it('a launch refusal keyed on n_repetitions still shows visible text, naming the field', async () => {
    const user = userEvent.setup();
    server.use(
      http.post(
        '/api/experiments/77/run/',
        guardedRunWrite(() => HttpResponse.json({ n_repetitions: ['Ensure this value is less than or equal to 100.'] }, { status: 400 }))
      )
    );

    renderApp({ initialEntries: ['/experiments/77'] });
    const repetitions = await screen.findByLabelText(/^repetitions$/i);
    // A perfectly VALID client-side value - this proves the banner fallback
    // independently of the input-side constraint below (item 1's own
    // "still surface the error" half), e.g. for a future backend rule this
    // screen doesn't know about yet.
    await user.clear(repetitions);
    await user.type(repetitions, '5');
    await user.click(screen.getByRole('button', { name: /^launch$/i }));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(/n_repetitions/i);
    expect(alert).toHaveTextContent(/less than or equal to 100/i);
  });

  it('a non-field launch refusal still shows visible text', async () => {
    const user = userEvent.setup();
    server.use(
      http.post(
        '/api/experiments/77/run/',
        guardedRunWrite(() =>
          HttpResponse.json({ non_field_errors: ['Something is broken across the whole configuration.'] }, { status: 400 })
        )
      )
    );

    renderApp({ initialEntries: ['/experiments/77'] });
    await screen.findByLabelText(/^repetitions$/i);
    await user.click(screen.getByRole('button', { name: /^launch$/i }));

    expect(await screen.findByRole('alert')).toHaveTextContent(/broken across the whole configuration/i);
  });

  // Coordinator review (final polish, item 1): `OPTIONS .../run/` reports
  // n_repetitions as 1-100, but nothing enforced that client-side before -
  // the field was a bare, unclamped string, so an out-of-range value was a
  // DIRECTLY REACHABLE instance of the silent-failure gap above (typing
  // "500" and clicking Launch reached the mutation with no feedback at
  // all). Now constrained: Launch disables itself and says why.
  it('the repetitions field is constrained to 1-100 - Launch disables itself on an out-of-range value, with a visible reason', async () => {
    const user = userEvent.setup();
    let launchRequested = false;
    server.use(
      http.post(
        '/api/experiments/77/run/',
        guardedRunWrite(() => {
          launchRequested = true;
          return HttpResponse.json({ runs: [], detail: '' });
        })
      )
    );

    renderApp({ initialEntries: ['/experiments/77'] });
    const repetitions = await screen.findByLabelText(/^repetitions$/i);
    const launchButton = screen.getByRole('button', { name: /^launch$/i });
    expect(launchButton).toBeEnabled();

    await user.clear(repetitions);
    await user.type(repetitions, '500');
    expect(launchButton).toBeDisabled();
    expect(screen.getByText(/enter a whole number between 1 and 100/i)).toBeInTheDocument();

    await user.click(launchButton);
    expect(launchRequested).toBe(false);

    await user.clear(repetitions);
    await user.type(repetitions, '0');
    expect(launchButton).toBeDisabled();

    await user.clear(repetitions);
    await user.type(repetitions, '100');
    expect(launchButton).toBeEnabled();
    expect(screen.queryByText(/enter a whole number between 1 and 100/i)).not.toBeInTheDocument();
  });

  it('hides Edit and Launch for a non-staff viewer, who can still read the configuration and runs', async () => {
    authState.currentUserEmail = 'researcher@example.com'; // not staff
    renderApp({ initialEntries: ['/experiments/77'] });

    expect(await screen.findByRole('heading', { name: 'Baseline comparison' })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /^edit$/i })).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/^repetitions$/i)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^launch$/i })).not.toBeInTheDocument();
  });

  // The exact scenario task 10.3 exists to fix: before this screen, the
  // builder's own 503 hint unmounted the only state a launch could happen
  // from, there was no `/experiments/:id` route, and Dashboard rows were
  // not links - an experiment that hit this 503 once was stranded forever.
  it('an experiment is never stranded: create it, hit the missing-key 503, navigate away, come back via the Dashboard, and launch', async () => {
    const user = userEvent.setup();
    let experiments = [];
    server.use(
      http.get('/api/models/', () => HttpResponse.json({ count: 1, results: [{ id: 'anthropic/claude-sonnet-4', name: 'Claude Sonnet 4', pricing: { prompt: '0.000003' } }] })),
      http.post(
        '/api/experiments/',
        guardedRunWrite(async ({ request }) => {
          const body = await request.json();
          const created = { ...EXPERIMENT, ...body, id: 77 };
          experiments = [created];
          return HttpResponse.json(created, { status: 201 });
        })
      ),
      http.get('/api/experiments/', () => HttpResponse.json(paginated(experiments))),
      http.get('/api/experiments/77/', () => HttpResponse.json(experiments[0] ?? EXPERIMENT))
    );

    // 1. Create.
    renderApp({ initialEntries: ['/experiments/new'] });
    await screen.findByLabelText(/^name$/i);
    await user.type(screen.getByLabelText(/^name$/i), 'Baseline comparison');
    await user.selectOptions(await screen.findByLabelText(/^taxonomy$/i), '2');
    const phase2ModelField = await screen.findByLabelText(/^phase 2 model$/i);
    await within(phase2ModelField).findByText(/claude sonnet 4/i);
    await user.selectOptions(phase2ModelField, 'anthropic/claude-sonnet-4');
    const variantASelect = await screen.findByLabelText(/variant a/i, { selector: 'select' });
    await within(variantASelect).findByText('gen-a (v1)');
    await user.selectOptions(variantASelect, '1');
    const variantBSelect = screen.getByLabelText(/variant b/i, { selector: 'select' });
    await user.selectOptions(variantBSelect, '2');
    const phase3ModelField = screen.getByLabelText(/^phase 3 model$/i);
    await user.type(phase3ModelField, 'openai/gpt-4o-mini');
    const phase3PromptField = await screen.findByLabelText(/^phase 3 prompt$/i);
    await within(phase3PromptField).findByText('label (v1)');
    await user.selectOptions(phase3PromptField, '3');
    await user.click(screen.getByRole('button', { name: /create experiment/i }));

    // 2. Landed on the detail screen; launching hits the 503.
    expect(await screen.findByRole('heading', { name: 'Baseline comparison' })).toBeInTheDocument();
    server.use(
      http.post(
        '/api/experiments/77/run/',
        guardedRunWrite(() => HttpResponse.json({ detail: 'No API key configured.', code: 'missing_api_key' }, { status: 503 }))
      )
    );
    await screen.findByLabelText(/^repetitions$/i);
    await user.click(screen.getByRole('button', { name: /^launch$/i }));
    expect(await screen.findByRole('link', { name: /go to settings/i })).toBeInTheDocument();

    // 3. Navigate away entirely - the old bug's own unmount, now just an
    // ordinary route change with nothing left behind to lose.
    await user.click(screen.getByRole('link', { name: 'Dashboard' }));
    // The row's link wraps BOTH the name and its run-count text, so its
    // accessible name is the two concatenated - a regex, not an exact
    // match, is what actually finds it.
    expect(await screen.findByRole('link', { name: /baseline comparison/i })).toHaveAttribute('href', '/experiments/77');

    // 4. Back to the SAME experiment, entirely through navigation - no id
    // typed anywhere - and launch for real this time.
    server.use(
      http.post(
        '/api/experiments/77/run/',
        guardedRunWrite(() =>
          HttpResponse.json(
            { runs: [{ id: 501, repetition_index: 1 }], detail: 'Cancelling marks a run dead so it can be deleted.' },
            { status: 202 }
          )
        )
      )
    );
    await user.click(screen.getByRole('link', { name: /baseline comparison/i }));
    await screen.findByLabelText(/^repetitions$/i);
    await user.click(screen.getByRole('button', { name: /^launch$/i }));

    expect(await screen.findByRole('link', { name: /repetition 1/i })).toHaveAttribute('href', '/pipeline-runs/501');
  });

  // `POST /api/experiments/{id}/run/` is staff-only (spec §9) - the UI
  // itself already hides the Launch button/form for a non-staff viewer
  // (the "hides Edit and Launch..." test above), so this calls the raw
  // `apiClient` directly, the same pattern client.test.js's own CSRF/auth
  // guard tests use, to prove the MOCK's own `requireStaff` guard is
  // actually wired into this endpoint - not merely present in the file.
  it('the mock refuses POST /api/experiments/{id}/run/ for a non-staff session (guard actually wired)', async () => {
    server.use(http.post('/api/experiments/77/run/', guardedRunWrite(() => HttpResponse.json({ runs: [], detail: '' }))));
    authState.currentUserEmail = 'researcher@example.com'; // not staff

    await expect(apiClient.post('/experiments/77/run/', { n_repetitions: 1 })).rejects.toMatchObject({
      response: { status: 403 },
    });
  });
});
