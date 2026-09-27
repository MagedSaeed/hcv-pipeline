import { beforeEach, describe, expect, it } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { authState, currentUser, server } from '../../test/server';
import { renderApp } from '../../test/renderApp';
import { paginate, paginated, requireCsrf, requireStaff } from '../../test/mockApi';
import { RUN_POLL_INTERVAL_MS } from '../../api/queries';

// Task D: PromptEditor.jsx no longer owns a phase/template picker - a test
// now lands directly on `/prompts/<id>/edit` (a known fixture id below) or
// `/prompts/new?phase=`, the same way the routed screen itself is reached.
const MODELS_SEED = {
  count: 3,
  results: [
    { id: 'openai/gpt-4o-mini', name: 'GPT-4o mini', pricing: { prompt: '0.0000002', completion: '0.0000006' }, context_length: 128000 },
    { id: 'trigger-failure', name: 'Trigger Failure (test double)', pricing: { prompt: '0', completion: '0' } },
    { id: 'trigger-parse-failure', name: 'Trigger Parse Failure (test double)', pricing: { prompt: '0', completion: '0' } },
  ],
};

// Four named templates, one row each, covering: a locked variant-A prompt
// that genuinely fails to render (id 1), a locked variant-A prompt that is
// safe to run to completion (id 2, for the isolation-evidence test), a
// locked variant-B prompt (id 3), and the phase-3 default (id 4). `variant`
// is the backend contract addition this task adds - "writable on create;
// copied forward on new versions" - so every phase-2 fixture below already
// carries a stored value, exercising the "fixed once saved" rule directly
// rather than only the free-choice path `/prompts/new` still has.
const TEMPLATES_SEED = [
  {
    id: 1,
    phase: 2,
    name: 'generation-baseline',
    variant: 'A',
    version: 1,
    system_prompt: '',
    user_prompt: 'Write {{ n_systems }} systems using {{ taxonomy }} as reference.',
    output_schema: {},
    is_active: true,
    created_by: 3,
    created_at: '2026-01-01T00:00:00Z',
  },
  {
    id: 2,
    phase: 2,
    name: 'phase2-variant-a',
    variant: 'A',
    version: 1,
    system_prompt: '',
    user_prompt: 'Write {{ n_systems }} safe systems.',
    output_schema: {},
    is_active: false,
    created_by: 3,
    created_at: '2026-01-01T00:00:00Z',
  },
  {
    id: 3,
    phase: 2,
    name: 'phase2-variant-b',
    variant: 'B',
    version: 1,
    system_prompt: '',
    user_prompt: 'Write {{ n_systems }} systems, variant B.',
    output_schema: {},
    is_active: false,
    created_by: 3,
    created_at: '2026-01-01T00:00:00Z',
  },
  {
    id: 4,
    phase: 3,
    name: 'labelling-default',
    variant: '',
    version: 1,
    system_prompt: '',
    user_prompt: 'Label {{ scenario.scenario_id }} against {{ taxonomy }}.',
    output_schema: {},
    is_active: true,
    created_by: 3,
    created_at: '2026-01-01T00:00:00Z',
  },
];

function generatedSystem(phaseRunId) {
  return { id: 5000 + phaseRunId, title: `Generated parking system ${phaseRunId}`, slug: `gen-${phaseRunId}`, body: 'A generated body.', origin: 'generated', generation_run: phaseRunId, is_scratch: true, is_editable: false, created_by: null, created_at: '2026-09-10T00:05:00Z', updated_at: '2026-09-10T00:05:00Z' };
}

function generatedScenarios(phaseRunId) {
  return ['sc_01', 'sc_02'].map((scenarioId, index) => ({
    id: 6000 + phaseRunId * 10 + index,
    system_description: 5000 + phaseRunId,
    scenario_id: scenarioId,
    actor: `Actor of ${scenarioId}`,
    trigger: '',
    standing_state: '',
    asset: '',
    threat: 'Spoofing',
    outcome: '',
    narrative: `Narrative of ${scenarioId}`,
    intended_hcvs: [],
    abox: null,
    origin: 'generated',
    generation_run: phaseRunId,
    is_scratch: true,
    is_editable: false,
    order: index,
  }));
}

const SCENARIOS_SEED = [
  {
    id: 10,
    system_description: 1,
    scenario_id: 'sc_01',
    actor: '',
    trigger: '',
    standing_state: '',
    asset: '',
    threat: '',
    outcome: '',
    narrative: 'A scenario.',
    intended_hcvs: [],
    abox: null,
    card: {},
    origin: 'seed',
    generation_run: null,
    is_scratch: false,
    is_editable: true,
    order: 1,
    created_by: 1,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  },
  // 54 filler scenarios (ids 11-64) so this fixture passes PAGE_SIZE=50 -
  // the phase-3 scenario picker (`useScenarios`) must offer every scenario,
  // not a first page of them.
  ...Array.from({ length: 54 }, (_, index) => ({
    id: 11 + index,
    system_description: 1,
    scenario_id: `filler_${String(11 + index).padStart(2, '0')}`,
    actor: '',
    trigger: '',
    standing_state: '',
    asset: '',
    threat: '',
    outcome: '',
    narrative: `Filler scenario ${11 + index}.`,
    intended_hcvs: [],
    abox: null,
    card: {},
    origin: 'seed',
    generation_run: null,
    is_scratch: false,
    is_editable: true,
    order: 1,
    created_by: 1,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  })),
  // Two scenarios saved by prompt test run 500 (see labellableRunsFixture).
  {
    id: 998,
    system_description: 5,
    scenario_id: 'scratch_02',
    actor: '',
    trigger: '',
    standing_state: '',
    asset: '',
    threat: 'Tampering with the pump schedule',
    outcome: '',
    narrative: 'Second scratch scenario.',
    intended_hcvs: [],
    abox: null,
    card: {},
    origin: 'generated',
    generation_run: 500,
    is_scratch: true,
    is_editable: false,
    order: 2,
    created_by: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  },
  {
    id: 999,
    system_description: 5,
    scenario_id: 'scratch_01',
    actor: '',
    trigger: '',
    standing_state: '',
    asset: '',
    threat: '',
    outcome: '',
    narrative: 'Scratch scenario from a test run.',
    intended_hcvs: [],
    abox: null,
    card: {},
    origin: 'generated',
    generation_run: 500,
    is_scratch: true,
    is_editable: false,
    order: 1,
    created_by: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  },
];

let templates;
let nextTemplateId;
let phaseRuns;
let nextPhaseRunId;
// Every write to /api/prompts/ (POST create, PATCH new-version) - proves a
// refused draft preview issues neither.
let promptWriteCount;
let mockActiveTaxonomyId;

function resetFixtures() {
  templates = TEMPLATES_SEED.map((row) => ({ ...row }));
  nextTemplateId = 100;
  phaseRuns = [];
  nextPhaseRunId = 900;
  promptWriteCount = 0;
  mockActiveTaxonomyId = 2;
}

// Stands in for prompts/rendering.py's render(): raises exactly the way
// StrictUndefined does when a variant-A template stays away from the word
// "taxonomy", and otherwise returns a trivially "rendered" string.
function fakeRender(text, { variantA }) {
  if (variantA && /taxonomy/i.test(text)) {
    throw new Error("'taxonomy' is undefined");
  }
  return text.replace(/\{\{\s*taxonomy\s*\}\}/gi, '<TAXONOMY TABLE>');
}

function renderPreview({ phase, systemPrompt, userPrompt, outputSchema, body }) {
  if (phase === 2) {
    const variant = body.variant;
    if (variant === 'B' && !body.taxonomy) {
      return HttpResponse.json(
        { detail: 'Run B is the guided variant and requires a taxonomy.' },
        { status: 400 }
      );
    }
    if (variant === 'A' && !body.guard_taxonomy && mockActiveTaxonomyId == null) {
      return HttpResponse.json(
        {
          detail:
            'Cannot verify variant A stayed unbiased: no guard taxonomy is available (no active taxonomy, and none was supplied).',
        },
        { status: 400 }
      );
    }
    try {
      const system = fakeRender(systemPrompt, { variantA: variant === 'A' });
      const user = fakeRender(userPrompt, { variantA: variant === 'A' });
      return HttpResponse.json({
        system_prompt: system,
        user_prompt: user,
        output_schema: outputSchema,
        leak_scan_ran: variant === 'A',
        guarded_by: variant === 'A' ? Number(body.guard_taxonomy) || mockActiveTaxonomyId : null,
      });
    } catch (error) {
      return HttpResponse.json({ detail: error.message }, { status: 400 });
    }
  }

  // Phase 3.
  if (!body.taxonomy) {
    return HttpResponse.json({ detail: 'Phase 3 labelling requires a taxonomy.' }, { status: 400 });
  }
  if (!body.scenario) {
    return HttpResponse.json(
      { scenario: 'Previewing a phase 3 template requires a scenario id.' },
      { status: 400 }
    );
  }
  return HttpResponse.json({
    system_prompt: fakeRender(systemPrompt, { variantA: false }),
    user_prompt: fakeRender(userPrompt, { variantA: false }),
    output_schema: outputSchema,
    leak_scan_ran: false,
    guarded_by: null,
  });
}

// A stand-in for `POST /api/prompts/check/`: a tag that never closes is a
// syntax error; a `{{ name` the phase doesn't provide is an unknown variable.
const PHASE_NAMES = {
  2: ['seed_systems', 'fewshots', 'generate_systems', 'generate_scenarios', 'n_systems', 'n_scenarios_per_system', 'target_system', 'resource'],
  3: ['scenario', 'system_description', 'fewshots', 'resource', 'taxonomy', 'hcv_table'],
};

function fakeCheck({ phase, variant, system_prompt: systemPrompt = '', user_prompt: userPrompt = '' }) {
  for (const [label, source] of [['System prompt', systemPrompt], ['User prompt', userPrompt]]) {
    if (/{{(?![\s\S]*?}})|{%(?![\s\S]*?%})/.test(source)) return { syntax_error: `${label}, line 1: unexpected end of template`, unknown_variables: [] };
  }
  const known = new Set(PHASE_NAMES[phase]);
  if (phase === 2 && variant !== 'A') ['taxonomy', 'hcv_table'].forEach((name) => known.add(name));
  const used = [...`${systemPrompt} ${userPrompt}`.matchAll(/{{\s*([A-Za-z_]\w*)/g)].map((match) => match[1]);
  return { syntax_error: null, unknown_variables: [...new Set(used.filter((name) => !known.has(name)))].sort() };
}

function nextVersionFor(name, phase) {
  const existingVersions = templates.filter((t) => t.name === name && t.phase === phase).map((t) => t.version);
  return Math.max(...existingVersions) + 1;
}

// Phase 2 few-shots: only the active phase-2 one is offered to a generation test.
const FEWSHOTS_SEED = [
  { id: 21, phase: 2, name: 'oldsmar', input_text: 'in', input_file: null, output_text: 'out', output_file: null, order: 0, is_active: true },
  { id: 22, phase: 2, name: 'retired-example', input_text: 'in', input_file: null, output_text: 'out', output_file: null, order: 1, is_active: false },
  { id: 23, phase: 3, name: 'oldsmar-sc_01', input_text: 'in', input_file: null, output_text: 'out', output_file: null, order: 0, is_active: true },
];

let labellableRunsFixture = [];
let previousRunsFixture = [];
let scenariosFixture = SCENARIOS_SEED;

beforeEach(() => {
  previousRunsFixture = [];
  labellableRunsFixture = [
    { id: 500, pipeline_run: null, phase: 2, variant: 'A', model: 'openai/gpt-4o-mini', temperature: 0, prompt_template: 1, taxonomy: 2, source_phase_run: null, experiment: null, n_systems: null, n_scenarios_per_system: null, status: 'completed', error: '', progress: { done: 1, total: 1, failed: 0, percent: 100 }, total_children: 1, completed_children: 1, failed_children: 0, leak_scan_ran: true, guarded_by: [2], started_at: null, finished_at: '2026-09-10T00:05:00Z' },
  ];
  scenariosFixture = SCENARIOS_SEED;
  authState.currentUserEmail = 'admin@example.com'; // staff - see server.js SEED_USERS
  resetFixtures();

  server.use(
    http.get('/api/prompts/', ({ request }) => {
      const url = new URL(request.url);
      const phaseParam = url.searchParams.get('phase');
      let rows = templates;
      if (phaseParam !== null) rows = rows.filter((t) => t.phase === Number(phaseParam));
      rows = [...rows].sort(
        (a, b) => a.phase - b.phase || a.name.localeCompare(b.name) || b.version - a.version
      );
      return HttpResponse.json(paginate(rows, url));
    }),

    http.get('/api/models/', () => HttpResponse.json(MODELS_SEED)),

    http.post('/api/prompts/', async ({ request }) => {
      const staffRefusal = requireStaff(currentUser());
      if (staffRefusal) return staffRefusal;
      const csrfRefusal = requireCsrf(request);
      if (csrfRefusal) return csrfRefusal;
      promptWriteCount += 1;
      const body = await request.json();
      const created = {
        id: nextTemplateId++,
        phase: body.phase,
        name: body.name,
        variant: body.variant ?? '',
        version: 1,
        system_prompt: body.system_prompt ?? '',
        user_prompt: body.user_prompt ?? '',
        output_schema: body.output_schema ?? {},
        is_active: false,
        created_by: 3,
        created_at: '2026-09-10T00:00:00Z',
      };
      templates.push(created);
      return HttpResponse.json(created, { status: 201 });
    }),

    // `PATCH` never mutates the addressed row - it creates and returns the
    // NEXT version, copying `variant` forward (never accepting a new one -
    // the contract's "copied forward on new versions").
    http.patch('/api/prompts/:id/', async ({ request, params }) => {
      const staffRefusal = requireStaff(currentUser());
      if (staffRefusal) return staffRefusal;
      const csrfRefusal = requireCsrf(request);
      if (csrfRefusal) return csrfRefusal;
      promptWriteCount += 1;
      const existing = templates.find((t) => t.id === Number(params.id));
      if (!existing) return HttpResponse.json({ detail: 'Not found.' }, { status: 404 });
      const body = await request.json();
      const created = {
        id: nextTemplateId++,
        phase: existing.phase,
        name: existing.name,
        variant: existing.variant,
        version: nextVersionFor(existing.name, existing.phase),
        system_prompt: body.system_prompt ?? existing.system_prompt,
        user_prompt: body.user_prompt ?? existing.user_prompt,
        output_schema: body.output_schema ?? existing.output_schema,
        is_active: false,
        created_by: 3,
        created_at: '2026-09-10T00:00:00Z',
      };
      templates.push(created);
      return HttpResponse.json(created, { status: 201 });
    }),

    http.post('/api/prompts/check/', async ({ request }) => {
      const staffRefusal = requireStaff(currentUser());
      if (staffRefusal) return staffRefusal;
      const csrfRefusal = requireCsrf(request);
      if (csrfRefusal) return csrfRefusal;
      return HttpResponse.json(fakeCheck(await request.json()));
    }),

    http.post('/api/prompts/:id/preview/', async ({ request, params }) => {
      const staffRefusal = requireStaff(currentUser());
      if (staffRefusal) return staffRefusal;
      const csrfRefusal = requireCsrf(request);
      if (csrfRefusal) return csrfRefusal;
      const template = templates.find((t) => t.id === Number(params.id));
      if (!template) return HttpResponse.json({ detail: 'Not found.' }, { status: 404 });
      const body = await request.json();
      const isDraft = typeof body.user_prompt === 'string';
      return renderPreview({
        phase: template.phase,
        systemPrompt: isDraft ? (body.system_prompt ?? '') : template.system_prompt,
        userPrompt: isDraft ? body.user_prompt : template.user_prompt,
        outputSchema: isDraft ? (body.output_schema ?? {}) : template.output_schema,
        body,
      });
    }),

    http.post('/api/prompts/preview/', async ({ request }) => {
      const staffRefusal = requireStaff(currentUser());
      if (staffRefusal) return staffRefusal;
      const csrfRefusal = requireCsrf(request);
      if (csrfRefusal) return csrfRefusal;
      const body = await request.json();
      if (!body.phase) {
        return HttpResponse.json(
          { phase: 'Previewing a template with no id requires phase.' },
          { status: 400 }
        );
      }
      if (typeof body.user_prompt !== 'string') {
        return HttpResponse.json(
          { user_prompt: 'Previewing a template with no id requires user_prompt.' },
          { status: 400 }
        );
      }
      return renderPreview({
        phase: body.phase,
        systemPrompt: body.system_prompt ?? '',
        userPrompt: body.user_prompt,
        outputSchema: body.output_schema ?? {},
        body,
      });
    }),

    // `?phase_run=` asks for what an iterate run saved (the Parsed output view).
    http.get('/api/scenarios/', ({ request }) => {
      const phaseRun = new URL(request.url).searchParams.get('phase_run');
      if (phaseRun) return HttpResponse.json(paginate(generatedScenarios(Number(phaseRun)), request));
      return HttpResponse.json(paginate(scenariosFixture, request));
    }),
    http.get('/api/system-descriptions/', ({ request }) =>
      HttpResponse.json(
        paginate(
          [
            { ...generatedSystem(0), id: 1, title: 'Oldsmar water plant', origin: 'seed', generation_run: null, is_scratch: false },
            { ...generatedSystem(0), id: 2, title: 'Hand-written tram depot', origin: 'manual', generation_run: null, is_scratch: false },
            { ...generatedSystem(0), id: 5, title: 'Scratch parking system', generation_run: 500 },
            ...phaseRuns.map((run) => generatedSystem(run.id)),
          ],
          request
        )
      )
    ),

    http.get('/api/fewshots/', ({ request }) => {
      const phaseParam = new URL(request.url).searchParams.get('phase');
      const rows = phaseParam ? FEWSHOTS_SEED.filter((row) => row.phase === Number(phaseParam)) : FEWSHOTS_SEED;
      return HttpResponse.json(paginate(rows, request));
    }),

    // Finished generation runs a labelling test can pick from; 500 saved `scratch_01`.
    http.get('/api/phase-runs/', ({ request }) => {
      // `?prompt_name=` is the Previous runs list; empty unless a test sets it.
      if (new URL(request.url).searchParams.get('prompt_name')) {
        return HttpResponse.json(paginate(previousRunsFixture, request));
      }
      return HttpResponse.json(paginate(labellableRunsFixture, request));
    }),

    http.post('/api/phase-runs/', async ({ request }) => {
      const staffRefusal = requireStaff(currentUser());
      if (staffRefusal) return staffRefusal;
      const csrfRefusal = requireCsrf(request);
      if (csrfRefusal) return csrfRefusal;
      const body = await request.json();
      if (!body.model) {
        return HttpResponse.json({ model: ['This field is required.'] }, { status: 400 });
      }
      const created = {
        id: nextPhaseRunId++,
        pipeline_run: null,
        phase: body.phase,
        variant: body.variant ?? '',
        model: body.model,
        temperature: body.temperature ?? 0,
        prompt_template: body.prompt_template,
        taxonomy: body.taxonomy,
        source_phase_run: body.source_phase_run ?? null,
        scenarios: body.scenarios ?? null,
        experiment: body.experiment ?? null,
        n_systems: body.n_systems ?? null,
        n_scenarios_per_system: body.n_scenarios_per_system ?? null,
        seed_systems: body.seed_systems ?? null,
        fewshots: body.fewshots ?? null,
        target_system: body.target_system ?? null,
        generate_systems: body.generate_systems ?? null,
        generate_scenarios: body.generate_scenarios ?? null,
        status: 'queued',
        error: '',
        progress: { done: 0, total: 1, failed: 0, percent: 0 },
        total_children: 1,
        completed_children: 0,
        failed_children: 0,
        leak_scan_ran: false,
        guarded_by: [],
        started_at: '2026-09-10T00:00:00Z',
        finished_at: null,
        _polls: 0,
      };
      phaseRuns.push(created);
      return HttpResponse.json(created, { status: 202 });
    }),

    http.delete('/api/phase-runs/:id/', async ({ request, params }) => {
      const staffRefusal = requireStaff(currentUser());
      if (staffRefusal) return staffRefusal;
      const csrfRefusal = requireCsrf(request);
      if (csrfRefusal) return csrfRefusal;
      const id = Number(params.id);
      const index = phaseRuns.findIndex((r) => r.id === id);
      if (index === -1) return HttpResponse.json({ detail: 'Not found.' }, { status: 404 });
      const run = phaseRuns[index];
      if (run.status === 'queued' || run.status === 'running') {
        return HttpResponse.json(
          {
            detail: `Phase run ${id} is ${run.status} and cannot be deleted while a worker may still hold it.`,
            code: 'run_in_flight',
          },
          { status: 409 }
        );
      }
      phaseRuns.splice(index, 1);
      return new HttpResponse(null, { status: 204 });
    }),

    http.get('/api/phase-runs/:id/', ({ params }) => {
      const run = phaseRuns.find((r) => r.id === Number(params.id));
      if (!run) return HttpResponse.json({ detail: 'Not found.' }, { status: 404 });
      run._polls += 1;
      if (run._polls >= 2 && run.status === 'queued') {
        run.status = run.model === 'trigger-failure' ? 'failed' : 'completed';
        run.progress = { done: 1, total: 1, failed: run.status === 'failed' ? 1 : 0, percent: 100 };
        run.finished_at = '2026-09-10T00:05:00Z';
        if (run.status === 'failed') {
          run.error = 'OpenRouter returned HTTP 500: upstream provider error.';
        } else {
          run.leak_scan_ran = run.phase === 2 && run.variant === 'A';
          run.guarded_by = run.leak_scan_ran ? [Number(run.taxonomy) || 2] : [];
        }
      }
      // eslint-disable-next-line no-unused-vars
      const { _polls, ...wire } = run;
      return HttpResponse.json(wire);
    }),

    http.get('/api/phase-runs/:id/calls/', ({ params }) => {
      const run = phaseRuns.find((r) => r.id === Number(params.id));
      if (!run || (run.status !== 'completed' && run.status !== 'failed')) {
        return HttpResponse.json(paginated([]));
      }
      // 'trigger-parse-failure' stands in for a call whose model DID
      // respond (the run still completes) but whose output didn't parse -
      // exercises the parse-failure header, the finish_reason==='length'
      // note, and "Raw response" opening by default.
      if (run.model === 'trigger-parse-failure') {
        return HttpResponse.json(
          paginated([
            {
              id: 9002,
              sequence: 1,
              scenario: null,
              rendered_system_prompt: 'RENDERED SYSTEM PROMPT',
              rendered_user_prompt: 'RENDERED USER PROMPT',
              request_json: { model: run.model, some_key: 'some request value' },
              response_json: { some_key: 'some response value' },
              raw_response: 'RAW PARSE-FAILURE RESPONSE TEXT',
              parsed_ok: false,
              parse_error: 'Missing required key "hcv_id".',
              unknown_hcv_ids: [],
              stray_scenarios: [],
              http_status: 200,
              transport_error: '',
              model: run.model,
              finish_reason: 'length',
              prompt_tokens: 30,
              completion_tokens: 400,
              cost: 0.02,
              latency_ms: 900,
              leak_scan_ran: run.variant === 'A',
              guarded_by: run.variant === 'A' ? 2 : null,
              created_at: '2026-09-10T00:05:00Z',
            },
          ])
        );
      }
      return HttpResponse.json(
        paginated([
          {
            id: 9001,
            sequence: 1,
            scenario: null,
            rendered_system_prompt: 'RENDERED SYSTEM PROMPT',
            rendered_user_prompt: 'RENDERED USER PROMPT',
            request_json: {},
            response_json: {},
            raw_response: 'RAW MODEL RESPONSE TEXT',
            parsed_ok: true,
            parse_error: '',
            unknown_hcv_ids: [],
            stray_scenarios: [],
            http_status: 200,
            transport_error: '',
            model: run.model,
            finish_reason: 'stop',
            prompt_tokens: 10,
            completion_tokens: 20,
            cost: 0.01,
            latency_ms: 500,
            leak_scan_ran: run.variant === 'A',
            guarded_by: run.variant === 'A' ? 2 : null,
            created_at: '2026-09-10T00:05:00Z',
          },
        ])
      );
    })
  );
});

// The iterate panel's model field is a ModelPicker, not a plain text input -
// open it, type the id to narrow the list to one row, then click that row.
async function pickModel(user, modelId) {
  const field = await screen.findByLabelText(/^model$/i);
  await user.click(field);
  await user.type(field, modelId);
  const option = await screen.findByTestId(`model-option-${modelId}`);
  await user.click(option);
}

async function openTestPanel(user) {
  await user.click(await screen.findByRole('button', { name: /test this prompt/i }));
}

describe('PromptEditor', () => {
  it('shows a stored variant fixed for an existing phase-2 prompt', async () => {
    renderApp({ initialEntries: ['/prompts/1/edit'] });

    expect(await screen.findByText(/editing: variant a.*taxonomy withheld/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/variant a/i)).toBeDisabled();
    expect(screen.getByText(/variant is fixed for an existing prompt/i)).toBeInTheDocument();
  });

  it('phase 3 has no variant concept at all', async () => {
    renderApp({ initialEntries: ['/prompts/4/edit'] });

    expect(await screen.findByLabelText(/user prompt/i)).toBeInTheDocument();
    expect(screen.queryByRole('radio', { name: /^variant a/i, hidden: true })).not.toBeInTheDocument();
  });

  it('a variant-A template referencing taxonomy fails to preview, and Save warns before saving it', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/prompts/1/edit'] });

    const previewButton = await screen.findByRole('button', { name: /^preview$/i });
    const saveButton = screen.getByRole('button', { name: /^save$/i });
    expect(saveButton).toBeDisabled(); // nothing changed yet
    expect(screen.getByText(/no changes to save/i)).toBeInTheDocument();

    await user.click(previewButton);
    expect(await screen.findByText(/could not be rendered/i)).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent(/'taxonomy' is undefined/i);
    expect(screen.queryByText(/preview ok/i)).not.toBeInTheDocument();

    // Save checks without rendering: `taxonomy` is unknown to variant A, so it
    // warns and waits for the user's choice.
    await user.type(screen.getByLabelText(/user prompt/i), ' extra');
    expect(saveButton).toBeEnabled();
    await user.click(saveButton);
    const warning = await screen.findByRole('alertdialog');
    expect(warning).toHaveTextContent(/unknown variable: taxonomy/i);
    expect(promptWriteCount).toBe(0);

    await user.click(within(warning).getByRole('button', { name: /keep editing/i }));
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
    expect(promptWriteCount).toBe(0);

    await user.click(saveButton);
    await user.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: /save anyway/i }));
    await waitFor(() => expect(promptWriteCount).toBe(1));
  });

  it('Save refuses a template with a syntax error and says where it is', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/prompts/4/edit'] });

    await user.type(await screen.findByLabelText(/user prompt/i), ' {{{{ scenario.actor');
    await user.click(screen.getByRole('button', { name: /^save$/i }));

    expect(await screen.findByText(/not saved\. user prompt, line 1/i)).toBeInTheDocument();
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
    expect(promptWriteCount).toBe(0);
  });

  it('a labelling prompt saves without choosing a scenario to preview with', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/prompts/4/edit'] });

    await user.type(await screen.findByLabelText(/user prompt/i), ' Be brief.');
    expect(screen.getByText(/choose a scenario to preview/i)).toBeInTheDocument();
    const saveButton = screen.getByRole('button', { name: /^save$/i });
    expect(saveButton).toBeEnabled();

    await user.click(saveButton);
    await waitFor(() => expect(promptWriteCount).toBe(1));
  });

  it('an existing variant-B template previews successfully with the active taxonomy preselected', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/prompts/3/edit'] });

    await screen.findByText(/editing: variant b/i);
    expect(screen.getByLabelText(/^taxonomy$/i)).toHaveTextContent('hcv-21 v2 (active)');

    const saveButton = screen.getByRole('button', { name: /^save$/i });
    expect(saveButton).toBeDisabled();
    await user.click(screen.getByRole('button', { name: /^preview$/i }));

    expect(await screen.findByText(/preview ok/i)).toBeInTheDocument();
    expect(saveButton).toBeDisabled(); // still nothing to save
    expect(promptWriteCount).toBe(0);
  });

  it('editing a template body creates a new version once previewed cleanly, and "Test this prompt" reflects it is saved', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/prompts/3/edit'] });

    const userPromptField = await screen.findByLabelText(/user prompt/i);
    await user.type(userPromptField, ' Updated.');
    await user.click(screen.getByRole('button', { name: /^preview$/i }));
    expect(await screen.findByText(/preview ok/i)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => expect(promptWriteCount).toBe(1));
    expect(templates.find((t) => t.name === 'phase2-variant-b' && t.version === 2)).toMatchObject({
      user_prompt: 'Write {{ n_systems }} systems, variant B. Updated.',
    });

    // Proof the route/id actually followed the new version, not just that a
    // write happened: with nothing left unsaved, the test button now reads
    // "Run test", not "Save & test".
    await openTestPanel(user);
    expect(await screen.findByRole('button', { name: /^run test$/i })).toBeInTheDocument();
  });

  it('creates a brand-new phase-2 prompt with a freely-chosen variant through the id-less preview route', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/prompts/new?phase=2'] });

    await user.type(await screen.findByLabelText(/^name$/i), 'brand-new-template');
    await user.click(screen.getByLabelText(/variant a/i));
    expect(screen.getByLabelText(/variant a/i)).toBeEnabled(); // free choice - nothing stored yet
    await user.type(screen.getByLabelText(/user prompt/i), 'Make {{ n_systems }} systems.');

    // Save works straight away: it checks the draft itself before saving.
    const saveButton = screen.getByRole('button', { name: /^save$/i });
    expect(saveButton).toBeEnabled();
    expect(promptWriteCount).toBe(0);

    await user.click(saveButton);

    expect(await screen.findByText(/editing.*brand-new-template/i)).toBeInTheDocument();
    expect(templates.find((t) => t.name === 'brand-new-template')).toMatchObject({ version: 1, variant: 'A' });
    expect(promptWriteCount).toBe(1);
  });

  it('"Test this prompt" opens a side panel that never blocks the editor, closed by default', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/prompts/3/edit'] });
    await screen.findByLabelText(/user prompt/i);

    const panel = screen.getByTestId('side-panel');
    expect(panel).not.toBeVisible();
    expect(screen.queryByTestId('test-status-dot')).not.toBeInTheDocument();

    await openTestPanel(user);
    expect(panel).toBeVisible();
    expect(await screen.findByText(/never part of an experiment/i)).toBeInTheDocument();
    // Nothing about the editor itself became unusable - no backdrop, no
    // focus trap.
    expect(screen.getByRole('button', { name: /^preview$/i })).toBeVisible();
    expect(screen.getByLabelText(/user prompt/i)).toBeEnabled();

    await user.click(screen.getByRole('button', { name: /^close$/i }));
    expect(panel).not.toBeVisible();
    // Hidden, not unmounted - its own content is still in the DOM.
    expect(screen.getByText(/never part of an experiment/i)).toBeInTheDocument();
  });

  it('with unsaved changes the button reads "Save & test", saves a new version, then runs it', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/prompts/3/edit'] });

    await user.type(await screen.findByLabelText(/user prompt/i), ' Edited for a test run.');
    await user.click(screen.getByRole('button', { name: /^preview$/i }));
    expect(await screen.findByText(/preview ok/i)).toBeInTheDocument();

    await openTestPanel(user);
    expect(await screen.findByText(/never part of an experiment/i)).toBeInTheDocument();
    const seeds = screen.getByRole('group', { name: 'Seed systems' });
    await user.click(await within(seeds).findByRole('checkbox', { name: /hand-written tram depot/i }));
    await pickModel(user, 'openai/gpt-4o-mini');

    const button = screen.getByRole('button', { name: /^save & test$/i });
    await user.click(button);

    expect(await screen.findByText('Queued')).toBeInTheDocument();
    expect(await screen.findByText('Completed', {}, { timeout: RUN_POLL_INTERVAL_MS + 2000 })).toBeInTheDocument();

    expect(promptWriteCount).toBe(1);
    const newVersion = templates.find((t) => t.name === 'phase2-variant-b' && t.version === 2);
    expect(newVersion).toBeTruthy();
    expect(phaseRuns[0].prompt_template).toBe(newVersion.id);
    expect(phaseRuns[0].experiment).toBeNull();
    expect(phaseRuns[0]).toMatchObject({ seed_systems: [1], fewshots: [21] });
  }, 15000);

  it('with no unsaved changes the button reads "Run test" and runs the version being edited without saving', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/prompts/2/edit'] });

    await openTestPanel(user);
    await pickModel(user, 'openai/gpt-4o-mini');
    await user.click(screen.getByRole('button', { name: /^run test$/i }));

    expect(await screen.findByText('Queued')).toBeInTheDocument();
    // The "Test this prompt" button carries a status dot while a test runs.
    expect(screen.getByTestId('test-status-dot')).toBeInTheDocument();
    expect(await screen.findByText('Completed', {}, { timeout: RUN_POLL_INTERVAL_MS + 2000 })).toBeInTheDocument();
    expect(screen.queryByTestId('test-status-dot')).not.toBeInTheDocument();

    expect(promptWriteCount).toBe(0);
    expect(phaseRuns[0].prompt_template).toBe(2);
    expect(screen.getByText(/isolation verified/i)).toBeInTheDocument();
    expect(screen.getByText(/checked against hcv-21 v2/i)).toBeInTheDocument();
  }, 15000);

  it('a generation test preselects every example, allows none, and sends every input', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/prompts/2/edit'] });

    await openTestPanel(user);
    const panel = screen.getByTestId('side-panel');
    const seeds = within(panel).getByRole('group', { name: 'Seed systems' });
    const fewshots = within(panel).getByRole('group', { name: 'Few-shot examples' });
    expect(await within(seeds).findByText('2 of 2 selected')).toBeInTheDocument();
    expect(await within(fewshots).findByText('1 of 1 selected')).toBeInTheDocument();
    // Generated systems, inactive and phase 3 few-shots are never offered.
    expect(within(seeds).queryByText(/scratch parking system/i)).not.toBeInTheDocument();
    expect(within(fewshots).queryByText(/retired-example|oldsmar-sc_01/)).not.toBeInTheDocument();
    expect(within(panel).getByText(/examples: 2 seed systems and 1 few-shot example/i)).toBeInTheDocument();

    await user.click(within(fewshots).getByRole('button', { name: /^clear$/i }));
    expect(within(fewshots).getByText(/no few-shots will be included/i)).toBeInTheDocument();
    expect(within(panel).getByText(/2 seed systems and 0 few-shot examples/i)).toBeInTheDocument();

    await user.click(within(panel).getByRole('checkbox', { name: /new systems/i }));
    expect(within(panel).getByText(/choose a target system/i)).toBeInTheDocument();
    await user.selectOptions(within(panel).getByLabelText(/target system/i), 'Oldsmar water plant');
    await pickModel(user, 'openai/gpt-4o-mini');
    await user.click(within(panel).getByRole('button', { name: /^run test$/i }));

    expect(await within(panel).findByText(/generation test run · 2 seed systems · 0 few-shot examples/i)).toBeInTheDocument();
    expect(phaseRuns[0]).toMatchObject({
      experiment: null,
      seed_systems: [1, 2],
      fewshots: [],
      generate_systems: false,
      generate_scenarios: true,
      target_system: 1,
      n_systems: 1,
      n_scenarios_per_system: 3,
    });
  });

  it('copies an experiment’s inputs into the form once, without linking the run to it', async () => {
    const user = userEvent.setup();
    server.use(
      http.get('/api/experiments/', ({ request }) =>
        HttpResponse.json(
          paginate(
            [{ id: 7, name: 'Targeted pilot', taxonomy: 2, seed_systems: [2, 5], fewshots: [22, 21], generate_systems: false, generate_scenarios: true, target_system: 2, n_systems: 80, n_scenarios_per_system: 5 }],
            request
          )
        )
      )
    );
    renderApp({ initialEntries: ['/prompts/2/edit'] });

    await openTestPanel(user);
    const panel = screen.getByTestId('side-panel');
    const seeds = within(panel).getByRole('group', { name: 'Seed systems' });
    expect(await within(seeds).findByText('2 of 2 selected')).toBeInTheDocument();
    await user.selectOptions(await within(panel).findByLabelText(/copy inputs from an experiment/i), 'Targeted pilot');
    await user.click(within(panel).getByRole('button', { name: /^copy$/i }));

    // The generated seed and the inactive few-shot are dropped; counts clamp to the test cap.
    expect(within(seeds).getByText('1 of 2 selected')).toBeInTheDocument();
    expect(within(panel).getByRole('checkbox', { name: /new systems/i })).not.toBeChecked();
    expect(within(panel).getByLabelText(/target system/i)).toHaveValue('2');
    expect(within(panel).getByLabelText(/systems to generate/i)).toHaveValue(50);
    expect(within(panel).getByLabelText(/scenarios per system/i)).toHaveValue(5);
    expect(within(panel).getByText(/copied from “targeted pilot”/i)).toBeInTheDocument();

    await pickModel(user, 'openai/gpt-4o-mini');
    await user.click(within(panel).getByRole('button', { name: /^run test$/i }));
    await waitFor(() => expect(phaseRuns).toHaveLength(1));
    expect(phaseRuns[0]).toMatchObject({ experiment: null, seed_systems: [2], fewshots: [21], target_system: 2, n_systems: 50 });
  });

  it('lists previous runs of the prompt and shows one when picked', async () => {
    const user = userEvent.setup();
    previousRunsFixture = [
      { ...labellableRunsFixture[0], id: 501, prompt_template: 2, total_cost: 0.0123, finished_at: '2026-09-11T00:00:00Z' },
    ];
    renderApp({ initialEntries: ['/prompts/2/edit'] });

    await openTestPanel(user);
    const panel = screen.getByTestId('side-panel');
    await user.click(await within(panel).findByRole('button', { name: /previous runs of this prompt/i }));
    expect(await within(panel).findByText(/\$0\.0123/)).toBeInTheDocument();
    await user.click(within(panel).getByRole('button', { name: /^show$/i }));
    expect(await within(panel).findByRole('button', { name: /^showing$/i })).toBeDisabled();
  });

  it('shows the parsed output of a successful call: the generated system beside its scenarios as tabs', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/prompts/2/edit'] });

    await openTestPanel(user);
    await pickModel(user, 'openai/gpt-4o-mini');
    await user.click(screen.getByRole('button', { name: /^run test$/i }));
    expect(await screen.findByText('Completed', {}, { timeout: RUN_POLL_INTERVAL_MS + 2000 })).toBeInTheDocument();

    const panel = screen.getByTestId('side-panel');
    expect(await within(panel).findByText(/generated parking system/i)).toBeInTheDocument();
    expect(within(panel).getByText('Actor of sc_01')).toBeInTheDocument();
    await user.click(within(panel).getByRole('tab', { name: 'sc_02' }));
    expect(within(panel).getByText('Actor of sc_02')).toBeInTheDocument();
    // Parsed output opens by default on success; Raw response stays closed.
    expect(within(panel).queryByText('RAW MODEL RESPONSE TEXT')).not.toBeInTheDocument();
  }, 15000);

  it('shows per-call detail: header, parse failure, the output-limit note, and Raw response open by default', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/prompts/2/edit'] });

    await openTestPanel(user);
    await pickModel(user, 'trigger-parse-failure');
    await user.click(screen.getByRole('button', { name: /^run test$/i }));

    expect(await screen.findByText('Queued')).toBeInTheDocument();
    expect(await screen.findByText('Completed', {}, { timeout: RUN_POLL_INTERVAL_MS + 2000 })).toBeInTheDocument();

    expect(await screen.findByText(/parse failed/i)).toBeInTheDocument();
    expect(screen.getByText(/parse error: missing required key/i)).toBeInTheDocument();
    expect(screen.getByText(/model stopped at its output limit/i)).toBeInTheDocument();

    // Raw response opens by default because parsing failed - its text is
    // visible with no click.
    expect(screen.getByText('RAW PARSE-FAILURE RESPONSE TEXT')).toBeInTheDocument();
    // Every other section stays closed until asked for.
    expect(screen.queryByText(/"some_key": "some response value"/)).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /full response json/i }));
    expect(await screen.findByText(/"some_key": "some response value"/)).toBeInTheDocument();
  }, 15000);

  it('an iterate run that fails shows the API error, and a missing model keeps Run test disabled with a reason', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/prompts/2/edit'] });

    await openTestPanel(user);
    expect(screen.getByRole('button', { name: /^run test$/i })).toBeDisabled();
    expect(screen.getByText(/choose a model to run/i)).toBeInTheDocument();

    await pickModel(user, 'trigger-failure');
    await user.click(screen.getByRole('button', { name: /^run test$/i }));

    expect(await screen.findByText('Queued')).toBeInTheDocument();
    expect(await screen.findByText('Failed', {}, { timeout: RUN_POLL_INTERVAL_MS + 2000 })).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent(/upstream provider error/i);
    // The status dot turns red once the last run failed.
    expect(screen.getByTestId('test-status-dot')).toHaveClass('bg-red-500');
  }, 15000);

  it('a completed run can be deleted, and a 409 while still queued keeps it with the API message', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/prompts/2/edit'] });

    await openTestPanel(user);
    await pickModel(user, 'openai/gpt-4o-mini');
    await user.click(screen.getByRole('button', { name: /^run test$/i }));
    expect(await screen.findByText('Queued')).toBeInTheDocument();

    const runId = phaseRuns[0].id;
    await user.click(screen.getByRole('button', { name: /delete this run/i }));
    const dialog = screen.getByRole('alertdialog');
    await user.click(within(dialog).getByRole('button', { name: /^confirm$/i }));
    expect(await screen.findByText(/cannot be deleted while a worker may still hold it/i)).toBeInTheDocument();
    expect(phaseRuns.find((r) => r.id === runId)).toBeDefined();

    // The dialog stays open after a failed attempt (no re-click of the
    // trigger needed) - confirm again once the run has actually finished.
    expect(await screen.findByText('Completed', {}, { timeout: RUN_POLL_INTERVAL_MS + 2000 })).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: /^confirm$/i }));
    await waitFor(() => expect(screen.queryByText(/generation test run/i)).not.toBeInTheDocument());
    expect(phaseRuns.find((r) => r.id === runId)).toBeUndefined();
  }, 15000);

  it('phase 3 preview: a grouped scenario picker with details, provenance and every page of scenarios', async () => {
    const user = userEvent.setup();
    let previewBody = null;
    server.use(
      http.post('/api/prompts/:id/preview/', async ({ request }) => {
        previewBody = await request.json();
        return HttpResponse.json({ system_prompt: '', user_prompt: 'ok', output_schema: {}, leak_scan_ran: false, guarded_by: null });
      })
    );
    renderApp({ initialEntries: ['/prompts/4/edit'] });

    const picker = await screen.findByRole('group', { name: 'Scenario' });
    expect(await within(picker).findByRole('button', { name: /seed & hand-written scenarios/i })).toBeInTheDocument();
    const runGroup = within(picker).getByRole('button', { name: /prompt test run · variant a/i });
    expect(runGroup).toHaveTextContent(/2 scenarios · generation-baseline v1 · openai\/gpt-4o-mini/i);
    // The last page of seed scenarios is reachable.
    expect(within(picker).getByRole('radio', { name: /filler_64 · oldsmar water plant/i })).toBeInTheDocument();

    // Details show the full text, and generated rows explain how they were made.
    await user.click(within(picker).getByRole('button', { name: 'Details for scratch_02' }));
    expect(within(picker).getByText('Second scratch scenario.')).toBeInTheDocument();
    expect(within(picker).getByRole('button', { name: /how was this generated/i })).toBeInTheDocument();

    const previewButton = screen.getByRole('button', { name: /^preview$/i });
    expect(previewButton).toBeDisabled();
    expect(screen.getByText(/choose a scenario to preview/i)).toBeInTheDocument();
    await user.click(within(picker).getByRole('radio', { name: /scratch_02 · scratch parking system/i }));
    expect(within(picker).getByText(/selected: scratch_02/i)).toBeInTheDocument();
    await user.click(previewButton);
    expect(await screen.findByText(/preview ok/i)).toBeInTheDocument();
    expect(previewBody.scenario).toBe(998);
  });

  it('a labelling test runs on the chosen scenarios, seed and generated mixed', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/prompts/4/edit'] });
    await screen.findByLabelText(/user prompt/i);
    await openTestPanel(user);
    const panel = screen.getByTestId('side-panel');

    const picker = await within(panel).findByRole('group', { name: /scenarios to label/i });
    expect(within(panel).getByRole('button', { name: /^run test$/i })).toBeDisabled();
    expect(within(panel).getByText(/choose at least one scenario to label/i)).toBeInTheDocument();

    const groupBox = await within(picker).findByRole('checkbox', { name: /select all in prompt test run · variant a/i });
    await user.click(groupBox);
    expect(groupBox).toBeChecked();
    expect(within(picker).getByText('2 selected')).toBeInTheDocument();
    await user.click(within(picker).getByRole('checkbox', { name: /scratch_01/ }));
    expect(groupBox).toBePartiallyChecked();

    await user.click(within(picker).getByRole('checkbox', { name: /^sc_01 · oldsmar water plant/i }));
    expect(within(picker).getByText('2 selected')).toBeInTheDocument();

    await pickModel(user, 'openai/gpt-4o-mini');
    await user.click(within(panel).getByRole('button', { name: /^run test$/i }));

    expect(await within(panel).findByText(/labelling test run · 2 scenarios/i)).toBeInTheDocument();
    expect(phaseRuns[0].scenarios).toEqual([998, 10]);
    expect(phaseRuns[0].source_phase_run).toBeNull();

    await user.click(within(picker).getByRole('button', { name: /^clear$/i }));
    expect(within(picker).getByText('0 selected')).toBeInTheDocument();
  });

  it('a labelling test with no scenarios anywhere explains what to do first', async () => {
    const user = userEvent.setup();
    labellableRunsFixture = [];
    scenariosFixture = [];
    renderApp({ initialEntries: ['/prompts/4/edit'] });

    await screen.findByLabelText(/user prompt/i);
    await openTestPanel(user);
    const panel = screen.getByTestId('side-panel');
    expect(await within(panel).findByText(/no scenarios to label yet/i)).toBeInTheDocument();
    expect(within(panel).queryByRole('button', { name: /^run test$/i })).not.toBeInTheDocument();
  });
});
