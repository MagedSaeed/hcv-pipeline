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
  { id: 1, vocabulary_id: 'hcv-21', version: '1', notes: '', is_active: false, hcv_count: 21, created_at: '2026-01-01T00:00:00Z' },
  { id: 2, vocabulary_id: 'hcv-21', version: '2', notes: '', is_active: true, hcv_count: 21, created_at: '2026-02-01T00:00:00Z' },
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

    // read-only: nothing on the page lets a viewer edit an HCV.
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /save|edit/i })).not.toBeInTheDocument();

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
