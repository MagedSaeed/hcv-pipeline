import { describe, expect, it } from 'vitest';
import { RUN_POLL_INTERVAL_MS, isTerminalRunStatus, runsRefetchInterval } from '../queries';

// This is the pure decision at the heart of "polling must stop": a fixed
// interval that never stops hammers the API forever on a finished
// experiment. Tested directly as a pure function (rather than only through
// a full component + fake-timer integration test) because the decision
// itself - which statuses are terminal, and what an empty/undefined run
// list means - is what must never regress, independent of how any given
// screen wires it into `refetchInterval`.
describe('isTerminalRunStatus', () => {
  it('treats completed, failed and cancelled as terminal', () => {
    expect(isTerminalRunStatus('completed')).toBe(true);
    expect(isTerminalRunStatus('failed')).toBe(true);
    expect(isTerminalRunStatus('cancelled')).toBe(true);
  });

  it('treats pending, queued and running as not terminal', () => {
    expect(isTerminalRunStatus('pending')).toBe(false);
    expect(isTerminalRunStatus('queued')).toBe(false);
    expect(isTerminalRunStatus('running')).toBe(false);
  });
});

describe('runsRefetchInterval', () => {
  it('keeps polling while at least one run is still pending/queued/running', () => {
    expect(
      runsRefetchInterval([{ status: 'completed' }, { status: 'running' }])
    ).toBe(RUN_POLL_INTERVAL_MS);
  });

  it('stops once every run on the page is terminal', () => {
    expect(
      runsRefetchInterval([{ status: 'completed' }, { status: 'cancelled' }, { status: 'failed' }])
    ).toBe(false);
  });

  it('stops when there are no runs at all - nothing to watch', () => {
    expect(runsRefetchInterval([])).toBe(false);
  });

  it('keeps polling before the first fetch has resolved, rather than assuming terminal', () => {
    expect(runsRefetchInterval(undefined)).toBe(RUN_POLL_INTERVAL_MS);
  });
});
