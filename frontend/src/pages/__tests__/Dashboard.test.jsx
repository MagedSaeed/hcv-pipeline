import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, screen } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { authState, server } from '../../test/server';
import { renderApp } from '../../test/renderApp';
import { paginate, paginated } from '../../test/mockApi';
import { RUN_POLL_INTERVAL_MS } from '../../api/queries';

function run(overrides) {
  return {
    id: 1,
    experiment: 1,
    repetition_index: 1,
    status: 'completed',
    config: {},
    progress: { done: 1, total: 1, failed: 0, percent: 100 },
    started_at: '2026-03-01T00:00:00Z',
    finished_at: '2026-03-01T00:10:00Z',
    ...overrides,
  };
}

beforeEach(() => {
  authState.currentUserEmail = 'researcher@example.com';
});

describe('Dashboard', () => {
  it('lists experiments with a status and a progress bar', async () => {
    server.use(
      http.get('/api/experiments/', () =>
        HttpResponse.json(
          paginated([
            {
              id: 1,
              name: 'Baseline comparison',
              run_count: 2,
              progress: { done: 16, total: 32, failed: 0, percent: 50 },
            },
          ])
        )
      ),
      http.get('/api/pipeline-runs/', () => HttpResponse.json(paginated([])))
    );

    renderApp({ initialEntries: ['/dashboard'] });

    expect(await screen.findByText('Baseline comparison')).toBeInTheDocument();
    expect(screen.getByText('Running')).toBeInTheDocument();
    const progressBar = screen.getByRole('progressbar');
    expect(progressBar).toHaveAttribute('aria-valuenow', '50');
  });

  it('shows an empty state with a link to the builder when there are no experiments', async () => {
    server.use(
      http.get('/api/experiments/', () => HttpResponse.json(paginated([]))),
      http.get('/api/pipeline-runs/', () => HttpResponse.json(paginated([])))
    );

    renderApp({ initialEntries: ['/dashboard'] });

    expect(await screen.findByText(/no experiments yet/i)).toBeInTheDocument();
    const builderLinks = screen.getAllByRole('link', { name: /new experiment/i });
    expect(builderLinks.some((link) => link.getAttribute('href') === '/experiments/new')).toBe(true);
  });

  it('shows a cancelled run distinctly from a failed one', async () => {
    server.use(
      http.get('/api/experiments/', () => HttpResponse.json(paginated([]))),
      http.get('/api/pipeline-runs/', () =>
        HttpResponse.json(
          paginated([
            run({ id: 1, repetition_index: 1, status: 'failed' }),
            run({ id: 2, repetition_index: 2, status: 'cancelled' }),
          ])
        )
      )
    );

    renderApp({ initialEntries: ['/dashboard'] });

    const failedBadge = await screen.findByText('Failed');
    const cancelledBadge = await screen.findByText('Cancelled');
    // Both are terminal, but an operator who cancelled a run must not read
    // it as a crash - StatusBadge must give them visibly different colours.
    expect(failedBadge.className).not.toBe(cancelledBadge.className);
  });

  it("renders the server's own error message when the experiments list fails to load", async () => {
    server.use(
      http.get('/api/experiments/', () =>
        HttpResponse.json({ detail: 'Experiments are temporarily unavailable.' }, { status: 500 })
      ),
      http.get('/api/pipeline-runs/', () => HttpResponse.json(paginated([])))
    );

    renderApp({ initialEntries: ['/dashboard'] });

    expect(await screen.findByRole('alert')).toHaveTextContent('Experiments are temporarily unavailable.');
  });

  it('polls the recent-runs list while a run is active and stops once every run is terminal', async () => {
    let callCount = 0;
    server.use(
      http.get('/api/experiments/', () => HttpResponse.json(paginated([]))),
      http.get('/api/pipeline-runs/', () => {
        callCount += 1;
        // First response: still running, so a poll must be scheduled.
        // Every response after that: terminal, so polling must stop.
        const status = callCount === 1 ? 'running' : 'completed';
        return HttpResponse.json(paginated([run({ status })]));
      })
    );

    vi.useFakeTimers();
    try {
      renderApp({ initialEntries: ['/dashboard'] });

      // Flush the initial mount-triggered fetch (msw/axios resolve via
      // microtasks and real Node I/O callbacks, which advanceTimersByTimeAsync
      // still lets run even with fake timers installed). Wrapped in act()
      // because the resulting query state update re-renders Dashboard.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
      expect(callCount).toBe(1);

      // One run is still "running" - the next poll must fire.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(RUN_POLL_INTERVAL_MS + 50);
      });
      expect(callCount).toBe(2);

      // The second response was all-terminal - no further poll, ever,
      // no matter how much more time passes.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(RUN_POLL_INTERVAL_MS * 5);
      });
      expect(callCount).toBe(2);
    } finally {
      vi.useRealTimers();
    }
  });

  // REAL timers throughout, deliberately - unlike the fake-timer poll test
  // above, fetching 55 rows at PAGE_SIZE=50 takes TWO sequential HTTP round
  // trips per logical fetch (page 1, then page 2, `fetchAllPages` in
  // api/queries.js), and a fake clock advanced by a fixed amount between
  // `act()` calls proved unreliable at flushing more than one such round
  // trip at a time - the same reasoning PromptEditor.test.jsx's own iterate-
  // mode tests give for using real timers over fake ones once more than one
  // async hop is involved. A real wait is slower but unambiguous.
  it(
    'fetches every page of the run list, and the poll gate keeps polling for a run past row 50 ' +
      '(fails if only page 1 is read)',
    async () => {
      // Counts logical fetch-ALL cycles (one per poll), not raw HTTP
      // requests - this only advances when a new cycle STARTS (`?page=`
      // absent or `1`), the same walk `fetchAllPages` performs.
      let fetchCycle = 0;
      server.use(
        http.get('/api/experiments/', () => HttpResponse.json(paginated([]))),
        // 55 runs: rows 1-50 are ALWAYS terminal ("completed"); rows 51-55
        // start "running" and only turn terminal on the SECOND poll cycle -
        // the exact task 10.1 finding (n_repetitions allows up to 100, so a
        // batch's own later repetitions land past PAGE_SIZE=50). A
        // first-page-only read would see nothing but terminal rows on
        // page 1 and never even ask for page 2, so the poll gate would
        // wrongly stop after the very first fetch.
        http.get('/api/pipeline-runs/', ({ request }) => {
          const url = new URL(request.url);
          const page = url.searchParams.get('page');
          if (!page || page === '1') fetchCycle += 1;
          const tailStatus = fetchCycle === 1 ? 'running' : 'completed';
          const runs = [
            ...Array.from({ length: 50 }, (_, index) => run({ id: index + 1, repetition_index: index + 1, status: 'completed' })),
            ...Array.from({ length: 5 }, (_, index) => run({ id: 51 + index, repetition_index: 51 + index, status: tailStatus })),
          ];
          return HttpResponse.json(paginate(runs, url));
        })
      );

      renderApp({ initialEntries: ['/dashboard'] });

      // The run list itself must show every row, not just page 1's 50 -
      // and this only resolves once BOTH page requests of the first cycle
      // have completed.
      expect(await screen.findByText('Repetition 55')).toBeInTheDocument();
      expect(fetchCycle).toBe(1);

      // Repetition 55 (on page 2) is still "running" - the poll gate must
      // have seen it and scheduled another poll cycle.
      await vi.waitFor(() => expect(fetchCycle).toBe(2), { timeout: RUN_POLL_INTERVAL_MS + 3000 });

      // Every row is terminal as of the second cycle - no further poll,
      // no matter how much more real time passes.
      await new Promise((resolve) => setTimeout(resolve, RUN_POLL_INTERVAL_MS + 500));
      expect(fetchCycle).toBe(2);
    },
    15000
  );
});
