import { beforeEach, describe, expect, it } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { authState, server } from '../../test/server';
import { renderApp } from '../../test/renderApp';
import { paginate, paginated } from '../../test/mockApi';

function buildHcvs(taxonomyId, count = 21) {
  return Array.from({ length: count }, (_, index) => ({
    id: taxonomyId * 100 + index + 1,
    hcv_id: `HCV-${String(index + 1).padStart(2, '0')}`,
    name: `Condition ${index + 1}`,
    definition: `Definition text ${index + 1}.`,
    condition_classes: [],
    order: index + 1,
  }));
}

const TAXONOMIES = [
  { id: 1, vocabulary_id: 'hcv-21', version: '1', notes: '', is_active: false, is_locked: false, locked_reason: '', hcv_count: 21, created_at: '2026-01-01T00:00:00Z' },
  { id: 2, vocabulary_id: 'hcv-21', version: '2', notes: '', is_active: true, is_locked: false, locked_reason: '', hcv_count: 21, created_at: '2026-02-01T00:00:00Z' },
];

beforeEach(() => {
  authState.currentUserEmail = 'researcher@example.com';
  server.use(
    http.get('/api/taxonomies/', () => HttpResponse.json(paginated(TAXONOMIES))),
    http.get('/api/taxonomies/:id/hcvs/', ({ params }) =>
      HttpResponse.json(paginated(buildHcvs(Number(params.id))))
    )
  );
});

describe('Taxonomy', () => {
  it('lists all 21 HCVs read-only and marks the active taxonomy', async () => {
    renderApp({ initialEntries: ['/taxonomy'] });

    expect(await screen.findByText('HCV-01 — Condition 1')).toBeInTheDocument();
    expect(screen.getByText('HCV-21 — Condition 21')).toBeInTheDocument();

    const items = screen.getAllByRole('listitem');
    expect(items).toHaveLength(21);

    // the HCV count is shown next to the version select.
    expect(screen.getByText('21 HCVs')).toBeInTheDocument();

    // read-only for a non-staff viewer: nothing lets them add/edit/delete or
    // touch the active version.
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: /save|edit|add hcv|make active|create new version|delete/i })
    ).not.toBeInTheDocument();

    // the active taxonomy (version 2) is selected by default and marked.
    const select = screen.getByLabelText(/version/i);
    expect(within(select).getByRole('option', { name: /v2 \(active\)/i }).selected).toBe(true);
    expect(screen.getByText(/active taxonomy/i)).toBeInTheDocument();
  });

  it('switches to a different version and loses the "active" mark', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/taxonomy'] });

    await screen.findByText('HCV-01 — Condition 1');
    expect(screen.getByText(/active taxonomy/i)).toBeInTheDocument();

    const select = screen.getByLabelText(/version/i);
    await user.selectOptions(select, screen.getByRole('option', { name: /^hcv-21 v1$/i }));

    expect(screen.queryByText(/active taxonomy/i)).not.toBeInTheDocument();
  });

  it('shows an empty state when no taxonomy has been imported', async () => {
    server.use(http.get('/api/taxonomies/', () => HttpResponse.json(paginated([]))));

    renderApp({ initialEntries: ['/taxonomy'] });

    expect(await screen.findByText(/no taxonomy imported/i)).toBeInTheDocument();
  });

  it("renders the server's own error message when the taxonomy list fails to load", async () => {
    server.use(
      http.get('/api/taxonomies/', () =>
        HttpResponse.json({ detail: 'Taxonomy service unavailable.' }, { status: 500 })
      )
    );

    renderApp({ initialEntries: ['/taxonomy'] });

    expect(await screen.findByRole('alert')).toHaveTextContent('Taxonomy service unavailable.');
  });

  it('fetches every page of the taxonomy-version list, not just the first 50 (fails if only page 1 is read)', async () => {
    // 55 taxonomy versions - unrealistic (each arrives by a rare offline
    // import command), but `useTaxonomies` must still follow every page
    // regardless (task 10.2-10.4: task 10.1's own "a handful of versions,
    // stays first-page" exception for this hook was rejected - a bound
    // that holds today by construction is still an assumption).
    const versions = Array.from({ length: 55 }, (_, index) => ({
      id: index + 1,
      vocabulary_id: 'hcv-21',
      version: String(index + 1),
      notes: '',
      is_active: index === 0,
      hcv_count: 21,
      created_at: '2026-01-01T00:00:00Z',
    }));
    server.use(
      http.get('/api/taxonomies/', ({ request }) => HttpResponse.json(paginate(versions, request))),
      http.get('/api/taxonomies/:id/hcvs/', () => HttpResponse.json(paginated([])))
    );

    renderApp({ initialEntries: ['/taxonomy'] });

    const select = await screen.findByLabelText(/version/i);
    expect(within(select).getByRole('option', { name: /v55/i })).toBeInTheDocument();
  });

  it('fetches every page of one taxonomy\'s HCVs, not just the first 50 (fails if only page 1 is read)', async () => {
    // 55 HCV rows for one taxonomy - unrealistic (the real taxonomy is a
    // fixed 21, spec §5.1), but `useTaxonomyHcvs` must still follow every
    // page regardless of how large a given response turns out to be (same
    // "bound held by construction is still an assumption" reasoning).
    const hcvs = Array.from({ length: 55 }, (_, index) => ({
      id: 900 + index,
      hcv_id: `HCV-${String(index + 1).padStart(2, '0')}`,
      name: `Filler condition ${index + 1}`,
      definition: '',
      condition_classes: [],
      order: index + 1,
    }));
    server.use(
      http.get('/api/taxonomies/', () => HttpResponse.json(paginated(TAXONOMIES))),
      http.get('/api/taxonomies/:id/hcvs/', ({ request, params }) => {
        if (Number(params.id) !== TAXONOMIES[1].id) return HttpResponse.json(paginated([]));
        return HttpResponse.json(paginate(hcvs, request));
      })
    );

    renderApp({ initialEntries: ['/taxonomy'] });

    expect(await screen.findByText(/HCV-55 — Filler condition 55/)).toBeInTheDocument();
  });
});

describe('Taxonomy editing (staff)', () => {
  it('lets staff activate a different, non-active version', async () => {
    authState.currentUserEmail = 'admin@example.com';
    let taxonomies = TAXONOMIES.map((taxonomy) => ({ ...taxonomy }));
    server.use(
      http.get('/api/taxonomies/', () => HttpResponse.json(paginated(taxonomies))),
      http.post('/api/taxonomies/:id/activate/', ({ params }) => {
        const id = Number(params.id);
        taxonomies = taxonomies.map((taxonomy) => ({ ...taxonomy, is_active: taxonomy.id === id }));
        return HttpResponse.json(taxonomies.find((taxonomy) => taxonomy.id === id));
      })
    );

    const user = userEvent.setup();
    renderApp({ initialEntries: ['/taxonomy'] });

    await screen.findByText('HCV-01 — Condition 1');
    const select = screen.getByLabelText(/version/i);
    await user.selectOptions(select, screen.getByRole('option', { name: /^hcv-21 v1$/i }));

    const activateButton = await screen.findByRole('button', { name: /make active/i });
    expect(
      screen.getByText(/active version is what new experiments and prompt tests use by default/i)
    ).toBeInTheDocument();

    await user.click(activateButton);

    expect(await screen.findByText(/active taxonomy/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /make active/i })).not.toBeInTheDocument();
  });

  it('shows a locked note for a locked version and lets staff create a new version from it', async () => {
    authState.currentUserEmail = 'admin@example.com';
    let taxonomies = [
      {
        id: 5,
        vocabulary_id: 'hcv-21',
        version: '1.1',
        notes: '',
        is_active: true,
        is_locked: true,
        locked_reason: 'Pipeline run #12 has already used this version.',
        hcv_count: 21,
        created_at: '2026-03-01T00:00:00Z',
      },
    ];
    server.use(
      http.get('/api/taxonomies/', () => HttpResponse.json(paginated(taxonomies))),
      http.get('/api/taxonomies/:id/hcvs/', ({ params }) =>
        HttpResponse.json(paginated(buildHcvs(Number(params.id))))
      ),
      http.post('/api/taxonomies/:id/new-version/', async ({ request }) => {
        const body = await request.json();
        const created = {
          id: 6,
          vocabulary_id: 'hcv-21',
          version: body.version,
          notes: '',
          is_active: false,
          is_locked: false,
          locked_reason: '',
          hcv_count: 21,
          created_at: '2026-03-02T00:00:00Z',
        };
        taxonomies = [...taxonomies, created];
        return HttpResponse.json(created, { status: 201 });
      })
    );

    const user = userEvent.setup();
    renderApp({ initialEntries: ['/taxonomy'] });

    expect(await screen.findByText(/pipeline run #12 has already used this version/i)).toBeInTheDocument();
    // locked: no add/edit/delete controls.
    expect(screen.queryByRole('button', { name: /add hcv/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^edit$/i })).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /create new version/i }));

    const dialog = await screen.findByRole('dialog');
    const versionInput = within(dialog).getByLabelText(/version/i);
    // "1.1" -> "1.2": the suggested bump.
    expect(versionInput).toHaveValue('1.2');

    await user.click(within(dialog).getByRole('button', { name: /create version/i }));

    // the new, unlocked version is now selected and offers Add HCV.
    expect(await screen.findByRole('button', { name: /add hcv/i })).toBeInTheDocument();
    expect(screen.queryByText(/pipeline run #12 has already used this version/i)).not.toBeInTheDocument();
  });

  it('lets staff add, edit and delete an HCV on an unlocked version', async () => {
    authState.currentUserEmail = 'admin@example.com';
    const taxonomies = [
      { id: 7, vocabulary_id: 'hcv-21', version: '1', notes: '', is_active: true, is_locked: false, locked_reason: '', hcv_count: 2, created_at: '2026-04-01T00:00:00Z' },
    ];
    let hcvs = buildHcvs(7, 2);
    server.use(
      http.get('/api/taxonomies/', () => HttpResponse.json(paginated(taxonomies))),
      http.get('/api/taxonomies/:id/hcvs/', () => HttpResponse.json(paginated(hcvs))),
      http.post('/api/taxonomies/:id/hcvs/', async ({ request }) => {
        const body = await request.json();
        const created = { id: 999, ...body };
        hcvs = [...hcvs, created];
        return HttpResponse.json(created, { status: 201 });
      }),
      http.patch('/api/hcvs/:id/', async ({ request, params }) => {
        const body = await request.json();
        const id = Number(params.id);
        hcvs = hcvs.map((hcv) => (hcv.id === id ? { ...hcv, ...body } : hcv));
        return HttpResponse.json(hcvs.find((hcv) => hcv.id === id));
      }),
      http.delete('/api/hcvs/:id/', ({ params }) => {
        const id = Number(params.id);
        hcvs = hcvs.filter((hcv) => hcv.id !== id);
        return new HttpResponse(null, { status: 204 });
      })
    );

    const user = userEvent.setup();
    renderApp({ initialEntries: ['/taxonomy'] });

    await screen.findByText('HCV-01 — Condition 1');

    // Add - order defaults to max order + 1.
    await user.click(screen.getByRole('button', { name: /add hcv/i }));
    let dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByLabelText(/order/i)).toHaveValue(3);
    await user.type(within(dialog).getByLabelText(/hcv id/i), 'HCV-99');
    await user.type(within(dialog).getByLabelText(/^name$/i), 'New Condition');
    await user.type(within(dialog).getByLabelText(/condition classes/i), 'econ, health');
    await user.click(within(dialog).getByRole('button', { name: /^add$/i }));

    expect(await screen.findByText('HCV-99 — New Condition')).toBeInTheDocument();

    // Edit - prefilled from the row.
    const newCard = screen.getByText('HCV-99 — New Condition').closest('li');
    await user.click(within(newCard).getByRole('button', { name: /^edit$/i }));
    dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByLabelText(/hcv id/i)).toHaveValue('HCV-99');
    expect(within(dialog).getByLabelText(/condition classes/i)).toHaveValue('econ, health');
    const nameInput = within(dialog).getByLabelText(/^name$/i);
    await user.clear(nameInput);
    await user.type(nameInput, 'Updated Condition');
    await user.click(within(dialog).getByRole('button', { name: /^save$/i }));

    expect(await screen.findByText('HCV-99 — Updated Condition')).toBeInTheDocument();

    // Delete - via ConfirmDialog.
    const updatedCard = screen.getByText('HCV-99 — Updated Condition').closest('li');
    await user.click(within(updatedCard).getByRole('button', { name: /delete/i }));
    const confirm = await screen.findByRole('alertdialog');
    await user.click(within(confirm).getByRole('button', { name: /delete/i }));

    expect(screen.queryByText('HCV-99 — Updated Condition')).not.toBeInTheDocument();
  });
});
