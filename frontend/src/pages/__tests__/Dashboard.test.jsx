import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, screen } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { authState, server } from '../../test/server';
import { renderApp } from '../../test/renderApp';
import { RUN_POLL_INTERVAL_MS } from '../../api/queries';

function paginated(results) {
  return { count: results.length, next: null, previous: null, results };
}

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
});
