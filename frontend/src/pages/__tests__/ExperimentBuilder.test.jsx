import { beforeEach, describe, expect, it } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { delay, http, HttpResponse } from 'msw';
import { authState, currentUser, server } from '../../test/server';
import { renderApp } from '../../test/renderApp';
import { paginate, paginated, requireCsrf, requireStaff } from '../../test/mockApi';

// The OpenRouter catalogue shape llm/models_api.py's AvailableModelsView
// actually returns - {count, results}, deliberately NOT the paginated
// envelope (it is never paged; see that view's own tests).
const MODELS_SEED = {
  count: 2,
  results: [
    { id: 'anthropic/claude-sonnet-4', name: 'Claude Sonnet 4', pricing: { prompt: '0.000003' } },
    { id: 'openai/gpt-4o-mini', name: 'GPT-4o mini', pricing: { prompt: '0.0000002' } },
  ],
};

// Mirrors prompts/models.py's PromptTemplate.Meta.ordering shape (phase,
// name, version) - two phase-2 templates so variant A and variant B can
// genuinely be pointed at different ones, and one phase-3 template.
// 55 filler phase-2 templates (ids 100-154), appended AFTER gen-a/gen-b/
// label so the mock's own declaration order keeps those three on page 1
// regardless - only the FILLER rows themselves are meant to prove
// `usePromptTemplates` (task 10.1: now fetch-all) reaches page 2. A real
// prompt-tuning history is exactly what makes phase 2's own row count pass
// PAGE_SIZE=50 (every `PATCH` creates a new version, never mutates one).
const PAGINATION_FILLER_TEMPLATES = Array.from({ length: 55 }, (_, index) => ({
  id: 100 + index,
  phase: 2,
  name: `filler-template-${String(index + 1).padStart(2, '0')}`,
  version: 1,
  system_prompt: '',
  user_prompt: 'filler',
  output_schema: {},
  is_active: false,
  created_by: 3,
  created_at: '2026-01-01T00:00:00Z',
}));

const TEMPLATES_SEED = [
  { id: 1, phase: 2, name: 'gen-a', version: 1, system_prompt: '', user_prompt: 'A', output_schema: {}, is_active: true, created_by: 3, created_at: '2026-01-01T00:00:00Z' },
  { id: 2, phase: 2, name: 'gen-b', version: 1, system_prompt: '', user_prompt: 'B {{ taxonomy }}', output_schema: {}, is_active: true, created_by: 3, created_at: '2026-01-01T00:00:00Z' },
  { id: 3, phase: 3, name: 'label', version: 1, system_prompt: '', user_prompt: 'L', output_schema: {}, is_active: true, created_by: 3, created_at: '2026-01-01T00:00:00Z' },
  ...PAGINATION_FILLER_TEMPLATES,
];

// corpus.models.Origin - a seed row is a legitimate seed; a generated row
// is a previous run's own output, which Experiment.clean() refuses as a
// seed or target (backend/pipeline/models.py's generated_seed_systems_error/
// generated_target_system_error).
// 55 filler seed systems (ids 200-254), appended AFTER the two essential
// rows for the same reason as PAGINATION_FILLER_TEMPLATES above - proving
// `useSystemDescriptions` (task 10.1: now fetch-all) backs the builder's
// own seed/target pickers, which must offer every eligible system or a
// researcher simply cannot select one past row 50.
const PAGINATION_FILLER_SYSTEMS = Array.from({ length: 55 }, (_, index) => ({
  id: 200 + index,
  title: `Filler seed ${String(index + 1).padStart(2, '0')}`,
  slug: `filler-seed-${index + 1}`,
  body: 'filler body',
  origin: 'seed',
  is_editable: true,
  created_by: 1,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
}));

const SYSTEMS_SEED = [
  { id: 10, title: 'Oldsmar water plant', slug: 'oldsmar', body: 'A plant.', origin: 'seed', is_editable: true, created_by: 1, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' },
  { id: 11, title: 'Leaked system', slug: 'leaked', body: 'Generated output.', origin: 'generated', is_editable: false, created_by: null, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' },
  ...PAGINATION_FILLER_SYSTEMS,
];

const FEWSHOTS_SEED = [
  { id: 20, phase: 2, name: 'shot-1', input_text: 'in', input_file: null, output_text: 'out', output_file: null, resolved_input: 'in', resolved_output: 'out', order: 1, is_active: true },
];

// The exact envelope `OPTIONS /api/experiments/{id}/run/` now returns
// (backend commit 6f8e9a0, pinned by `pipeline/tests/test_experiment_api.py::
// test_options_on_run_describes_n_repetitions_help_text`): DRF's standard
// `SimpleMetadata.determine_metadata` shape, with `actions.POST` narrowed
// to ONLY `n_repetitions` (the real endpoint asserts
// `set(fields) == {"n_repetitions"}` - `RunRequestSerializer` has no other
// field). `helpText` is the one part a caller actually reads.
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

// Used by every test that doesn't care about the EXACT wording - only the
// dedicated test below asserts the real text is what renders, via its own
// distinctive sentinel (`server.use()` override, one handler wins over
// another registered for the same method+path).
const DEFAULT_RUN_OPTIONS_HELP_TEXT = 'Default fixture text for n_repetitions, unrelated to the real backend wording.';

function installBuilderFixtures() {
  server.use(
    http.get('/api/models/', () => HttpResponse.json(MODELS_SEED)),
    // `paginate`, not the single-page `paginated`: PAGINATION_FILLER_
    // TEMPLATES pushes phase 2's own row count past PAGE_SIZE=50, so
    // `usePromptTemplates` (now fetch-all, task 10.1) has something real to
    // prove itself against.
    http.get('/api/prompts/', ({ request }) => {
      const url = new URL(request.url);
      const phaseParam = url.searchParams.get('phase');
      const rows = phaseParam ? TEMPLATES_SEED.filter((t) => t.phase === Number(phaseParam)) : TEMPLATES_SEED;
      return HttpResponse.json(paginate(rows, url));
    }),
    // Same reasoning, for `useSystemDescriptions` and
    // PAGINATION_FILLER_SYSTEMS.
    http.get('/api/system-descriptions/', ({ request }) => HttpResponse.json(paginate(SYSTEMS_SEED, request))),
    http.get('/api/fewshots/', () => HttpResponse.json(paginated(FEWSHOTS_SEED))),
    http.options('/api/experiments/:id/run/', () =>
      HttpResponse.json(runOptionsResponse(DEFAULT_RUN_OPTIONS_HELP_TEXT))
    )
  );
}

// `POST /api/experiments/`/`POST /api/experiments/{id}/run/` are staff-only
// (spec §9) - every test in this file installs its OWN handler for these
// (the create/launch response body differs per test), so the CSRF/staff
// guard lives here, wrapped around each test's own resolver, rather than as
// a separate layered handler an msw request only reaches if the test's own
// (later-registered, so higher-priority) handler falls through to it.
function guardedExperimentWrite(resolver) {
  return async (info) => {
    const staffRefusal = requireStaff(currentUser());
    if (staffRefusal) return staffRefusal;
    const csrfRefusal = requireCsrf(info.request);
    if (csrfRefusal) return csrfRefusal;
    return resolver(info);
  };
}

beforeEach(() => {
  authState.currentUserEmail = 'admin@example.com'; // staff - see test/server.js SEED_USERS
  installBuilderFixtures();
});

// Fills in every field a real POST /api/experiments/ needs to validate
// cleanly, so a test can then deliberately corrupt exactly one thing and
// see that ONE field error land in the right place - never fighting five
// unrelated "required" errors at once.
// Selects an option only once it has actually loaded - the field itself
// (label + <select>/<input>) can exist on the very first render, well
// before the async query populating its options resolves; selecting
// against it too early is exactly the race PromptEditor.test.jsx's own
// `selectTemplate` helper already guards against.
async function selectOnceLoaded(user, label, optionText, value, selectorOpts) {
  const field = await screen.findByLabelText(label, selectorOpts);
  await within(field).findByText(optionText);
  await user.selectOptions(field, value);
}

async function fillMinimalValidForm(user, { name = 'Baseline comparison' } = {}) {
  await user.type(screen.getByLabelText(/^name$/i), name);
  await selectOnceLoaded(user, /^taxonomy$/i, /active/i, '2'); // active taxonomy, from server.js's TAXONOMIES_SEED
  await selectOnceLoaded(user, /^phase 2 model$/i, /claude sonnet 4/i, 'anthropic/claude-sonnet-4');
  await selectOnceLoaded(user, /variant a/i, 'gen-a (v1)', '1', { selector: 'select' });
  await selectOnceLoaded(user, /variant b/i, 'gen-b (v1)', '2', { selector: 'select' });
  await selectOnceLoaded(user, /^phase 3 model$/i, /gpt-4o mini/i, 'openai/gpt-4o-mini');
  await selectOnceLoaded(user, /^phase 3 prompt$/i, 'label (v1)', '3');
}

describe('ExperimentBuilder', () => {
  it('selects models per phase from the model catalogue', async () => {
    renderApp({ initialEntries: ['/experiments/new'] });

    const phase2Model = await screen.findByLabelText(/^phase 2 model$/i);
    expect(await within(phase2Model).findByText(/claude sonnet 4/i)).toBeInTheDocument();
    const phase3Model = screen.getByLabelText(/^phase 3 model$/i);
    expect(await within(phase3Model).findByText(/gpt-4o mini/i)).toBeInTheDocument();
  });

  it('selects a phase-2 prompt per variant, and a phase-3 prompt', async () => {
    renderApp({ initialEntries: ['/experiments/new'] });

    const variantA = await screen.findByLabelText(/variant a/i, { selector: 'select' });
    const variantB = screen.getByLabelText(/variant b/i, { selector: 'select' });
    expect(await within(variantA).findByText('gen-a (v1)')).toBeInTheDocument();
    expect(await within(variantB).findByText('gen-b (v1)')).toBeInTheDocument();

    const phase3Prompt = screen.getByLabelText(/^phase 3 prompt$/i);
    expect(await within(phase3Prompt).findByText('label (v1)')).toBeInTheDocument();
    // Phase-2 templates must never leak into the phase-3 dropdown.
    expect(within(phase3Prompt).queryByText('gen-a (v1)')).not.toBeInTheDocument();
  });

  it('selects taxonomy, seed systems, few-shots, both toggles, counts and variants', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/experiments/new'] });

    await screen.findByLabelText(/^name$/i);
    expect(await screen.findByLabelText(/oldsmar water plant/i)).toBeInTheDocument();
    expect(await screen.findByLabelText(/shot-1/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/^generate systems$/i)).toBeChecked();
    expect(screen.getByLabelText(/^generate scenarios$/i)).toBeChecked();

    const nSystems = screen.getByLabelText(/systems per repetition/i);
    const nScenarios = screen.getByLabelText(/scenarios per system/i);
    await user.clear(nSystems);
    await user.type(nSystems, '4');
    await user.clear(nScenarios);
    await user.type(nScenarios, '6');
    expect(nSystems).toHaveValue(4);
    expect(nScenarios).toHaveValue(6);

    // Both variant checkboxes are on by default (Experiment.variants
    // defaults to both - pipeline/models.py, Experiment.save()).
    expect(screen.getByRole('checkbox', { name: /^variant a$/i })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: /^variant b$/i })).toBeChecked();
  });

  it('the repetitions field defaults to 1, and its explanation comes from the live API, never a hand-copied string', async () => {
    const user = userEvent.setup();
    // A sentinel that could only ever have come from the OPTIONS mock below -
    // proves the rendered text is SOURCED from the API response, not from
    // any string baked into the component.
    const sentinelHelpText = 'SENTINEL — this text exists only in the OPTIONS mock, never hand-copied into the component.';
    server.use(
      http.post(
        '/api/experiments/',
        guardedExperimentWrite(async ({ request }) => {
          const body = await request.json();
          return HttpResponse.json({ id: 77, ...body }, { status: 201 });
        })
      ),
      http.options('/api/experiments/77/run/', () => HttpResponse.json(runOptionsResponse(sentinelHelpText)))
    );
    renderApp({ initialEntries: ['/experiments/new'] });

    await screen.findByLabelText(/^name$/i);
    await fillMinimalValidForm(user);
    await user.click(screen.getByRole('button', { name: /create experiment/i }));

    const repetitions = await screen.findByLabelText(/^repetitions$/i);
    expect(repetitions).toHaveValue(1);

    expect(await screen.findByText(sentinelHelpText)).toBeInTheDocument();
    // The old hand-copied sentence is gone from the SOURCE entirely now
    // (one source of truth, the live OPTIONS response) - this would only
    // ever appear again if someone reintroduced a hardcoded fallback.
    expect(screen.queryByText(/repeat this exact configuration/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/\$1\.61/)).not.toBeInTheDocument();
  });

  it('shows a neutral loading notice for the explanation - never the old hand-copied text - while OPTIONS is in flight', async () => {
    const user = userEvent.setup();
    server.use(
      http.post(
        '/api/experiments/',
        guardedExperimentWrite(async ({ request }) => {
          const body = await request.json();
          return HttpResponse.json({ id: 77, ...body }, { status: 201 });
        })
      ),
      http.options('/api/experiments/77/run/', async () => {
        await delay(50);
        return HttpResponse.json(runOptionsResponse('Resolved after the delay.'));
      })
    );
    renderApp({ initialEntries: ['/experiments/new'] });

    await screen.findByLabelText(/^name$/i);
    await fillMinimalValidForm(user);
    await user.click(screen.getByRole('button', { name: /create experiment/i }));

    // The field itself works immediately - it is never gated on this query.
    const repetitions = await screen.findByLabelText(/^repetitions$/i);
    expect(repetitions).toHaveValue(1);
    expect(screen.getByText(/loading the explanation/i)).toBeInTheDocument();
    expect(screen.queryByText(/repeat this exact configuration/i)).not.toBeInTheDocument();

    expect(await screen.findByText('Resolved after the delay.')).toBeInTheDocument();
  });

  it('still lets repetitions be used if the API explanation fails to load, and never falls back to stale copy', async () => {
    const user = userEvent.setup();
    server.use(
      http.post(
        '/api/experiments/',
        guardedExperimentWrite(async ({ request }) => {
          const body = await request.json();
          return HttpResponse.json({ id: 77, ...body }, { status: 201 });
        })
      ),
      http.options('/api/experiments/77/run/', () => HttpResponse.json({ detail: 'Server error.' }, { status: 500 }))
    );
    renderApp({ initialEntries: ['/experiments/new'] });

    await screen.findByLabelText(/^name$/i);
    await fillMinimalValidForm(user);
    await user.click(screen.getByRole('button', { name: /create experiment/i }));

    const repetitions = await screen.findByLabelText(/^repetitions$/i);
    expect(repetitions).toHaveValue(1);
    await user.clear(repetitions);
    await user.type(repetitions, '5');
    expect(repetitions).toHaveValue(5);

    expect(await screen.findByText(/could not load the explanation/i)).toBeInTheDocument();
    expect(screen.queryByText(/repeat this exact configuration/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/\$1\.61/)).not.toBeInTheDocument();
  });

  it("surfaces the API's validation errors field-by-field, never in a generic banner", async () => {
    const user = userEvent.setup();
    server.use(
      http.post(
        '/api/experiments/',
        guardedExperimentWrite(async ({ request }) => {
        const body = await request.json();
        if (body.name === 'trigger-field-errors') {
          // Shaped exactly like Experiment.clean()'s message_dict, re-raised
          // unchanged by ExperimentSerializer.validate() (pipeline/serializers.py) -
          // several distinct rules firing at once: a generated-origin seed
          // AND target, a template pinned to the wrong phase, an empty
          // taxonomy, and both generation toggles off.
          return HttpResponse.json(
            {
              seed_systems: [
                "['Leaked system'] were generated by a previous run. Seeding an experiment with its own output contaminates the comparison.",
              ],
              target_system: [
                "'Leaked system' was generated by a previous run. Targeting an experiment at its own output contaminates the comparison.",
              ],
              phase2_prompt_b: ['label is a phase 3 template; phase2_prompt_b must be a phase 2 template.'],
              taxonomy: ['Empty taxonomy has no HCV rows, so variant A leak backstop cannot run.'],
              generate_systems: ['Enable at least one of generate_systems or generate_scenarios.'],
            },
            { status: 400 }
          );
        }
        return HttpResponse.json({ id: 77, ...body }, { status: 201 });
        })
      )
    );
    renderApp({ initialEntries: ['/experiments/new'] });

    await screen.findByLabelText(/^name$/i);
    await fillMinimalValidForm(user, { name: 'trigger-field-errors' });
    // Uncheck "generate systems" so the target-system field (and its error
    // slot) are actually on screen for this attempt.
    await user.click(screen.getByLabelText(/^generate systems$/i));

    await user.click(screen.getByRole('button', { name: /create experiment/i }));

    // Every message lands next to the field it names...
    expect(await screen.findByText(/seeding an experiment with its own output/i)).toBeInTheDocument();
    expect(screen.getByText(/targeting an experiment at its own output/i)).toBeInTheDocument();
    expect(screen.getByText(/phase2_prompt_b must be a phase 2 template/i)).toBeInTheDocument();
    expect(screen.getByText(/empty taxonomy has no hcv rows/i)).toBeInTheDocument();
    expect(screen.getByText(/enable at least one of generate_systems/i)).toBeInTheDocument();

    // ...and there is no generic banner duplicating any of it, because
    // nothing in this response was `detail`/`non_field_errors`-shaped.
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('falls back to a banner only for a genuinely non-field error', async () => {
    const user = userEvent.setup();
    server.use(
      http.post(
        '/api/experiments/',
        guardedExperimentWrite(async ({ request }) => {
          const body = await request.json();
          if (body.name === 'trigger-nonfield-error') {
            return HttpResponse.json(
              { non_field_errors: ['Something is broken across the whole configuration.'] },
              { status: 400 }
            );
          }
          return HttpResponse.json({ id: 77, ...body }, { status: 201 });
        })
      )
    );
    renderApp({ initialEntries: ['/experiments/new'] });

    await screen.findByLabelText(/^name$/i);
    await fillMinimalValidForm(user, { name: 'trigger-nonfield-error' });
    await user.click(screen.getByRole('button', { name: /create experiment/i }));

    expect(await screen.findByRole('alert')).toHaveTextContent(/broken across the whole configuration/i);
  });

  it('launching shows the queued repetitions and links to them', async () => {
    const user = userEvent.setup();
    server.use(
      http.post(
        '/api/experiments/',
        guardedExperimentWrite(async ({ request }) => {
          const body = await request.json();
          return HttpResponse.json({ id: 77, ...body }, { status: 201 });
        })
      ),
      http.post(
        '/api/experiments/77/run/',
        guardedExperimentWrite(async ({ request }) => {
          const body = await request.json();
          expect(body.n_repetitions).toBe(2);
          return HttpResponse.json(
            {
              runs: [
                { id: 501, repetition_index: 1 },
                { id: 502, repetition_index: 2 },
              ],
              detail:
                'Cancelling marks a run dead so it can be deleted; it does NOT interrupt a worker that may already be executing it.',
            },
            { status: 202 }
          );
        })
      )
    );
    renderApp({ initialEntries: ['/experiments/new'] });

    await screen.findByLabelText(/^name$/i);
    await fillMinimalValidForm(user);
    await user.click(screen.getByRole('button', { name: /create experiment/i }));

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
        '/api/experiments/',
        guardedExperimentWrite(async ({ request }) => {
          const body = await request.json();
          return HttpResponse.json({ id: 77, ...body }, { status: 201 });
        })
      ),
      http.post(
        '/api/experiments/77/run/',
        guardedExperimentWrite(() =>
          // Deliberately unusual wording, carrying no "key"/"openrouter"
          // substring at all - if the UI's extra "configure a key" guidance
          // only appears because it parsed THIS English sentence, this would
          // fail; it must appear because `code` is missing_api_key.
          HttpResponse.json({ detail: 'Cannot proceed right now.', code: 'missing_api_key' }, { status: 503 })
        )
      )
    );
    renderApp({ initialEntries: ['/experiments/new'] });

    await screen.findByLabelText(/^name$/i);
    await fillMinimalValidForm(user);
    await user.click(screen.getByRole('button', { name: /create experiment/i }));

    await screen.findByLabelText(/^repetitions$/i);
    await user.click(screen.getByRole('button', { name: /^launch$/i }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Cannot proceed right now.');
    expect(screen.getByRole('link', { name: /go to settings/i })).toHaveAttribute('href', '/settings');
  });

  it('a non-staff user cannot see the builder form', async () => {
    authState.currentUserEmail = 'researcher@example.com';
    renderApp({ initialEntries: ['/experiments/new'] });

    expect(await screen.findByRole('status')).toHaveTextContent(/only staff/i);
    expect(screen.queryByLabelText(/^name$/i)).not.toBeInTheDocument();
  });

  it('the phase-2 prompt selects offer every template, not just the first 50 (fails if only page 1 is read)', async () => {
    renderApp({ initialEntries: ['/experiments/new'] });

    // Phase 2 has gen-a + gen-b + 55 filler templates = 57 rows - one page
    // (50) would drop the last 7, `filler-template-55` among them.
    const variantA = await screen.findByLabelText(/variant a/i, { selector: 'select' });
    expect(await within(variantA).findByText(/filler-template-55/)).toBeInTheDocument();
  });

  it('the seed-system list and target-system picker offer every system, not just the first 50 (fails if only page 1 is read)', async () => {
    renderApp({ initialEntries: ['/experiments/new'] });

    // 2 essential systems + 55 filler seeds = 57 rows - one page (50)
    // would drop the last 7, `Filler seed 55` among them.
    expect(await screen.findByLabelText(/filler seed 55/i)).toBeInTheDocument();
  });
});
