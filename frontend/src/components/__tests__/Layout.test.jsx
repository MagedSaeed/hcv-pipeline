import { describe, expect, it } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { authState } from '../../test/server';
import { mockAnalyses } from '../../test/analysesApi';
import { renderApp } from '../../test/renderApp';

const RESEARCH_LINKS = ['Dashboard', 'Resources', 'Taxonomy', 'Prompts', 'Labels', 'Settings'];

function open(path) {
  mockAnalyses();
  authState.currentUserEmail = 'admin@example.com';
  return renderApp({ initialEntries: [path] });
}

describe('Top bar', () => {
  it('shows only the two modes on the analyses screens', async () => {
    open('/analyses');

    await screen.findByRole('heading', { name: 'Your analyses' });
    const modes = screen.getByRole('navigation', { name: 'Main navigation' });
    expect(within(modes).getAllByRole('link').map((link) => link.textContent)).toEqual(['Analyses', 'Experiment Lab']);
    expect(within(modes).getByRole('link', { name: 'Analyses' })).toHaveAttribute('aria-current', 'page');
    expect(screen.queryByRole('navigation', { name: 'Experiment Lab navigation' })).not.toBeInTheDocument();
    for (const name of RESEARCH_LINKS) expect(screen.queryByRole('link', { name })).not.toBeInTheDocument();
  });

  it('opens the research screens with their own menu', async () => {
    open('/analyses');
    const user = userEvent.setup();

    await user.click(await screen.findByRole('link', { name: 'Experiment Lab' }));

    expect(await screen.findByRole('heading', { name: /dashboard/i })).toBeInTheDocument();
    const research = screen.getByRole('navigation', { name: 'Experiment Lab navigation' });
    expect(within(research).getAllByRole('link').map((link) => link.textContent)).toEqual(RESEARCH_LINKS);
    expect(screen.getByRole('link', { name: 'Experiment Lab' })).toHaveAttribute('aria-current', 'page');
  });

  it('marks Experiment Lab on every research screen', async () => {
    open('/taxonomy');

    expect(await screen.findByRole('link', { name: 'Experiment Lab' })).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('navigation', { name: 'Experiment Lab navigation' })).toBeInTheDocument();
  });

  it('returns to the analyses screens', async () => {
    open('/dashboard');
    const user = userEvent.setup();

    await user.click(await screen.findByRole('link', { name: 'Analyses' }));

    expect(await screen.findByRole('heading', { name: 'Your analyses' })).toBeInTheDocument();
    expect(screen.queryByRole('navigation', { name: 'Experiment Lab navigation' })).not.toBeInTheDocument();
  });

  it('hides Settings from non-staff in research mode', async () => {
    mockAnalyses();
    authState.currentUserEmail = 'researcher@example.com';
    renderApp({ initialEntries: ['/dashboard'] });

    const research = await screen.findByRole('navigation', { name: 'Experiment Lab navigation' });
    expect(within(research).queryByRole('link', { name: 'Settings' })).not.toBeInTheDocument();
  });
});
