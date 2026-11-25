import { describe, expect, it } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { authState } from '../../../test/server';
import { buildAnalysis, buildRun, buildScenario, mockAnalyses } from '../../../test/analysesApi';
import { renderApp } from '../../../test/renderApp';

const LABEL = { hcv_id: 'HCV-17', name: 'Weak Passwords Practices', evidence: 'one shared password', rationale: 'It is shared.' };

function open(fields = {}, email = 'admin@example.com') {
  const store = mockAnalyses({
    analyses: [buildAnalysis({ locked: true, scenarios: [buildScenario()], ...fields })],
  });
  authState.currentUserEmail = email;
  renderApp({ initialEntries: ['/analyses/1/label'] });
  return store;
}

describe('Label step', () => {
  it('shows Luna Pro, 0.0 and 5 worked examples by default', async () => {
    open();

    const setup = await screen.findByTestId('setup-summary');
    await waitFor(() => expect(setup).toHaveTextContent('GPT-5.6 Luna Pro'));
    expect(setup).toHaveTextContent('Recommended');
    expect(setup).toHaveTextContent('0.0');
    expect(setup).toHaveTextContent('few-shot, 5 worked examples');
  });

  it('lists the five examples as Recommended', async () => {
    open();
    const user = userEvent.setup();

    await screen.findByText('Recommended');
    await user.click(screen.getByRole('button', { name: 'Change' }));
    const panel = await screen.findByRole('dialog', { name: 'Identification settings' });

    const boxes = within(panel).getAllByRole('checkbox');
    expect(boxes).toHaveLength(5);
    for (const box of boxes) expect(box).toBeChecked();
  });

  it('shows labels as polling returns them, under From the scenario', async () => {
    const store = open({ latest_runs: { generate: null, label: buildRun({ kind: 'label', total: 1 }) } });

    expect(await screen.findByText(/Examining scenario 1 of 1/)).toBeInTheDocument();
    const analysis = store.analyses[0];
    analysis.latest_runs.label.status = 'completed';
    analysis.scenarios[0].label_status = 'current';
    analysis.scenarios[0].labels = [LABEL];

    expect(await screen.findByText('Weak Passwords Practices', {}, { timeout: 4000 })).toBeInTheDocument();
    expect(screen.getByText('From the scenario')).toBeInTheDocument();
    expect(screen.getByText(/one shared password/)).toBeInTheDocument();
  });

  it('reads empty, stale and failed scenarios as the spec says', async () => {
    open({
      scenarios: [
        buildScenario({ id: 1, label_status: 'current' }),
        buildScenario({ id: 2, label_status: 'stale', labels: [LABEL] }),
        buildScenario({ id: 3, label_status: 'failed', error: 'Rate limited.' }),
        buildScenario({ id: 4 }),
      ],
    });

    expect(await screen.findByText('No vulnerability found in this scenario')).toBeInTheDocument();
    expect(screen.getByText('Out of date. Identify again to update.')).toBeInTheDocument();
    expect(screen.getByText('Rate limited.')).toBeInTheDocument();
    expect(screen.getByText('Not checked yet')).toBeInTheDocument();
  });

  it('labels what is not current', async () => {
    const store = open({
      scenarios: [buildScenario({ id: 1, label_status: 'current' }), buildScenario({ id: 2 })],
    });
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: 'Identify vulnerabilities in 1 scenario' }));

    await waitFor(() => expect(store.requests[0]?.path).toBe('/api/analyses/1/label/'));
    expect(store.requests[0].body).toEqual({});
  });

  it('sends only_failed from Retry failed', async () => {
    const store = open({ scenarios: [buildScenario({ label_status: 'failed', error: 'Rate limited.' })] });
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: 'Retry failed' }));

    await waitFor(() => expect(store.requests[0]?.body).toEqual({ only_failed: true }));
  });

  it('disables the main button when all are current', async () => {
    open({ scenarios: [buildScenario({ label_status: 'current' })] });

    expect(await screen.findByRole('button', { name: 'Every scenario is checked' })).toBeDisabled();
  });

  it('moves on with Continue to report', async () => {
    open({ scenarios: [buildScenario({ label_status: 'current' })] });

    expect(await screen.findByRole('button', { name: 'Continue to report →' })).toBeInTheDocument();
  });

  it('tags manual scenarios', async () => {
    open({ scenarios: [buildScenario({ is_manual: true })] });

    expect(await screen.findByText('Added manually')).toBeInTheDocument();
  });

  it('shows what was set aside without counting it', async () => {
    open({ scenarios: [buildScenario(), buildScenario({ id: 2, title: 'Set aside one', set_aside: true })] });

    expect(await screen.findByRole('button', { name: 'Identify vulnerabilities in 1 scenario' })).toBeInTheDocument();
    const card = screen.getByText('Set aside one').closest('article');
    expect(within(card).getByText('Set aside, not checked')).toBeInTheDocument();
  });

  it('shows non-staff no actions', async () => {
    open({}, 'researcher@example.com');

    expect(await screen.findByText('Not checked yet')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Change|Identify vulnerabilities in 1|Retry/ })).not.toBeInTheDocument();
  });
});
