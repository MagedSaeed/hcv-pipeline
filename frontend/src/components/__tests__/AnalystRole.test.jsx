import { describe, expect, it } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { authState } from '../../test/server';
import { mockAnalyses } from '../../test/analysesApi';
import { renderApp } from '../../test/renderApp';

function open(path, email = 'analyst@example.com') {
  const store = mockAnalyses();
  authState.currentUserEmail = email;
  renderApp({ initialEntries: [path] });
  return store;
}

describe('Analyst accounts', () => {
  it('see Analyses without the Experiment Lab switch', async () => {
    open('/analyses');

    await screen.findByRole('heading', { name: 'Your analyses' });
    expect(screen.queryByRole('navigation', { name: 'Main navigation' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Experiment Lab' })).not.toBeInTheDocument();
  });

  it('are sent to Analyses from any Experiment Lab address', async () => {
    for (const path of ['/dashboard', '/prompts', '/labels', '/settings', '/experiments/77']) {
      open(path);
      expect(await screen.findByRole('heading', { name: 'Your analyses' })).toBeInTheDocument();
      document.body.innerHTML = '';
    }
  });

  it('create analyses and see only their own, with no scope switch', async () => {
    open('/analyses');

    expect(await screen.findByRole('link', { name: '+ New analysis' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Everyone’s' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Everyone/ })).not.toBeInTheDocument();
  });

  it('can run their analysis: the step controls are theirs', async () => {
    open('/analyses/1/generate');

    expect(await screen.findByRole('button', { name: 'Generate scenarios' })).toBeInTheDocument();
  });
});

describe('The project name', () => {
  it('takes everyone home', async () => {
    open('/analyses/1/system', 'admin@example.com');
    const user = userEvent.setup();

    await user.click(await screen.findByRole('link', { name: 'HCV Pipeline' }));

    await waitFor(() => expect(screen.getByRole('heading', { name: 'Your analyses' })).toBeInTheDocument());
  });
});
