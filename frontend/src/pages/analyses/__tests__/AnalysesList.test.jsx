import { describe, expect, it } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { authState } from '../../../test/server';
import { buildAnalysis, buildRun, buildScenario, mockAnalyses } from '../../../test/analysesApi';
import { renderApp } from '../../../test/renderApp';

function openList(email = 'admin@example.com') {
  authState.currentUserEmail = email;
  return renderApp({ initialEntries: ['/analyses'] });
}

describe('Your analyses', () => {
  it('lands on Analyses after sign-in and shows it first in the nav', async () => {
    mockAnalyses();
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/login'] });

    await user.type(screen.getByLabelText(/email/i), 'admin@example.com');
    await user.type(screen.getByLabelText(/password/i), 'admin-pass');
    await user.click(screen.getByRole('button', { name: /sign in/i }));

    expect(await screen.findByRole('heading', { name: 'Your analyses' })).toBeInTheDocument();
    const nav = screen.getByRole('navigation', { name: 'Main navigation' });
    expect(within(nav).getAllByRole('link')[0]).toHaveTextContent('Analyses');
  });

  it('sends the root path to Analyses', async () => {
    mockAnalyses();
    authState.currentUserEmail = 'admin@example.com';
    renderApp({ initialEntries: ['/'] });

    expect(await screen.findByRole('heading', { name: 'Your analyses' })).toBeInTheDocument();
  });

  it("lists mine and switches to Everyone's", async () => {
    mockAnalyses({
      analyses: [buildAnalysis(), buildAnalysis({ id: 2, name: 'Clinic Booking', owner_email: 'other@example.com' })],
    });
    const user = userEvent.setup();
    openList();

    expect(await screen.findByRole('link', { name: 'Smart Parking' })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Clinic Booking' })).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: "Everyone's" }));

    expect(await screen.findByRole('link', { name: 'Clinic Booking' })).toBeInTheDocument();
    expect(screen.getByText(/other@example\.com/)).toBeInTheDocument();
  });

  it('reopens at the step where work stopped', async () => {
    mockAnalyses({
      analyses: [
        buildAnalysis({
          locked: true,
          resume_step: 'generate',
          latest_runs: { generate: buildRun({ status: 'completed', done: 1 }), label: null },
          scenarios: [buildScenario()],
        }),
      ],
    });
    const user = userEvent.setup();
    openList();

    await user.click(await screen.findByRole('link', { name: 'Smart Parking' }));

    expect(await screen.findByRole('heading', { name: 'Generate scenarios' })).toBeInTheDocument();
  });

  it('renames from the row menu', async () => {
    const store = mockAnalyses();
    const user = userEvent.setup();
    openList();

    await user.click(await screen.findByRole('button', { name: 'Actions for Smart Parking' }));
    await user.click(screen.getByRole('menuitem', { name: 'Rename' }));
    const input = screen.getByRole('textbox', { name: 'Name' });
    await user.clear(input);
    await user.type(input, 'Riyadh pilot');
    await user.click(screen.getByRole('button', { name: 'Save' }));

    expect(await screen.findByRole('link', { name: 'Riyadh pilot' })).toBeInTheDocument();
    expect(store.requests).toContainEqual({ method: 'patch', path: '/api/analyses/1/', body: { name: 'Riyadh pilot' } });
  });

  it('starts a new analysis from the description', async () => {
    const store = mockAnalyses();
    const user = userEvent.setup();
    openList();

    await user.click(await screen.findByRole('button', { name: 'Actions for Smart Parking' }));
    await user.click(screen.getByRole('menuitem', { name: 'Start new from this description' }));

    expect(await screen.findByDisplayValue('Smart Parking (2)')).toBeInTheDocument();
    expect(screen.getByDisplayValue('An app lets residents pay for parking.')).toBeInTheDocument();
    expect(store.requests[0].path).toBe('/api/analyses/1/start-new/');
  });

  it('deletes after one confirmation', async () => {
    const store = mockAnalyses();
    const user = userEvent.setup();
    openList();

    await user.click(await screen.findByRole('button', { name: 'Actions for Smart Parking' }));
    await user.click(screen.getByRole('menuitem', { name: 'Delete' }));
    expect(store.requests).toHaveLength(0);
    await user.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Delete' }));

    await waitFor(() => expect(screen.queryByRole('link', { name: 'Smart Parking' })).not.toBeInTheDocument());
    expect(store.requests[0].method).toBe('delete');
  });

  it('has a menu that works by keyboard', async () => {
    mockAnalyses();
    const user = userEvent.setup();
    openList();

    const trigger = await screen.findByRole('button', { name: 'Actions for Smart Parking' });
    trigger.focus();
    await user.keyboard('{Enter}');
    expect(screen.getByRole('menuitem', { name: 'Rename' })).toHaveFocus();
    await user.keyboard('{ArrowDown}');
    expect(screen.getByRole('menuitem', { name: 'Start new from this description' })).toHaveFocus();
    await user.keyboard('{Escape}');

    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it('shows non-staff no action buttons', async () => {
    mockAnalyses();
    const user = userEvent.setup();
    openList('researcher@example.com');

    await user.click(await screen.findByRole('button', { name: "Everyone's" }));

    expect(await screen.findByRole('link', { name: 'Smart Parking' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Actions for/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /New analysis/ })).not.toBeInTheDocument();
  });
});
