import { describe, expect, it } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { authState } from '../../../test/server';
import { buildAnalysis, buildRun, buildScenario, mockAnalyses } from '../../../test/analysesApi';
import { renderApp } from '../../../test/renderApp';

const stepPanel = () => screen.getByTestId('step-panel');

describe('Moving between steps', () => {
  it('slides forward from the right, back from the left, and not on first load', async () => {
    mockAnalyses({
      analyses: [
        buildAnalysis({
          locked: true,
          latest_runs: { generate: buildRun({ status: 'completed', done: 1 }), label: null },
          scenarios: [buildScenario()],
        }),
      ],
    });
    authState.currentUserEmail = 'admin@example.com';
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/analyses/1/generate'] });

    await screen.findByRole('heading', { name: 'Generate scenarios' });
    expect(stepPanel().className).not.toMatch(/animate-/);

    await user.click(screen.getByRole('button', { name: /Continue to identification/ }));
    await screen.findByRole('heading', { name: 'Identify vulnerabilities' });
    expect(stepPanel().className).toMatch(/animate-slide-from-right/);
    expect(stepPanel().className).toMatch(/motion-reduce:animate-none/);

    await user.click(screen.getByRole('button', { name: /System/ }));
    await screen.findByText('An app lets residents pay for parking.');
    expect(stepPanel().className).toMatch(/animate-slide-from-left/);
  });
});
