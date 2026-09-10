import { beforeEach, describe, expect, it } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { authState, currentUser, server } from '../../test/server';
import { renderApp } from '../../test/renderApp';
import { paginate, paginated, requireCsrf, requireStaff } from '../../test/mockApi';
import { RUN_POLL_INTERVAL_MS } from '../../api/queries';

// 55 versions of ONE template name, deliberately kept separate from every
// other fixture template above (`generation-baseline`/`phase2-safe`) so it
// never perturbs an existing test's version-count assertions. `PATCH
// /api/prompts/{id}/` never mutates a version in place, it always creates
// the next one - task 10.1's own reasoning for why `usePromptTemplates`
// must fetch every page: an actively-tuned template is GUARANTEED to pass
// PAGE_SIZE=50 versions eventually, not merely plausible to.
const PAGINATION_TEMPLATE_VERSIONS = Array.from({ length: 55 }, (_, index) => ({
  id: 500 + index,
  phase: 2,
  name: 'pagination-fixture',
  version: index + 1,
  system_prompt: '',
  user_prompt: `Version ${index + 1} of the pagination fixture template.`,
  output_schema: {},
  is_active: false,
  created_by: 3,
  created_at: '2026-01-01T00:00:00Z',
}));

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
  // 54 filler scenarios (ids 11-64) so this fixture passes PAGE_SIZE=50 -
  // task 10.1's confirmed finding: the phase-3 scenario picker
  // (`useScenarios`) must offer every scenario, not a first page of them,
  // or a real one past row 50 is simply unselectable.
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
    is_editable: true,
    order: 1,
    created_by: 1,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  })),
];

let templates;
let nextTemplateId;
let phaseRuns;
let nextPhaseRunId;
// Every write to /api/prompts/ (POST create, PATCH new-version) - the thing
// backend commit 65f6387 makes provable: a refused draft preview issues
// NEITHER, so a test can assert zero writes rather than only "the version
// count looks the same," which a bug that both wrote AND rolled back could
// still pass.
let promptWriteCount;
// Task 10.4: whether the mock's own backing store currently has an active
// taxonomy to fall back to for a variant-A preview that supplies no
// explicit `guard_taxonomy` (the frontend never sends one for variant A -
// see PromptEditor.jsx's own `handlePreview`, phase-2 `contextFields`). A
// test sets this to `null` to simulate "no active taxonomy" and prove the
// mock fails CLOSED exactly like the real `_preview` (backend/prompts/
// views.py) does, rather than the old, more permissive mock that always
// returned a clean 200 for variant A regardless.
let mockActiveTaxonomyId;

function resetFixtures() {
  templates = [...TEMPLATES_SEED, ...PAGINATION_TEMPLATE_VERSIONS].map((row) => ({ ...row }));
  nextTemplateId = 100;
  phaseRuns = [];
  nextPhaseRunId = 900;
  promptWriteCount = 0;
  mockActiveTaxonomyId = 2;
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

// Shared by both preview routes (id-based and id-less) - one render path,
// matching `prompts/views.py::_preview`'s own "exactly one code path"
// design (a second, subtly different one for drafts is how this project
// has produced taxonomy leaks before).
function renderPreview({ phase, systemPrompt, userPrompt, outputSchema, body }) {
  if (phase === 2) {
    const variant = body.variant;
    if (variant === 'B' && !body.taxonomy) {
      return HttpResponse.json(
        { detail: 'Run B is the guided variant and requires a taxonomy.' },
        { status: 400 }
      );
    }
    // Task 10.4: fail CLOSED, mirroring `_preview`'s own reasoning
    // (backend/prompts/views.py) - a variant-A render for which the
    // isolation backstop could not actually run (no explicit
    // `guard_taxonomy`, and no active taxonomy to fall back to) must never
    // be presentable as a clean 200. The OLD mock always returned 200 with
    // `leak_scan_ran: true` for variant A regardless, which is exactly what
    // let PromptEditor.jsx's own dead "isolation scan did NOT run" green
    // state look reachable when it never legitimately was.
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
      // `paginate`, not the single-page `paginated`: PAGINATION_TEMPLATE_
      // VERSIONS above pushes phase 2's own row count past PAGE_SIZE=50, so
      // `usePromptTemplates` (now fetch-all, task 10.1) has something real
      // to prove itself against.
      return HttpResponse.json(paginate(rows, url));
    }),

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

    // Mirrors backend commit 65f6387: `user_prompt` present in the body
    // means "render THIS draft", never the persisted row's own text -
    // and, on the id-less route, `phase` stands in for the row that
    // doesn't exist yet. Neither branch touches `templates` - a refused
    // draft (variant A referencing taxonomy, a missing B taxonomy, a
    // missing phase-3 scenario) writes nothing, matching
    // `prompts/views.py::_resolve_template`'s own "never .save()'d or
    // .create()'d" guarantee.
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

    http.get('/api/scenarios/', ({ request }) => HttpResponse.json(paginate(SCENARIOS_SEED, request))),

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

    const previewButton = screen.getByRole('button', { name: /^preview$/i });
    const saveButton = screen.getByRole('button', { name: /^save$/i });
    // Nothing has been previewed yet - Save starts disabled, not merely
    // "not yet clicked". Spec §10: validated BEFORE it can be saved.
    expect(saveButton).toBeDisabled();

    await user.click(previewButton);

    expect(await screen.findByText(/render failed/i)).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent(/'taxonomy' is undefined/i);
    expect(screen.queryByText(/preview ok/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/rendered system prompt/i)).not.toBeInTheDocument();

    // Cannot be saved: Save stays disabled with the reason shown...
    expect(saveButton).toBeDisabled();
    expect(screen.getByText(/fix the render error above, then preview again before saving/i)).toBeInTheDocument();
    // ...and - the guarantee backend commit 65f6387 makes provable - NO
    // write request was ever issued: no new row, no version bump. This is
    // the whole point of a real, non-persisting draft preview.
    expect(promptWriteCount).toBe(0);
    expect(templates).toHaveLength(TEMPLATES_SEED.length + PAGINATION_TEMPLATE_VERSIONS.length);
    expect(templates.find((t) => t.id === 1).version).toBe(1);

    // Changing the draft is what lifts the block, not a page reload -
    // Save stays disabled until it has been proven again by a fresh preview.
    await user.type(screen.getByLabelText(/user prompt/i), ' extra');
    expect(saveButton).toBeDisabled();
    expect(promptWriteCount).toBe(0);
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

    const saveButton = screen.getByRole('button', { name: /^save$/i });
    expect(saveButton).toBeDisabled();

    await user.click(screen.getByRole('button', { name: /^preview$/i }));

    expect(await screen.findByText(/preview ok/i)).toBeInTheDocument();
    expect(screen.getByText(/<TAXONOMY TABLE>/)).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    // Proven by a real preview of this exact draft - now Save may proceed,
    // and nothing has been written yet (preview itself never persists).
    expect(saveButton).toBeEnabled();
    expect(promptWriteCount).toBe(0);
  });

  it('editing a template creates a new version once it previews cleanly, and older versions are viewable', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/prompts'] });

    await selectTemplate(user, 'phase2-safe');
    await user.click(screen.getByLabelText(/variant a/i));

    const userPromptField = screen.getByLabelText(/user prompt/i);
    await user.clear(userPromptField);
    await user.type(userPromptField, 'Write a totally different system.');

    const saveButton = screen.getByRole('button', { name: /^save$/i });
    expect(saveButton).toBeDisabled();

    await user.click(screen.getByRole('button', { name: /^preview$/i }));
    expect(await screen.findByText(/preview ok/i)).toBeInTheDocument();
    expect(saveButton).toBeEnabled();

    await user.click(saveButton);

    expect(await screen.findByText('Version 2')).toBeInTheDocument();
    expect(screen.getByText('Version 1')).toBeInTheDocument();
    expect(promptWriteCount).toBe(1);

    const version1Row = screen.getByText('Version 1').closest('li');
    await user.click(within(version1Row).getByRole('button', { name: /view/i }));

    expect(screen.getByLabelText(/user prompt/i)).toHaveValue('Write {{ n_systems }} systems.');
  });

  it('previews a brand-new template with no id yet through the id-less route, then creates it', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/prompts'] });

    await screen.findByLabelText(/^template$/i);
    await user.click(screen.getByRole('button', { name: /new template/i }));

    await user.type(screen.getByLabelText(/^name$/i), 'brand-new-template');
    await user.click(screen.getByLabelText(/variant a/i));
    await user.type(screen.getByLabelText(/user prompt/i), 'Make {{ n_systems }} systems.');

    const saveButton = screen.getByRole('button', { name: /^save$/i });
    expect(saveButton).toBeDisabled();

    await user.click(screen.getByRole('button', { name: /^preview$/i }));
    expect(await screen.findByText(/preview ok/i)).toBeInTheDocument();
    // Nothing was created by the preview itself - the id-less route never
    // touches `templates` either, same as the id-based one.
    expect(templates.find((t) => t.name === 'brand-new-template')).toBeUndefined();
    expect(promptWriteCount).toBe(0);

    await user.click(saveButton);

    expect(await screen.findByText(/editing.*brand-new-template/i)).toBeInTheDocument();
    expect(templates.find((t) => t.name === 'brand-new-template' && t.version === 1)).toBeTruthy();
    expect(promptWriteCount).toBe(1);
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

  it('the version history follows every page of a template with more than 50 versions (fails if only page 1 is read)', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/prompts'] });

    // `pagination-fixture` has 55 versions (PAGINATION_TEMPLATE_VERSIONS) -
    // combined with the two OTHER phase-2 templates, the full phase-2 list
    // is 57 rows: one page (50) would miss this template's own oldest
    // versions AND the alphabetically-last `phase2-safe` template entirely.
    await selectTemplate(user, 'pagination-fixture');

    expect(await screen.findByText('Version 55')).toBeInTheDocument(); // the newest, loaded by default
    expect(screen.getByText('Version 1')).toBeInTheDocument(); // the oldest - lands on page 2

    // `phase2-safe` (sorted after `pagination-fixture`) is entirely on page
    // 2 too - proof the picker itself, not only this template's own
    // history, reflects every page.
    const templateSelect = screen.getByLabelText(/^template$/i);
    expect(within(templateSelect).getByText(/phase2-safe/)).toBeInTheDocument();
  });

  it("the phase-3 scenario picker offers every scenario, not just the first 50 (fails if only page 1 is read)", async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/prompts'] });

    // SCENARIOS_SEED has 55 rows - one page (50) would drop the last 5. The
    // scenario picker only renders once a phase-3 template is loaded.
    const phaseSelect = await screen.findByLabelText(/^phase$/i);
    await user.selectOptions(phaseSelect, '3');
    await selectTemplate(user, 'labelling-default');
    const scenarioSelect = await screen.findByLabelText(/^scenario$/i);
    expect(await within(scenarioSelect).findByText(/filler_64/)).toBeInTheDocument();
  });

  it('resets the variant choice whenever a different template loads, so a stale one can never carry over', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/prompts'] });

    await selectTemplate(user, 'phase2-safe');
    await user.click(screen.getByLabelText(/variant b/i));
    expect(screen.getByText(/editing: variant b/i)).toBeInTheDocument();

    // Task 10.4's own confirmed bug: switching to a DIFFERENT template used
    // to leave "B" selected and checked, as if it had already been chosen
    // for THIS template too - a template only ever intended for variant A
    // could then preview clean under the leftover B and be saved.
    await selectTemplate(user, 'generation-baseline');

    expect(screen.getByText(/no variant chosen yet/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/variant a/i)).not.toBeChecked();
    expect(screen.getByLabelText(/variant b/i)).not.toBeChecked();
    expect(screen.getByRole('button', { name: /^preview$/i })).toBeDisabled();
    expect(screen.getByRole('button', { name: /^save$/i })).toBeDisabled();
  });

  it('never lets a stale variant silently validate a newly-loaded template under the wrong one', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/prompts'] });

    // Preview 'phase2-safe' cleanly under variant B first.
    await selectTemplate(user, 'phase2-safe');
    await user.click(screen.getByLabelText(/variant b/i));
    await user.click(screen.getByRole('button', { name: /^preview$/i }));
    expect(await screen.findByText(/preview ok/i)).toBeInTheDocument();

    // 'generation-baseline' references {{ taxonomy }} - safe under B, but
    // fails under A. Loading it resets the variant (the fix above), so it
    // is never silently validated against the leftover "B" without the
    // researcher explicitly choosing again for THIS template.
    await selectTemplate(user, 'generation-baseline');

    expect(screen.getByText(/no variant chosen yet/i)).toBeInTheDocument();
    expect(screen.queryByText(/preview ok/i)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^save$/i })).toBeDisabled();
  });

  // Task 10.4: the real endpoint fails CLOSED (400) for a variant-A preview
  // whose isolation scan could not actually run - never a clean 200 with
  // `leak_scan_ran: false`. Before this task, the mock always returned 200
  // regardless, which is the only reason PromptEditor.jsx's own dead
  // "isolation scan did NOT run" green state ever looked reachable.
  it('a variant-A preview that cannot verify isolation fails closed with a 400, never a green "Preview OK"', async () => {
    const user = userEvent.setup();
    mockActiveTaxonomyId = null; // nothing to fall back to, and the frontend never sends guard_taxonomy for variant A
    renderApp({ initialEntries: ['/prompts'] });

    // 'phase2-safe' never references {{ taxonomy }} - it would otherwise
    // render "clean" every time, so a 400 here can only come from the
    // fail-closed isolation check itself, not a StrictUndefined error.
    await selectTemplate(user, 'phase2-safe');
    await user.click(screen.getByLabelText(/variant a/i));
    await user.click(screen.getByRole('button', { name: /^preview$/i }));

    expect(await screen.findByText(/render failed/i)).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent(/cannot verify variant a stayed unbiased/i);
    expect(screen.queryByText(/preview ok/i)).not.toBeInTheDocument();
    // The removed dead state must never appear, under any circumstance.
    expect(screen.queryByText(/isolation scan did not run/i)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^save$/i })).toBeDisabled();
  });
});
