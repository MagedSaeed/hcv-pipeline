import { beforeEach, describe, expect, it } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { authState, currentUser, server } from '../../test/server';
import { renderApp } from '../../test/renderApp';
import { paginate, requireCsrf, requireStaff } from '../../test/mockApi';

// Four named templates (id 1/2 share a name, two versions), plus a
// 55-version fixture under its own name so the list's fetch-all is proven
// against something real (task D: `usePromptTemplates({})` must follow
// every page, same as PromptEditor's own version history used to).
const TEMPLATES_SEED = [
  {
    id: 1,
    phase: 2,
    name: 'generation-baseline',
    variant: 'A',
    version: 1,
    system_prompt: '',
    user_prompt: 'v1 body',
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
    user_prompt: 'v2 body',
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
    system_prompt: '',
    user_prompt: 'safe body',
    output_schema: {},
    is_active: false,
    created_by: 3,
    created_at: '2026-01-02T00:00:00Z',
  },
  {
    id: 4,
    phase: 3,
    name: 'labelling-default',
    variant: '',
    version: 1,
    system_prompt: '',
    user_prompt: 'label body',
    output_schema: {},
    is_active: true,
    created_by: 3,
    created_at: '2026-01-03T00:00:00Z',
  },
];

const PAGINATION_TEMPLATE_VERSIONS = Array.from({ length: 55 }, (_, index) => ({
  id: 500 + index,
  phase: 2,
  name: 'pagination-fixture',
  variant: 'A',
  version: index + 1,
  system_prompt: '',
  user_prompt: `Version ${index + 1}`,
  output_schema: {},
  is_active: false,
  created_by: 3,
  created_at: '2026-01-01T00:00:00Z',
}));

let templates;
let blockedDeleteIds;

function resetFixtures() {
  templates = [...TEMPLATES_SEED, ...PAGINATION_TEMPLATE_VERSIONS].map((row) => ({ ...row }));
  blockedDeleteIds = new Set();
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
      return HttpResponse.json(paginate(rows, url));
    }),

    http.delete('/api/prompts/:id/', async ({ request, params }) => {
      const staffRefusal = requireStaff(currentUser());
      if (staffRefusal) return staffRefusal;
      const csrfRefusal = requireCsrf(request);
      if (csrfRefusal) return csrfRefusal;
      const id = Number(params.id);
      const index = templates.findIndex((t) => t.id === id);
      if (index === -1) return HttpResponse.json({ detail: 'Not found.' }, { status: 404 });
      if (blockedDeleteIds.has(id)) {
        return HttpResponse.json(
          { detail: `Prompt template ${id} is in use by an experiment or a run and cannot be deleted.` },
          { status: 400 }
        );
      }
      templates.splice(index, 1);
      return new HttpResponse(null, { status: 204 });
    }),

    // Clicking "New prompt" navigates into PromptEditor.jsx, which fetches
    // both of these regardless of phase - trivial empty handlers so that
    // one test doesn't trip the suite's strict "no unhandled request" mode.
    http.get('/api/models/', () => HttpResponse.json({ count: 0, results: [] })),
    http.get('/api/scenarios/', () => HttpResponse.json({ count: 0, next: null, previous: null, results: [] }))
  );
});

describe('PromptList', () => {
  it('groups templates by phase then name, one row per name, with variant badge/latest/active/last-edited', async () => {
    renderApp({ initialEntries: ['/prompts'] });

    expect(await screen.findByRole('heading', { name: /phase 2 — generation/i })).toBeInTheDocument();
    const baselineRow = (await screen.findByText('generation-baseline')).closest('li');
    expect(within(baselineRow).getByText(/variant a/i)).toBeInTheDocument();
    expect(within(baselineRow).getByText(/v2/)).toBeInTheDocument();
    expect(within(baselineRow).getByText(/active: v2/)).toBeInTheDocument();

    const safeRow = screen.getByText('phase2-safe').closest('li');
    expect(within(safeRow).getByText(/variant b/i)).toBeInTheDocument();
    expect(within(safeRow).getByText(/no active version/i)).toBeInTheDocument();

    const labellingRow = screen.getByText('labelling-default').closest('li');
    expect(within(labellingRow).queryByText(/variant/i)).not.toBeInTheDocument();
    expect(within(labellingRow).getByText(/active: v1/)).toBeInTheDocument();
  });

  it('the Versions expander lists every version and deletes one at a time', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/prompts'] });

    const baselineRow = (await screen.findByText('generation-baseline')).closest('li');
    await user.click(within(baselineRow).getByRole('button', { name: /versions \(2\)/i }));
    expect(within(baselineRow).getByText('Version 1')).toBeInTheDocument();
    const version2 = within(baselineRow).getByText('Version 2').closest('li');
    expect(within(version2).getByText(/active/i)).toBeInTheDocument();

    const version1 = within(baselineRow).getByText('Version 1').closest('li');
    await user.click(within(version1).getByRole('button', { name: /^delete$/i }));
    const dialog = within(baselineRow).getByRole('alertdialog');
    await user.click(within(dialog).getByRole('button', { name: /^confirm$/i }));

    await waitFor(() => expect(within(baselineRow).queryByText('Version 1')).not.toBeInTheDocument());
    expect(within(baselineRow).getByText('Version 2')).toBeInTheDocument();
    expect(templates.find((t) => t.id === 1)).toBeUndefined();
  });

  it('deleting the whole row removes every version one by one, keeping a version still in use and explaining why', async () => {
    const user = userEvent.setup();
    blockedDeleteIds.add(2); // generation-baseline v2 (active) still "in use"
    renderApp({ initialEntries: ['/prompts'] });

    const baselineRow = (await screen.findByText('generation-baseline')).closest('li');
    await user.click(within(baselineRow).getByRole('button', { name: /^delete$/i }));
    const dialog = within(baselineRow).getByRole('alertdialog');
    await user.click(within(dialog).getByRole('button', { name: /^confirm$/i }));

    expect(await within(baselineRow).findByRole('note')).toHaveTextContent(/still in use/i);
    expect(within(baselineRow).getByRole('note')).toHaveTextContent(/v2/);
    expect(templates.find((t) => t.id === 1)).toBeUndefined(); // deleted
    expect(templates.find((t) => t.id === 2)).toBeDefined(); // kept
    expect(within(baselineRow).getByText(/versions \(1\)/i)).toBeInTheDocument();
  });

  it('"New prompt" opens the empty editor for the right phase', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/prompts'] });

    const phase3Section = (await screen.findByRole('heading', { name: /phase 3 — labelling/i })).closest('section');
    await user.click(within(phase3Section).getByRole('link', { name: /new prompt/i }));

    expect(await screen.findByRole('heading', { name: /^new prompt$/i })).toBeInTheDocument();
    expect(screen.getByText(/phase 3 — labelling/i)).toBeInTheDocument();
  });

  it('fetches every page, so a name sorted past page 1 still appears', async () => {
    renderApp({ initialEntries: ['/prompts'] });

    expect(await screen.findByText(/versions \(55\)/i)).toBeInTheDocument();
    // 'phase2-safe' sorts alphabetically after 'pagination-fixture', landing
    // entirely on page 2 of the raw API response - fails if only page 1 is
    // read.
    expect(screen.getByText('phase2-safe')).toBeInTheDocument();
  });

  it('a non-staff user sees View only, no Edit/Delete/New prompt controls', async () => {
    authState.currentUserEmail = 'researcher@example.com'; // not staff
    renderApp({ initialEntries: ['/prompts'] });

    const baselineRow = (await screen.findByText('generation-baseline')).closest('li');
    expect(within(baselineRow).getByRole('link', { name: /^view$/i })).toBeInTheDocument();
    expect(within(baselineRow).queryByRole('link', { name: /^edit$/i })).not.toBeInTheDocument();
    expect(within(baselineRow).queryByRole('button', { name: /^delete$/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /new prompt/i })).not.toBeInTheDocument();
  });
});
