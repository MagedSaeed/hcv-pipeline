import { beforeEach, describe, expect, it } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { authState, server } from '../../test/server';
import { renderApp } from '../../test/renderApp';
import { RUN_POLL_INTERVAL_MS } from '../../api/queries';

function paginated(results) {
  return { count: results.length, next: null, previous: null, results };
}

// Mirrors prompts/serializers.py's PromptTemplateSerializer field set and
// prompts/models.py's PromptTemplate.Meta.ordering ((phase, name,
// -version) - the latest version of a named template sorts first). Two
// named phase-2 templates: "generation-baseline" genuinely references
// {{ taxonomy }} (so it fails to render for variant A exactly the way a
// real Run A template mistake would - see prompts/rendering.py's
// StrictUndefined note), and "phase2-safe" never mentions it, standing in
// for an ordinary edit-and-save cycle unrelated to the isolation guarantee.
const TEMPLATES_SEED = [
  {
    id: 1,
    phase: 2,
    name: 'generation-baseline',
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
    name: 'phase2-safe',
    version: 1,
    system_prompt: '',
    user_prompt: 'Write {{ n_systems }} systems.',
    output_schema: {},
    is_active: false,
    created_by: 3,
    created_at: '2026-01-01T00:00:00Z',
  },
  {
    id: 3,
    phase: 3,
    name: 'labelling-default',
    version: 1,
    system_prompt: '',
    user_prompt: 'Label {{ scenario.scenario_id }} against {{ taxonomy }}.',
    output_schema: {},
    is_active: true,
    created_by: 3,
    created_at: '2026-01-01T00:00:00Z',
  },
];

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
    is_editable: true,
    order: 1,
    created_by: 1,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  },
];

let templates;
let nextTemplateId;
let phaseRuns;
let nextPhaseRunId;

function resetFixtures() {
  templates = TEMPLATES_SEED.map((row) => ({ ...row }));
  nextTemplateId = 100;
  phaseRuns = [];
  nextPhaseRunId = 900;
}

// Stands in for prompts/rendering.py's render(): raises exactly the way
// StrictUndefined does when a variant-A template stays away from the word
// "taxonomy", and otherwise returns a trivially "rendered" string - this
// mock is not a Jinja2 engine, only enough fidelity to prove the frontend
// reacts correctly to a 200 vs. a 400 from the real shape of endpoint.
function fakeRender(text, { variantA }) {
  if (variantA && /taxonomy/i.test(text)) {
    throw new Error("'taxonomy' is undefined");
  }
  return text.replace(/\{\{\s*taxonomy\s*\}\}/gi, '<TAXONOMY TABLE>');
}

function nextVersionFor(name, phase) {
  const existingVersions = templates.filter((t) => t.name === name && t.phase === phase).map((t) => t.version);
  return Math.max(...existingVersions) + 1;
}

beforeEach(() => {
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
      return HttpResponse.json(paginated(rows));
    }),

    http.post('/api/prompts/', async ({ request }) => {
      const body = await request.json();
      const created = {
        id: nextTemplateId++,
        phase: body.phase,
        name: body.name,
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

    http.patch('/api/prompts/:id/', async ({ request, params }) => {
      const existing = templates.find((t) => t.id === Number(params.id));
      if (!existing) return HttpResponse.json({ detail: 'Not found.' }, { status: 404 });
      const body = await request.json();
      const created = {
        id: nextTemplateId++,
        phase: existing.phase,
        name: existing.name,
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

    http.post('/api/prompts/:id/preview/', async ({ request, params }) => {
      const template = templates.find((t) => t.id === Number(params.id));
      if (!template) return HttpResponse.json({ detail: 'Not found.' }, { status: 404 });
      const body = await request.json();

      if (template.phase === 2) {
        const variant = body.variant;
        if (variant === 'B' && !body.taxonomy) {
          return HttpResponse.json(
            { detail: 'Run B is the guided variant and requires a taxonomy.' },
            { status: 400 }
          );
        }
        try {
          const system = fakeRender(template.system_prompt, { variantA: variant === 'A' });
          const user = fakeRender(template.user_prompt, { variantA: variant === 'A' });
          return HttpResponse.json({
            system_prompt: system,
            user_prompt: user,
            output_schema: template.output_schema,
            leak_scan_ran: variant === 'A',
            guarded_by: variant === 'A' ? Number(body.guard_taxonomy) || 2 : null,
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
        system_prompt: fakeRender(template.system_prompt, { variantA: false }),
        user_prompt: fakeRender(template.user_prompt, { variantA: false }),
        output_schema: template.output_schema,
        leak_scan_ran: false,
        guarded_by: null,
      });
    }),

    http.get('/api/scenarios/', () => HttpResponse.json(paginated(SCENARIOS_SEED))),

    http.post('/api/phase-runs/', async ({ request }) => {
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
        experiment: body.experiment ?? null,
        n_systems: body.n_systems ?? null,
        n_scenarios_per_system: body.n_scenarios_per_system ?? null,
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

    http.get('/api/phase-runs/:id/', ({ params }) => {
      const run = phaseRuns.find((r) => r.id === Number(params.id));
      if (!run) return HttpResponse.json({ detail: 'Not found.' }, { status: 404 });
      run._polls += 1;
      // First poll: still queued (so the screen has something non-terminal
      // to show). Second poll onward: terminal - failed for the one
      // fixture model this file uses to exercise the failure path,
      // completed otherwise.
      if (run._polls >= 2 && run.status === 'queued') {
        run.status = run.model === 'trigger-failure' ? 'failed' : 'completed';
        run.progress = { done: 1, total: 1, failed: run.status === 'failed' ? 1 : 0, percent: 100 };
        run.finished_at = '2026-09-10T00:05:00Z';
        if (run.status === 'failed') {
          run.error = 'OpenRouter returned HTTP 500: upstream provider error.';
        }
      }
      // eslint-disable-next-line no-unused-vars
      const { _polls, ...wire } = run;
      return HttpResponse.json(wire);
    }),

    http.get('/api/phase-runs/:id/calls/', ({ params }) => {
      const run = phaseRuns.find((r) => r.id === Number(params.id));
      if (!run || run.status !== 'completed') return HttpResponse.json(paginated([]));
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

// Template options only exist once the list query has resolved - waiting
// for the option's own text (not merely for the <select> itself, which
// renders immediately) is what keeps every interaction below from racing
// the fetch.
async function selectTemplate(user, name) {
  const select = await screen.findByLabelText(/^template$/i);
  await within(select).findByText(new RegExp(name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
  await user.selectOptions(select, name);
}

describe('PromptEditor', () => {
  it('lists templates by phase, and makes the phase-2 variant an explicit, required choice', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/prompts'] });

    const templateSelect = await screen.findByLabelText(/^template$/i);
    expect(await within(templateSelect).findByText(/generation-baseline/)).toBeInTheDocument();
    expect(within(templateSelect).getByText(/phase2-safe/)).toBeInTheDocument();

    await selectTemplate(user, 'generation-baseline');

    // Phase 2: the variant is not merely implied by which template is
    // selected (PromptTemplate has no variant field of its own) - the
    // screen must say, in plain words, which one is being edited.
    expect(screen.getByLabelText(/variant a/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/variant b/i)).toBeInTheDocument();
    expect(screen.getByText(/no variant chosen yet/i)).toBeInTheDocument();

    await user.click(screen.getByLabelText(/variant a/i));
    expect(screen.getByText(/editing: variant a.*taxonomy withheld/i)).toBeInTheDocument();

    // Phase 3 has no variant concept at all - always sees the taxonomy.
    await user.selectOptions(screen.getByLabelText(/^phase$/i), '3');
    expect(await screen.findByText(/labelling-default/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/variant a/i)).not.toBeInTheDocument();
  });

  it('a variant-A template referencing taxonomy shows the render error, not a preview, and cannot be saved', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/prompts'] });

    await selectTemplate(user, 'generation-baseline');
    await user.click(screen.getByLabelText(/variant a/i));

    const saveButton = screen.getByRole('button', { name: /save/i });
    await user.click(saveButton);

    expect(await screen.findByText(/render failed/i)).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent(/'taxonomy' is undefined/i);
    expect(screen.queryByText(/preview ok/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/rendered system prompt/i)).not.toBeInTheDocument();

    // Blocked: resubmitting the identical, still-broken content is refused
    // outright, with the reason visible, until something actually changes.
    expect(saveButton).toBeDisabled();
    expect(screen.getByText(/fix the render error above before saving again/i)).toBeInTheDocument();

    // Changing the draft is what lifts the block - not a page reload.
    await user.type(screen.getByLabelText(/user prompt/i), ' extra');
    expect(saveButton).toBeEnabled();
  });

  it('the same template body previews successfully for variant B', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/prompts'] });

    await selectTemplate(user, 'generation-baseline');
    await user.click(screen.getByLabelText(/variant b/i));

    // The active taxonomy (id 2, from the shared fixture) is offered by
    // default - variant B requires one, but the researcher should not have
    // to hunt for "the" taxonomy every single time.
    expect(screen.getByLabelText(/^taxonomy$/i)).toHaveValue('2');

    await user.click(screen.getByRole('button', { name: /save/i }));

    expect(await screen.findByText(/preview ok/i)).toBeInTheDocument();
    expect(screen.getByText('RENDERED SYSTEM PROMPT'.toLowerCase(), { exact: false })).toBeTruthy();
    expect(screen.getByText(/<TAXONOMY TABLE>/)).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('editing a template creates a new version, and older versions are viewable', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/prompts'] });

    await selectTemplate(user, 'phase2-safe');
    await user.click(screen.getByLabelText(/variant a/i));

    const userPromptField = screen.getByLabelText(/user prompt/i);
    await user.clear(userPromptField);
    await user.type(userPromptField, 'Write a totally different system.');

    await user.click(screen.getByRole('button', { name: /save/i }));

    expect(await screen.findByText(/preview ok/i)).toBeInTheDocument();
    expect(screen.getByText('Version 2')).toBeInTheDocument();
    expect(screen.getByText('Version 1')).toBeInTheDocument();

    const version1Row = screen.getByText('Version 1').closest('li');
    await user.click(within(version1Row).getByRole('button', { name: /view/i }));

    expect(screen.getByLabelText(/user prompt/i)).toHaveValue('Write {{ n_systems }} systems.');
  });

  // These two poll the real RUN_POLL_INTERVAL_MS (4s) with REAL timers
  // rather than fake ones: userEvent's own internal pacing schedules real
  // `setTimeout`s that a fake clock engaged partway through a test never
  // reliably captures (React Query only schedules its refetch-interval
  // timer once the FIRST fetch settles, and getting a fake clock installed
  // at exactly the right moment relative to that, while still letting
  // userEvent's own clicks/typing resolve, proved consistently flaky here).
  // A real wait is slower but unambiguous - `findByText`'s own `timeout`
  // is raised past one poll interval accordingly.
  const POLL_WAIT = { timeout: RUN_POLL_INTERVAL_MS + 2000 };

  it('runs an iterate-mode phase run, borrowing an experiment and overriding counts, and states it is scratch', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/prompts'] });

    expect(await screen.findByText(/never archived/i)).toBeInTheDocument();

    await selectTemplate(user, 'phase2-safe');
    await user.click(screen.getByLabelText(/variant a/i));

    await user.selectOptions(
      screen.getByLabelText(/borrow inputs from experiment/i),
      'Baseline HCV-21 comparison'
    );
    expect(screen.getByLabelText(/override n_systems/i)).toHaveValue(1);
    expect(screen.getByLabelText(/override n_scenarios_per_system/i)).toHaveValue(1);

    await user.type(screen.getByLabelText(/^model$/i), 'openai/gpt-4o-mini');

    await user.click(screen.getByRole('button', { name: /^run$/i }));
    expect(await screen.findByText('Queued')).toBeInTheDocument();
    expect(await screen.findByText('Completed', {}, POLL_WAIT)).toBeInTheDocument();

    expect(await screen.findByText('RENDERED SYSTEM PROMPT')).toBeInTheDocument();
    expect(screen.getByText('RENDERED USER PROMPT')).toBeInTheDocument();
    expect(screen.getByText('RAW MODEL RESPONSE TEXT')).toBeInTheDocument();

    expect(phaseRuns[0].experiment).toBe(1);
    expect(phaseRuns[0].n_systems).toBe(1);
    expect(phaseRuns[0].n_scenarios_per_system).toBe(1);
  }, 15000);

  it('an iterate run that fails shows the API error', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/prompts'] });

    await selectTemplate(user, 'phase2-safe');
    await user.click(screen.getByLabelText(/variant a/i));
    await user.type(screen.getByLabelText(/^model$/i), 'trigger-failure');

    await user.click(screen.getByRole('button', { name: /^run$/i }));
    expect(await screen.findByText('Queued')).toBeInTheDocument();
    expect(await screen.findByText('Failed', {}, POLL_WAIT)).toBeInTheDocument();

    expect(screen.getByRole('alert')).toHaveTextContent(/upstream provider error/i);
  }, 15000);

  it('a POST validation failure when creating an iterate run shows the API error', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/prompts'] });

    await selectTemplate(user, 'phase2-safe');
    await user.click(screen.getByLabelText(/variant a/i));

    // The Run button stays disabled with no model entered (client-side
    // guard) - assert the reason is shown rather than nothing happening.
    expect(screen.getByRole('button', { name: /^run$/i })).toBeDisabled();
    expect(screen.getByText(/enter a model id/i)).toBeInTheDocument();
  });
});
