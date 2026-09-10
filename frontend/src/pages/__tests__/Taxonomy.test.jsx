import { beforeEach, describe, expect, it } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { authState, server } from '../../test/server';
import { renderApp } from '../../test/renderApp';

function paginated(results) {
  return { count: results.length, next: null, previous: null, results };
}

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
});
