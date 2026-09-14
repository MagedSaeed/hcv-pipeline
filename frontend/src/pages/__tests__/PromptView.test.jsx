import { beforeEach, describe, expect, it } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { authState, currentUser, server } from '../../test/server';
import { renderApp } from '../../test/renderApp';
import { paginated, requireCsrf, requireStaff } from '../../test/mockApi';

// Two versions of one name (a variant-A prompt that genuinely leaks
// taxonomy - used to prove the Rendered tab surfaces a real refusal), plus
// a safe variant-B prompt used for the render-defaults/re-render test.
const TEMPLATES_SEED = [
  {
    id: 1,
    phase: 2,
    name: 'generation-baseline',
    variant: 'A',
    version: 1,
    system_prompt: '',
    user_prompt: 'Write {{ n_systems }} systems using {{ taxonomy }}. V1',
    output_schema: {},
    is_active: false,
    created_by: 3,
    created_at: '2026-01-01T00:00:00Z',
  },
  {
    id: 2,
    phase: 2,
    name: 'generation-baseline',
    variant: 'A',
    version: 2,
    system_prompt: '',
    user_prompt: 'Write {{ n_systems }} systems using {{ taxonomy }}. V2',
    output_schema: {},
    is_active: true,
    created_by: 3,
    created_at: '2026-01-05T00:00:00Z',
  },
  {
    id: 3,
    phase: 2,
    name: 'phase2-safe',
    variant: 'B',
    version: 1,
    system_prompt: 'A system prompt.',
    user_prompt: 'Write {{ n_systems }} safe systems.',
    output_schema: {},
    is_active: true,
    created_by: 3,
    created_at: '2026-01-02T00:00:00Z',
  },
];

const SYSTEMS_SEED = [
  { id: 20, title: 'Water treatment plant', kind: 'system', origin: 'seed', body: '', created_by: 1, created_at: '2026-01-01T00:00:00Z' },
];

const FEWSHOTS_SEED = [
  { id: 30, phase: 2, name: 'Oldsmar', input_text: 'in', output_text: 'out', resolved_input: 'in', resolved_output: 'out', order: 1, is_active: true },
];

let templates;

function resetFixtures() {
  templates = TEMPLATES_SEED.map((row) => ({ ...row }));
}

// Stands in for prompts/rendering.py's render(): a variant-A row that
// mentions "taxonomy" fails exactly the way StrictUndefined does. Echoes
// `generate_scenarios` from the request body into the rendered text (a
// mock-only signal) so a test can prove changing that input re-renders.
function renderPreview(template, body) {
  if (template.variant === 'A' && /taxonomy/i.test(template.user_prompt)) {
    return HttpResponse.json({ detail: "'taxonomy' is undefined" }, { status: 400 });
  }
  const rendered = template.user_prompt.replace(/\{\{\s*taxonomy\s*\}\}/gi, '<TAXONOMY TABLE>');
  return HttpResponse.json({
    system_prompt: template.system_prompt,
    user_prompt: `${rendered} [gs=${body.generate_scenarios}]`,
    output_schema: template.output_schema,
    leak_scan_ran: template.variant === 'A',
    guarded_by: template.variant === 'A' ? Number(body.taxonomy) || null : null,
  });
}

let viewRunsFixture = [];

beforeEach(() => {
  viewRunsFixture = [];
  authState.currentUserEmail = 'admin@example.com'; // staff - see server.js SEED_USERS
  resetFixtures();

  server.use(
    http.get('/api/prompts/', () => {
      const rows = [...templates].sort(
        (a, b) => a.phase - b.phase || a.name.localeCompare(b.name) || b.version - a.version
      );
      return HttpResponse.json(paginated(rows));
    }),

    http.post('/api/prompts/:id/preview/', async ({ request, params }) => {
      const staffRefusal = requireStaff(currentUser());
      if (staffRefusal) return staffRefusal;
      const template = templates.find((t) => t.id === Number(params.id));
      if (!template) return HttpResponse.json({ detail: 'Not found.' }, { status: 404 });
      const body = await request.json();
      return renderPreview(template, body);
    }),

    http.get('/api/system-descriptions/', () => HttpResponse.json(paginated(SYSTEMS_SEED))),
    http.get('/api/fewshots/', ({ request }) => {
      const url = new URL(request.url);
      const phaseParam = url.searchParams.get('phase');
      const rows = phaseParam ? FEWSHOTS_SEED.filter((f) => f.phase === Number(phaseParam)) : FEWSHOTS_SEED;
      return HttpResponse.json(paginated(rows));
    }),
    http.get('/api/scenarios/', () => HttpResponse.json(paginated([]))),
    http.get('/api/phase-runs/', () => HttpResponse.json(paginated(viewRunsFixture)))
  );
});

describe('PromptView', () => {
  it('shows the stored template, and the version selector switches to a different version', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/prompts/2'] });

    expect(await screen.findByText(/write.*systems using.*v2/i)).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText(/^version$/i), '1');
    expect(await screen.findByText(/write.*systems using.*v1/i)).toBeInTheDocument();
  });

  it('the Rendered tab renders automatically against default resources, and re-renders when an input changes', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/prompts/3'] });

    await screen.findByText('phase2-safe');
    await user.click(screen.getByRole('tab', { name: /^rendered$/i }));
    expect(await screen.findByText(/\[gs=true\]/)).toBeInTheDocument();

    await user.click(screen.getByLabelText(/generate scenarios/i));
    expect(await screen.findByText(/\[gs=false\]/)).toBeInTheDocument();
  });

  it('shows a render refusal (a variant-A taxonomy leak) through ErrorBanner', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/prompts/2'] });

    await screen.findByText('generation-baseline');
    await user.click(screen.getByRole('tab', { name: /^rendered$/i }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/'taxonomy' is undefined/i);
  });

  it('a non-staff user sees a notice instead of a render attempt, and no Edit button', async () => {
    const user = userEvent.setup();
    authState.currentUserEmail = 'researcher@example.com'; // not staff
    renderApp({ initialEntries: ['/prompts/3'] });

    expect(await screen.findByText('phase2-safe')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /^edit$/i })).not.toBeInTheDocument();

    await user.click(screen.getByRole('tab', { name: /^rendered$/i }));
    expect(screen.getByText(/only staff may render this prompt/i)).toBeInTheDocument();
  });

  it('lists the prompt\'s runs: tests open in the editor panel, experiment runs link to their repetition', async () => {
    viewRunsFixture = [
      { id: 71, pipeline_run: null, phase: 2, variant: 'A', model: 'm-test', prompt_template: 1, status: 'completed', total_cost: null, finished_at: '2026-09-11T00:00:00Z' },
      { id: 72, pipeline_run: 900, phase: 2, variant: 'B', model: 'm-exp', prompt_template: 1, status: 'completed', total_cost: 0.5, finished_at: '2026-09-11T01:00:00Z' },
    ];
    renderApp({ initialEntries: ['/prompts/1'] });

    expect(await screen.findByRole('link', { name: /^open$/i })).toHaveAttribute('href', '/prompts/1/edit?testRun=71');
    expect(screen.getByRole('link', { name: /open run/i })).toHaveAttribute('href', '/pipeline-runs/900');
  });

  it('a staff user can make a non-active version active', async () => {
    const user = userEvent.setup();
    let activated = null;
    server.use(
      http.post('/api/prompts/:id/activate/', ({ params, request }) => {
        const refusal = requireCsrf(request);
        if (refusal) return refusal;
        activated = Number(params.id);
        templates.forEach((t) => {
          if (t.name === 'generation-baseline') t.is_active = t.id === activated;
        });
        return HttpResponse.json(templates.find((t) => t.id === activated));
      })
    );
    const inactive = templates.find((t) => t.name === 'generation-baseline' && !t.is_active);
    renderApp({ initialEntries: [`/prompts/${inactive.id}`] });

    await user.click(await screen.findByRole('button', { name: /make this version active/i }));
    await waitFor(() => expect(activated).toBe(inactive.id));
    await waitFor(() => expect(screen.queryByRole('button', { name: /make this version active/i })).not.toBeInTheDocument());
  });

  it('an unknown id shows an error banner', async () => {
    renderApp({ initialEntries: ['/prompts/999'] });
    expect(await screen.findByRole('alert')).toHaveTextContent(/could not be found/i);
  });
});
