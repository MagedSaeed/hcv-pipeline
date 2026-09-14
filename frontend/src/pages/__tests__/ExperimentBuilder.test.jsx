import { beforeEach, describe, expect, it } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { authState, currentUser, server } from '../../test/server';
import { renderApp } from '../../test/renderApp';
import { paginate, paginated, requireCsrf, requireStaff } from '../../test/mockApi';
import { pickOption } from '../../test/pickOption';
import { apiClient } from '../../api/client';

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
  // The app's own seeded defaults (common.md's API contract) - separate
  // from gen-a/gen-b/label above so every OTHER test in this file, which
  // deliberately picks those by name, is unaffected by preselection ever
  // reaching for these three instead.
  { id: 4, phase: 2, name: 'default-generation-variant-a', version: 1, system_prompt: '', user_prompt: 'Default A', output_schema: {}, is_active: true, created_by: 3, created_at: '2026-01-01T00:00:00Z' },
  { id: 5, phase: 2, name: 'default-generation-variant-b', version: 1, system_prompt: '', user_prompt: 'Default B', output_schema: {}, is_active: true, created_by: 3, created_at: '2026-01-01T00:00:00Z' },
  { id: 6, phase: 3, name: 'default-labelling', version: 1, system_prompt: '', user_prompt: 'Default label', output_schema: {}, is_active: true, created_by: 3, created_at: '2026-01-01T00:00:00Z' },
  // Live only on one variant - the grouped-prompt-fields round's own
  // "each field lists only its own stored variant" rule.
  { id: 62, phase: 2, name: 'phase2-only-a', variant: 'A', version: 1, system_prompt: '', user_prompt: 'A only', output_schema: {}, is_active: true, created_by: 3, created_at: '2026-01-01T00:00:00Z' },
  { id: 63, phase: 2, name: 'phase2-only-b', variant: 'B', version: 1, system_prompt: '', user_prompt: 'B only', output_schema: {}, is_active: true, created_by: 3, created_at: '2026-01-01T00:00:00Z' },
  // Two saved versions of the same name - the ONE prompt in this fixture
  // that groups behind a toggle rather than rendering as a flat leaf.
  { id: 60, phase: 2, name: 'multi-version-prompt', variant: 'A', version: 1, system_prompt: '', user_prompt: 'V1', output_schema: {}, is_active: false, created_by: 3, created_at: '2026-01-01T00:00:00Z' },
  { id: 61, phase: 2, name: 'multi-version-prompt', variant: 'A', version: 2, system_prompt: '', user_prompt: 'V2', output_schema: {}, is_active: true, created_by: 3, created_at: '2026-01-02T00:00:00Z' },
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
// ModelPicker (task C) is a searchable combobox, not a native <select> -
// open it, type the model's id to narrow the list to one row, then click
// that row (found by the id ModelPicker itself puts on the option's
// `data-testid`).
async function pickModel(user, label, modelId) {
  const field = await screen.findByLabelText(label);
  await user.click(field);
  await user.type(field, modelId);
  const option = await screen.findByTestId(`model-option-${modelId}`);
  await user.click(option);
}

async function fillMinimalValidForm(user, { name = 'Baseline comparison' } = {}) {
  await user.type(screen.getByLabelText(/^name$/i), name);
  await pickOption(user, /^taxonomy$/i, '2'); // active taxonomy, from server.js's TAXONOMIES_SEED
  await pickModel(user, /^phase 2 model$/i, 'anthropic/claude-sonnet-4');
  await pickOption(user, /variant a/i, '1'); // gen-a (v1)
  await pickOption(user, /variant b/i, '2'); // gen-b (v1)
  await pickModel(user, /^phase 3 model$/i, 'openai/gpt-4o-mini');
  await pickOption(user, /^phase 3 prompt$/i, '3'); // label (v1)
}

describe('ExperimentBuilder', () => {
  it('selects models per phase from the model catalogue', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/experiments/new'] });

    await pickModel(user, /^phase 2 model$/i, 'anthropic/claude-sonnet-4');
    expect(screen.getByLabelText(/^phase 2 model$/i)).toHaveValue('Claude Sonnet 4 (anthropic/claude-sonnet-4)');

    await pickModel(user, /^phase 3 model$/i, 'openai/gpt-4o-mini');
    expect(screen.getByLabelText(/^phase 3 model$/i)).toHaveValue('GPT-4o mini (openai/gpt-4o-mini)');
  });

  it('a model picker shows its own price and an OpenRouter link once a model is selected', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/experiments/new'] });

    await pickModel(user, /^phase 2 model$/i, 'anthropic/claude-sonnet-4');
    const link = screen.getByRole('link', { name: /openrouter/i });
    expect(link).toHaveAttribute('href', 'https://openrouter.ai/anthropic/claude-sonnet-4');
    expect(link).toHaveAttribute('target', '_blank');
  });

  it('selects a phase-2 prompt per variant, and a phase-3 prompt', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/experiments/new'] });

    const variantA = await screen.findByRole('combobox', { name: /variant a/i });
    await user.click(variantA);
    expect(await screen.findByTestId('phase2-prompt-a-option-1')).toHaveTextContent('gen-a (v1)');

    const variantB = screen.getByRole('combobox', { name: /variant b/i });
    await user.click(variantB);
    expect(await screen.findByTestId('phase2-prompt-b-option-2')).toHaveTextContent('gen-b (v1)');

    const phase3Prompt = screen.getByRole('combobox', { name: /^phase 3 prompt$/i });
    await user.click(phase3Prompt);
    expect(await screen.findByTestId('phase3-prompt-option-3')).toHaveTextContent('label (v1)');
    // Phase-2 templates must never leak into the phase-3 dropdown.
    expect(screen.queryByTestId('phase3-prompt-option-1')).not.toBeInTheDocument();
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
    // A brand-new experiment defaults to 1 system × 3 scenarios (matching
    // the backend model's own new default), not the old 3 × 5.
    expect(nSystems).toHaveValue(1);
    expect(nScenarios).toHaveValue(3);
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

  // Task 10.3: launching (repetitions field, OPTIONS help text, the queued-
  // repetitions result, the missing-key 503 mapping) all moved to the new
  // Experiment detail screen (ExperimentDetail.test.jsx) - this screen no
  // longer has an inline "Launch" section at all, so those tests moved with
  // it rather than staying here against UI that no longer exists.

  // User feedback: "please have one default prompt for both variant[s]...
  // as a default there should be one for each variant."
  it('preselects the seeded default prompt templates on a brand-new experiment, and a later pick still wins', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/experiments/new'] });

    const variantA = await screen.findByRole('combobox', { name: /variant a/i });
    await within(variantA).findByText('default-generation-variant-a (v1)');

    const variantB = screen.getByRole('combobox', { name: /variant b/i });
    expect(within(variantB).getByText('default-generation-variant-b (v1)')).toBeInTheDocument();

    const phase3Prompt = screen.getByRole('combobox', { name: /^phase 3 prompt$/i });
    expect(within(phase3Prompt).getByText('default-labelling (v1)')).toBeInTheDocument();

    // Still a free choice - picking something else overrides the preselected default.
    await pickOption(user, /variant a/i, '1');
    expect(within(variantA).getByText('gen-a (v1)')).toBeInTheDocument();
  });

  it('auto-preselects the active taxonomy on a brand-new experiment, and a later pick still wins', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/experiments/new'] });

    // Taxonomy id 2 ("hcv-21 v2") is the active one - server.js's own
    // TAXONOMIES_SEED - so it fills in on its own with nothing clicked.
    const taxonomy = await screen.findByRole('combobox', { name: /^taxonomy$/i });
    await within(taxonomy).findByText(/hcv-21 v2 \(active\)/i);

    // Still a free choice - picking something else overrides it.
    await pickOption(user, /^taxonomy$/i, '1');
    expect(within(taxonomy).getByText(/hcv-21 v1/i)).toBeInTheDocument();
  });

  // Grouped-prompt-fields round: "instead of listing versions... only list
  // the prompts with a triangle collapse for each prompt for versions when
  // applicable" - `multi-version-prompt` (ids 60/61) is the one fixture
  // template with two saved versions.
  it('a prompt with several saved versions collapses behind a toggle, defaulting to its active version', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/experiments/new'] });

    const variantA = await screen.findByRole('combobox', { name: /variant a/i });
    await user.click(variantA);
    // v2 (id 61) is the active version - it, not v1, is what the group row
    // itself resolves to.
    const groupRow = await screen.findByTestId('phase2-prompt-a-option-61');
    expect(groupRow).toHaveTextContent('multi-version-prompt');
    expect(groupRow).not.toHaveTextContent('v1');
    // No version rows yet - collapsed by default.
    expect(screen.queryByTestId('phase2-prompt-a-version-60')).not.toBeInTheDocument();

    // Clicking the row itself (not the toggle) picks the active version.
    await user.click(groupRow);
    expect(within(variantA).getByText('multi-version-prompt (v2)')).toBeInTheDocument();

    // Reopening shows its versions already expanded, since the current
    // selection (v2) belongs to this group - pick the OLDER version instead.
    await user.click(variantA);
    expect(await screen.findByTestId('phase2-prompt-a-version-60')).toHaveTextContent('v1');
    await user.click(screen.getByTestId('phase2-prompt-a-version-60'));
    expect(within(variantA).getByText('multi-version-prompt (v1)')).toBeInTheDocument();

    // Collapsing it back closed hides the version rows again.
    await user.click(variantA);
    await user.click(screen.getByRole('button', { name: /collapse multi-version-prompt versions/i }));
    expect(screen.queryByTestId('phase2-prompt-a-version-61')).not.toBeInTheDocument();
  });

  it('each phase-2 prompt field lists only its own stored variant, plus any template with no variant set', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/experiments/new'] });

    const variantA = await screen.findByRole('combobox', { name: /variant a/i });
    await user.click(variantA);
    // Its own variant, and an unset-variant template (gen-a), both offered…
    expect(await screen.findByTestId('phase2-prompt-a-option-62')).toHaveTextContent('phase2-only-a (v1)');
    expect(screen.getByTestId('phase2-prompt-a-option-1')).toHaveTextContent('gen-a (v1)');
    // …but never the OTHER variant's own template.
    expect(screen.queryByTestId('phase2-prompt-a-option-63')).not.toBeInTheDocument();

    const variantB = screen.getByRole('combobox', { name: /variant b/i });
    await user.click(variantB);
    expect(await screen.findByTestId('phase2-prompt-b-option-63')).toHaveTextContent('phase2-only-b (v1)');
    expect(screen.getByTestId('phase2-prompt-b-option-2')).toHaveTextContent('gen-b (v1)');
    expect(screen.queryByTestId('phase2-prompt-b-option-62')).not.toBeInTheDocument();
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

  it('a successful create navigates straight to the new experiment\'s detail screen, never stranding it', async () => {
    const user = userEvent.setup();
    server.use(
      http.post(
        '/api/experiments/',
        guardedExperimentWrite(async ({ request }) => {
          const body = await request.json();
          return HttpResponse.json({ id: 77, ...body, run_count: 0, progress: { done: 0, total: 0, failed: 0, percent: 0 }, created_by: 3, created_at: '2026-09-10T00:00:00Z' }, { status: 201 });
        })
      ),
      // Whatever ExperimentDetail.jsx itself needs to render past its own
      // loading state - this test only cares that the builder actually
      // navigated there, not what that screen shows once it has.
      http.get('/api/experiments/77/', () =>
        HttpResponse.json({
          id: 77,
          name: 'Baseline comparison',
          description: '',
          taxonomy: 2,
          phase2_model: 'anthropic/claude-sonnet-4',
          phase2_temperature: 1,
          phase2_prompt_a: 1,
          phase2_prompt_b: 2,
          phase3_model: 'openai/gpt-4o-mini',
          phase3_temperature: 0,
          phase3_prompt: 3,
          seed_systems: [],
          fewshots: [],
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
        })
      ),
      http.get('/api/pipeline-runs/', () => HttpResponse.json(paginated([]))),
      http.options('/api/experiments/77/run/', () => HttpResponse.json(runOptionsResponse('help text')))
    );
    renderApp({ initialEntries: ['/experiments/new'] });

    await screen.findByLabelText(/^name$/i);
    await fillMinimalValidForm(user);
    await user.click(screen.getByRole('button', { name: /create experiment/i }));

    // Landed on the detail screen, not left on the builder with nothing
    // left to do - task 10.3's own acceptance bar.
    expect(await screen.findByRole('heading', { name: 'Baseline comparison' })).toBeInTheDocument();
    expect(screen.queryByLabelText(/^name$/i)).not.toBeInTheDocument();
  });

  it('always runs phase 2; unticking phase 3 hides its section and saves phases [2]', async () => {
    const user = userEvent.setup();
    let posted = null;
    server.use(
      http.post(
        '/api/experiments/',
        guardedExperimentWrite(async ({ request }) => {
          posted = await request.json();
          return HttpResponse.json({ phases: ['Phase 3 labels the scenarios phase 2 generates, so it needs phase 2.'] }, { status: 400 });
        })
      )
    );
    renderApp({ initialEntries: ['/experiments/new'] });

    const phase2 = await screen.findByRole('checkbox', { name: /phase 2 — generate scenarios/i });
    expect(phase2).toBeChecked();
    expect(phase2).toBeDisabled();
    const phase3 = screen.getByRole('checkbox', { name: /phase 3 — label the generated scenarios/i });
    expect(phase3).toBeChecked();
    expect(screen.getByLabelText(/^phase 3 prompt$/i)).toBeInTheDocument();

    await fillMinimalValidForm(user);
    await user.click(phase3);
    expect(screen.queryByLabelText(/^phase 3 prompt$/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/^phase 3 model$/i)).not.toBeInTheDocument();
    expect(screen.getByText(/only generates scenarios; nothing is labelled/i)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /create experiment/i }));
    expect(await screen.findByText(/so it needs phase 2/i)).toBeInTheDocument();
    expect(posted.phases).toEqual([2]);
  });

  it('a non-staff user cannot see the builder form', async () => {
    authState.currentUserEmail = 'researcher@example.com';
    renderApp({ initialEntries: ['/experiments/new'] });

    expect(await screen.findByRole('status')).toHaveTextContent(/only staff/i);
    expect(screen.queryByLabelText(/^name$/i)).not.toBeInTheDocument();
  });

  it('the phase-2 prompt selects offer every template, not just the first 50 (fails if only page 1 is read)', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/experiments/new'] });

    // Phase 2 has gen-a + gen-b + 55 filler templates = 57 rows (filler ids
    // 100-154) - one page (50) would drop the last 7, `filler-template-55`
    // (id 154) among them.
    const variantA = await screen.findByRole('combobox', { name: /variant a/i });
    await user.click(variantA);
    expect(await screen.findByTestId('phase2-prompt-a-option-154')).toHaveTextContent(/filler-template-55/);
  });

  // Task 10.5: the backend refuses ANY generated-origin row as a seed or a
  // target (spec §5.4 - its prose is taxonomy-DERIVED, so none of the
  // text-scanning isolation guards can catch it; the origin check is the
  // only thing that closes this route). Offering "Leaked system" here would
  // only ever be refused with a 400 the researcher has no way to anticipate
  // in advance - so it must not be offered at all, not merely discouraged.
  it('offers only seed/manual systems as a seed or a target — a generated-origin row the API would refuse is never offered', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/experiments/new'] });

    await screen.findByLabelText(/^name$/i);
    // A seed system genuinely renders (proof this is a real filter, not an
    // empty list)...
    expect(await screen.findByLabelText(/oldsmar water plant/i)).toBeInTheDocument();
    // ...but the generated-origin one never does, anywhere on the page.
    expect(screen.queryByLabelText(/leaked system/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/leaked system/i)).not.toBeInTheDocument();

    // Uncheck "generate systems" to reveal the Target system picker too.
    await user.click(screen.getByLabelText(/^generate systems$/i));
    const targetSelect = await screen.findByRole('combobox', { name: /^target system$/i });
    await user.click(targetSelect);
    expect(screen.getByTestId('target-system-option-10')).toHaveTextContent(/oldsmar water plant/i);
    expect(screen.queryByText(/leaked system/i)).not.toBeInTheDocument();
  });

  it('the seed-system list and target-system picker offer every system, not just the first 50 (fails if only page 1 is read)', async () => {
    renderApp({ initialEntries: ['/experiments/new'] });

    // 2 essential systems + 55 filler seeds = 57 rows - one page (50)
    // would drop the last 7, `Filler seed 55` among them.
    expect(await screen.findByLabelText(/filler seed 55/i)).toBeInTheDocument();
  });
});

// Task 10.3: this SAME form, at `/experiments/:id/edit`, is also the
// "Edit" action reachable from Experiment detail - `Experiment` stays
// completely unrestricted to edit at any time (spec §9), so there is no
// separate read-only state to account for once an experiment has runs.
describe('ExperimentBuilder editing an existing experiment', () => {
  const EXISTING_SEED = {
    id: 42,
    name: 'Existing comparison',
    description: 'Already created.',
    taxonomy: 2,
    phase2_model: 'anthropic/claude-sonnet-4',
    phase2_temperature: 0.5,
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
    created_at: '2026-01-01T00:00:00Z',
  };

  let currentExperiment;

  beforeEach(() => {
    currentExperiment = { ...EXISTING_SEED };
    server.use(
      http.get('/api/experiments/42/', () => HttpResponse.json(currentExperiment)),
      http.get('/api/pipeline-runs/', () => HttpResponse.json(paginated([]))),
      http.options('/api/experiments/42/run/', () => HttpResponse.json(runOptionsResponse('help text')))
    );
  });

  it("loads the existing experiment's values into the form, not the blank defaults", async () => {
    renderApp({ initialEntries: ['/experiments/42/edit'] });

    expect(await screen.findByLabelText(/^name$/i)).toHaveValue('Existing comparison');
    expect(screen.getByLabelText(/^description$/i)).toHaveValue('Already created.');
    expect(screen.getByLabelText(/oldsmar water plant/i)).toBeChecked();
    expect(screen.getByLabelText(/shot-1/i)).toBeChecked();
    expect(screen.getByRole('button', { name: /save changes/i })).toBeInTheDocument();
    // Never the create-mode heading/button for an existing experiment.
    expect(screen.queryByRole('button', { name: /^create experiment$/i })).not.toBeInTheDocument();
  });

  it('loads a generation-only experiment with phase 3 unticked and its section hidden', async () => {
    currentExperiment = { ...EXISTING_SEED, phases: [2] };
    renderApp({ initialEntries: ['/experiments/42/edit'] });

    expect(await screen.findByRole('checkbox', { name: /phase 3 — label/i })).not.toBeChecked();
    expect(screen.queryByLabelText(/^phase 3 prompt$/i)).not.toBeInTheDocument();
  });

  it('PATCHes (not POSTs) on save, and navigates back to the detail screen', async () => {
    const user = userEvent.setup();
    let createCalled = false;
    server.use(
      http.post('/api/experiments/', () => {
        createCalled = true;
        return HttpResponse.json({ id: 999 }, { status: 201 });
      }),
      http.patch(
        '/api/experiments/42/',
        guardedExperimentWrite(async ({ request }) => {
          const body = await request.json();
          currentExperiment = { ...currentExperiment, ...body, id: 42 };
          return HttpResponse.json(currentExperiment);
        })
      )
    );

    renderApp({ initialEntries: ['/experiments/42/edit'] });
    const nameField = await screen.findByLabelText(/^name$/i);
    await user.clear(nameField);
    await user.type(nameField, 'Renamed comparison');
    await user.click(screen.getByRole('button', { name: /save changes/i }));

    // Landed back on the detail screen, showing the JUST-SAVED name.
    expect(await screen.findByRole('heading', { name: 'Renamed comparison' })).toBeInTheDocument();
    expect(createCalled).toBe(false);
  });

  // `PATCH /api/experiments/{id}/` is staff-only (spec §9) - the UI itself
  // already keeps a non-staff viewer off this form entirely (`!isStaff`
  // returns early before any field even renders), so this calls the raw
  // `apiClient` directly, the same pattern client.test.js's own CSRF/auth
  // guard tests use, to prove the MOCK's own `requireStaff` guard is
  // actually wired into this endpoint - not merely present in the file.
  it('the mock refuses PATCH /api/experiments/{id}/ for a non-staff session (guard actually wired)', async () => {
    server.use(
      http.patch('/api/experiments/42/', guardedExperimentWrite(() => HttpResponse.json(currentExperiment)))
    );
    authState.currentUserEmail = 'researcher@example.com'; // not staff

    await expect(apiClient.patch('/experiments/42/', { name: 'x' })).rejects.toMatchObject({
      response: { status: 403 },
    });
  });
});
