import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { authState, currentUser, server } from '../../test/server';
import { renderApp } from '../../test/renderApp';
import { paginate, paginated, requireCsrf, requireStaff } from '../../test/mockApi';
import { RUN_POLL_INTERVAL_MS } from '../../api/queries';

// `cancel`/`delete`/`retry` are staff-only (spec §9) - every test that
// exercises one installs its own handler (the response varies per test),
// so the guard wraps each test's own resolver rather than living as a
// separate layered handler (see ExperimentBuilder.test.jsx's identical
// `guardedExperimentWrite` for why: a later `server.use()` call - the
// test's own - wins over an earlier one for the same request, so a
// standalone guard handler registered first would never actually run).
function guardedStaffWrite(resolver) {
  return async (info) => {
    const staffRefusal = requireStaff(currentUser());
    if (staffRefusal) return staffRefusal;
    const csrfRefusal = requireCsrf(info.request);
    if (csrfRefusal) return csrfRefusal;
    return resolver(info);
  };
}

// PipelineRun.config's exact shape (backend/pipeline/execution_config.py's
// config_snapshot) - what THIS repetition actually pinned at launch.
const PINNED_CONFIG = {
  seed_systems: [
    { id: 10, title: 'Pinned Oldsmar (as launched)', slug: 'oldsmar', body: 'body', scenarios: [] },
  ],
  fewshots: [],
  target_system: null,
  generate_systems: true,
  generate_scenarios: true,
  n_systems: 3,
  n_scenarios_per_system: 5,
};

function pipelineRun(overrides = {}) {
  return {
    id: 501,
    experiment: 1,
    repetition_index: 1,
    status: 'running',
    config: PINNED_CONFIG,
    progress: { done: 10, total: 32, failed: 0, percent: 31 },
    started_at: '2026-09-10T00:00:00Z',
    finished_at: null,
    ...overrides,
  };
}

// The experiment's CURRENT, live, editable configuration - deliberately
// DIFFERENT from PINNED_CONFIG above (a different seed system id/title),
// so a test can prove the two panels never show one where the other
// belongs (spec: "Pinned config is not current config").
const EXPERIMENT_CURRENT = {
  id: 1,
  name: 'Baseline HCV-21 comparison',
  description: '',
  taxonomy: 2,
  phase2_model: 'openai/gpt-4o-mini',
  phase2_temperature: 0.7,
  phase2_prompt_a: 1,
  phase2_prompt_b: 2,
  phase3_model: 'openai/gpt-4o-mini',
  phase3_temperature: 0,
  phase3_prompt: 3,
  seed_systems: [99],
  fewshots: [],
  generate_systems: true,
  generate_scenarios: true,
  target_system: null,
  n_systems: 3,
  n_scenarios_per_system: 5,
  variants: ['A', 'B'],
  progress: { done: 32, total: 32, failed: 0, percent: 100 },
  run_count: 1,
  created_by: 3,
  created_at: '2026-03-01T00:00:00Z',
};

const CURRENT_SEED_SYSTEMS = [
  { id: 99, title: 'Current Oldsmar (edited since launch)', slug: 'oldsmar-2', body: 'body', origin: 'seed', is_editable: true, created_by: 1, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' },
];

function phaseRun(overrides = {}) {
  return {
    id: 9001,
    pipeline_run: 501,
    phase: 2,
    variant: 'A',
    model: 'openai/gpt-4o-mini',
    temperature: 1,
    prompt_template: 1,
    taxonomy: 2,
    source_phase_run: null,
    experiment: null,
    n_systems: null,
    n_scenarios_per_system: null,
    status: 'running',
    error: '',
    progress: { done: 1, total: 2, failed: 0, percent: 50 },
    total_children: 2,
    completed_children: 1,
    failed_children: 0,
    leak_scan_ran: true,
    guarded_by: [2],
    started_at: '2026-09-10T00:00:00Z',
    finished_at: null,
    ...overrides,
  };
}

function call(overrides = {}) {
  return {
    id: 70001,
    sequence: 1,
    scenario: null,
    rendered_system_prompt: 'SYSTEM PROMPT TEXT',
    rendered_user_prompt: 'USER PROMPT TEXT',
    request_json: {},
    response_json: {},
    raw_response: 'RAW RESPONSE TEXT',
    parsed_ok: true,
    parse_error: '',
    unknown_hcv_ids: [],
    stray_scenarios: [],
    http_status: 200,
    transport_error: '',
    model: 'openai/gpt-4o-mini',
    finish_reason: 'stop',
    prompt_tokens: 100,
    completion_tokens: 50,
    cost: 0.01,
    latency_ms: 900,
    leak_scan_ran: true,
    guarded_by: 2,
    created_at: '2026-09-10T00:05:00Z',
    ...overrides,
  };
}

function installBaseFixtures({ run, phaseRuns }) {
  server.use(
    http.get('/api/pipeline-runs/501/', () => HttpResponse.json(run)),
    http.get('/api/experiments/1/', () => HttpResponse.json(EXPERIMENT_CURRENT)),
    http.get('/api/system-descriptions/', () => HttpResponse.json(paginated(CURRENT_SEED_SYSTEMS))),
    http.get('/api/fewshots/', () => HttpResponse.json(paginated([]))),
    http.get('/api/phase-runs/', ({ request }) => {
      const url = new URL(request.url);
      expect(url.searchParams.get('pipeline_run')).toBe('501');
      return HttpResponse.json(paginated(phaseRuns));
    })
  );
}

beforeEach(() => {
  authState.currentUserEmail = 'admin@example.com'; // staff
});

describe('RunDetail', () => {
  it('shows live progress per phase run, polling while active and stopping once every run is terminal', async () => {
    let pipelineRunPolls = 0;
    let phaseRunPolls = 0;
    const run = pipelineRun({ status: 'running' });
    server.use(
      http.get('/api/pipeline-runs/501/', () => {
        pipelineRunPolls += 1;
        return HttpResponse.json({ ...run, status: pipelineRunPolls >= 2 ? 'completed' : 'running' });
      }),
      http.get('/api/experiments/1/', () => HttpResponse.json(EXPERIMENT_CURRENT)),
      http.get('/api/system-descriptions/', () => HttpResponse.json(paginated(CURRENT_SEED_SYSTEMS))),
      http.get('/api/fewshots/', () => HttpResponse.json(paginated([]))),
      http.get('/api/phase-runs/', () => {
        phaseRunPolls += 1;
        const status = phaseRunPolls >= 2 ? 'completed' : 'running';
        return HttpResponse.json(paginated([phaseRun({ status, progress: { done: 2, total: 2, failed: 0, percent: 100 } })]));
      })
    );

    vi.useFakeTimers();
    try {
      renderApp({ initialEntries: ['/pipeline-runs/501'] });

      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
      expect(pipelineRunPolls).toBe(1);
      expect(phaseRunPolls).toBe(1);

      await act(async () => {
        await vi.advanceTimersByTimeAsync(RUN_POLL_INTERVAL_MS + 50);
      });
      expect(pipelineRunPolls).toBe(2);
      expect(phaseRunPolls).toBe(2);

      // Both are now terminal ("completed") - no further poll, ever.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(RUN_POLL_INTERVAL_MS * 5);
      });
      expect(pipelineRunPolls).toBe(2);
      expect(phaseRunPolls).toBe(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows isolation evidence per call, with variant A's state legible at a glance", async () => {
    const user = userEvent.setup();
    const run = pipelineRun({ status: 'completed', finished_at: '2026-09-10T00:10:00Z' });
    const variantA = phaseRun({ id: 9001, variant: 'A', status: 'completed', leak_scan_ran: true, guarded_by: [2] });
    const variantB = phaseRun({ id: 9002, variant: 'B', status: 'completed', leak_scan_ran: false, guarded_by: [] });
    installBaseFixtures({ run, phaseRuns: [variantA, variantB] });
    server.use(
      http.get('/api/phase-runs/9001/calls/', () =>
        HttpResponse.json(paginated([call({ id: 1, leak_scan_ran: true, guarded_by: 2 })]))
      ),
      http.get('/api/phase-runs/9002/calls/', () =>
        HttpResponse.json(paginated([call({ id: 2, leak_scan_ran: false, guarded_by: null })]))
      )
    );

    renderApp({ initialEntries: ['/pipeline-runs/501'] });

    const variantARow = (await screen.findByText(/phase 2.*variant a/i)).closest('[data-testid="phase-run-row"]');
    expect(within(variantARow).getByText(/isolation verified/i)).toBeInTheDocument();

    await user.click(within(variantARow).getByRole('button', { name: /view calls/i }));
    const callRow = await screen.findByTestId('call-row-1');
    expect(within(callRow).getByText(/leak scan ran/i)).toBeInTheDocument();
    expect(within(callRow).getByText(/guarded by taxonomy #2/i)).toBeInTheDocument();

    const variantBRow = screen.getByText(/phase 2.*variant b/i).closest('[data-testid="phase-run-row"]');
    // Variant B legitimately never runs the leak backstop (it has the
    // taxonomy on purpose) - this must NOT read as an alarm the way a
    // false leak_scan_ran on variant A would.
    expect(within(variantBRow).queryByText(/isolation verified/i)).not.toBeInTheDocument();
    expect(within(variantBRow).queryByText(/isolation.*not verified|isolation.*missing/i)).not.toBeInTheDocument();
  });

  it('flags variant A missing its isolation evidence as an alarm, not a neutral fact', async () => {
    const run = pipelineRun({ status: 'failed', finished_at: '2026-09-10T00:10:00Z' });
    const variantA = phaseRun({ id: 9001, variant: 'A', status: 'failed', leak_scan_ran: false, guarded_by: [] });
    installBaseFixtures({ run, phaseRuns: [variantA] });

    renderApp({ initialEntries: ['/pipeline-runs/501'] });

    const variantARow = (await screen.findByText(/phase 2.*variant a/i)).closest('[data-testid="phase-run-row"]');
    expect(within(variantARow).getByText(/isolation.*not verified|scan did not run/i)).toBeInTheDocument();
  });

  it('shows the pinned configuration distinctly from the experiment\'s current configuration', async () => {
    const run = pipelineRun({ status: 'completed' });
    installBaseFixtures({ run, phaseRuns: [] });

    renderApp({ initialEntries: ['/pipeline-runs/501'] });

    expect(await screen.findByText(/pinned to this repetition/i)).toBeInTheDocument();
    expect(screen.getByText(/pinned oldsmar \(as launched\)/i)).toBeInTheDocument();

    expect(await screen.findByText(/experiment.s current configuration/i)).toBeInTheDocument();
    expect(await screen.findByText(/current oldsmar \(edited since launch\)/i)).toBeInTheDocument();

    // Never one where the other belongs.
    const pinnedPanel = screen.getByTestId('pinned-config');
    const currentPanel = screen.getByTestId('current-config');
    expect(within(pinnedPanel).queryByText(/current oldsmar/i)).not.toBeInTheDocument();
    expect(within(currentPanel).queryByText(/pinned oldsmar/i)).not.toBeInTheDocument();
  });

  it('the call inspector shows the full rendered prompt beside the verbatim response, untruncated and copyable', async () => {
    const user = userEvent.setup();
    const run = pipelineRun({ status: 'completed' });
    const variantA = phaseRun({ id: 9001, variant: 'A', status: 'completed' });
    installBaseFixtures({ run, phaseRuns: [variantA] });
    const longResponse = 'X'.repeat(5000);
    server.use(
      http.get('/api/phase-runs/9001/calls/', () =>
        HttpResponse.json(paginated([call({ id: 1, rendered_system_prompt: 'SYS', rendered_user_prompt: 'USR', raw_response: longResponse })]))
      )
    );

    renderApp({ initialEntries: ['/pipeline-runs/501'] });

    await user.click(await screen.findByRole('button', { name: /view calls/i }));
    await user.click(await screen.findByRole('button', { name: /^inspect$/i }));

    expect(screen.getByText('SYS')).toBeInTheDocument();
    expect(screen.getByText('USR')).toBeInTheDocument();
    const responseBlock = screen.getByText(longResponse);
    expect(responseBlock.textContent).toHaveLength(5000); // untruncated
    // CodeBlock (task 5) already provides a Copy button next to each panel.
    expect(screen.getAllByRole('button', { name: /^copy$/i }).length).toBeGreaterThanOrEqual(3);
  });

  it('the call inspector lists every call, not just the first 50 (fails if only page 1 is read)', async () => {
    const user = userEvent.setup();
    const run = pipelineRun({ status: 'completed' });
    const variantA = phaseRun({ id: 9001, variant: 'A', status: 'completed' });
    installBaseFixtures({ run, phaseRuns: [variantA] });
    // 55 calls under one phase run - spec §9 promises this endpoint is
    // "every call"; one page (PAGE_SIZE=50) would silently hide the last 5.
    const calls = Array.from({ length: 55 }, (_, index) => call({ id: index + 1, sequence: index + 1 }));
    server.use(
      http.get('/api/phase-runs/9001/calls/', ({ request }) => HttpResponse.json(paginate(calls, request)))
    );

    renderApp({ initialEntries: ['/pipeline-runs/501'] });

    await user.click(await screen.findByRole('button', { name: /view calls/i }));

    expect(await screen.findByTestId('call-row-55')).toBeInTheDocument();
  });

  it('offers cancel while pending/queued/running with the no-interrupt caveat, and hides it once terminal', async () => {
    const running = pipelineRun({ status: 'running' });
    installBaseFixtures({ run: running, phaseRuns: [] });

    const { unmount } = renderApp({ initialEntries: ['/pipeline-runs/501'] });

    const cancelButton = await screen.findByRole('button', { name: /cancel run/i });
    expect(cancelButton).toBeInTheDocument();
    expect(screen.getByText(/does not interrupt a worker/i)).toBeInTheDocument();
    expect(screen.getByText(/marks.*dead so it can be deleted/i)).toBeInTheDocument();
    unmount();

    const completed = pipelineRun({ status: 'completed', finished_at: '2026-09-10T01:00:00Z' });
    installBaseFixtures({ run: completed, phaseRuns: [] });
    renderApp({ initialEntries: ['/pipeline-runs/501'] });

    await screen.findByText(/completed/i);
    expect(screen.queryByRole('button', { name: /cancel run/i })).not.toBeInTheDocument();
  });

  it('cancelling calls the API and reflects the cancelled status', async () => {
    const user = userEvent.setup();
    const run = pipelineRun({ status: 'running' });
    installBaseFixtures({ run, phaseRuns: [] });
    server.use(
      http.post(
        '/api/pipeline-runs/501/cancel/',
        guardedStaffWrite(() =>
          HttpResponse.json({
            id: 501,
            status: 'cancelled',
            detail: 'Cancelling marks a run dead so it can be deleted; it does NOT interrupt a worker already executing it.',
          })
        )
      )
    );

    renderApp({ initialEntries: ['/pipeline-runs/501'] });
    await user.click(await screen.findByRole('button', { name: /cancel run/i }));

    expect(await screen.findByText(/cancelling marks a run dead/i)).toBeInTheDocument();
  });

  it('offers retry on a failed phase-3 run, and shows the API 409 message for a cancelled/in-flight run', async () => {
    const user = userEvent.setup();
    const run = pipelineRun({ status: 'failed', finished_at: '2026-09-10T00:10:00Z' });
    const failedLabelling = phaseRun({ id: 9003, phase: 3, variant: '', status: 'failed', error: 'Some scenarios failed.', leak_scan_ran: false, guarded_by: [] });
    installBaseFixtures({ run, phaseRuns: [failedLabelling] });
    server.use(
      http.post(
        '/api/phase-runs/9003/retry/',
        guardedStaffWrite(() =>
          HttpResponse.json(
            { detail: 'This run was cancelled and cannot be retried.', code: 'retry_run_cancelled' },
            { status: 409 }
          )
        )
      )
    );

    renderApp({ initialEntries: ['/pipeline-runs/501'] });
    const retryButton = await screen.findByRole('button', { name: /^retry$/i });
    await user.click(retryButton);

    expect(await screen.findByText(/this run was cancelled and cannot be retried/i)).toBeInTheDocument();
  });

  it('per-run export downloads use the Content-Disposition filename for both JSON and CSV', async () => {
    const user = userEvent.setup();
    const run = pipelineRun({ status: 'completed' });
    installBaseFixtures({ run, phaseRuns: [] });

    const created = [];
    const originalCreateObjectURL = window.URL.createObjectURL;
    const originalRevokeObjectURL = window.URL.revokeObjectURL;
    window.URL.createObjectURL = vi.fn((blob) => {
      created.push(blob);
      return 'blob:mock-url';
    });
    window.URL.revokeObjectURL = vi.fn();
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});

    // The response body here is a plain STRING, deliberately never a
    // `Blob` - jsdom's own `Blob` does not survive the trip through msw's
    // Node interceptors (`@mswjs/interceptors` reconstructs the response
    // with undici's OWN `Response`/body-extraction internals, which call
    // `.stream()` on the body - a method jsdom's Blob does not implement -
    // "TypeError: object.stream is not a function"; the same realm
    // mismatch Resources.test.jsx already hit for `FormData`/`File`).
    // A string body sidesteps that entirely: msw hands it to jsdom's XHR
    // exactly like every other mocked response in this app, and it is
    // jsdom's OWN `responseType: 'blob'` support - not msw - that turns it
    // into the `Blob` `apiClient` actually receives.
    server.use(
      http.get('/api/pipeline-runs/501/export/', ({ request }) => {
        const format = new URL(request.url).searchParams.get('format');
        if (format === 'csv') {
          return new HttpResponse('zip-bytes', {
            headers: {
              'Content-Type': 'application/zip',
              'Content-Disposition': 'attachment; filename="baseline-rep1-export.zip"',
            },
          });
        }
        return new HttpResponse('{"ok":true}', {
          headers: {
            'Content-Type': 'application/json',
            'Content-Disposition': 'attachment; filename="baseline-rep1-export.json"',
          },
        });
      })
    );

    try {
      renderApp({ initialEntries: ['/pipeline-runs/501'] });

      await user.click(await screen.findByRole('button', { name: /export json/i }));
      await vi.waitFor(() => expect(clickSpy).toHaveBeenCalledTimes(1));
      let anchor = clickSpy.mock.contexts[0];
      expect(anchor.download).toBe('baseline-rep1-export.json');

      await user.click(screen.getByRole('button', { name: /export csv/i }));
      await vi.waitFor(() => expect(clickSpy).toHaveBeenCalledTimes(2));
      anchor = clickSpy.mock.contexts[1];
      expect(anchor.download).toBe('baseline-rep1-export.zip');
    } finally {
      window.URL.createObjectURL = originalCreateObjectURL;
      window.URL.revokeObjectURL = originalRevokeObjectURL;
      clickSpy.mockRestore();
    }
  });

  it('deleting a run asks for confirmation and states its generated material goes with it', async () => {
    const user = userEvent.setup();
    const run = pipelineRun({ status: 'completed' });
    installBaseFixtures({ run, phaseRuns: [] });
    let deleteCalled = false;
    server.use(
      http.delete(
        '/api/pipeline-runs/501/',
        guardedStaffWrite(() => {
          deleteCalled = true;
          return new HttpResponse(null, { status: 204 });
        })
      )
    );

    renderApp({ initialEntries: ['/pipeline-runs/501'] });

    await user.click(await screen.findByRole('button', { name: /^delete run$/i }));

    const dialog = await screen.findByRole('alertdialog');
    expect(dialog).toHaveTextContent(/generated material/i);
    expect(deleteCalled).toBe(false); // not yet - only asked, not acted on

    await user.click(within(dialog).getByRole('button', { name: /^confirm$/i }));

    await vi.waitFor(() => expect(deleteCalled).toBe(true));
  });

  it('hides cancel/delete/retry for a non-staff viewer, who can still export', async () => {
    authState.currentUserEmail = 'researcher@example.com'; // not staff
    const run = pipelineRun({ status: 'running' });
    const failedLabelling = phaseRun({ id: 9003, phase: 3, variant: '', status: 'failed', error: 'x', leak_scan_ran: false, guarded_by: [] });
    installBaseFixtures({ run, phaseRuns: [failedLabelling] });

    renderApp({ initialEntries: ['/pipeline-runs/501'] });

    await screen.findByText(/repetition 1/i);
    expect(screen.queryByRole('button', { name: /cancel run/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^delete run$/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^retry$/i })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /export json/i })).toBeInTheDocument();
  });
});
