import { describe, expect, it } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { authState, server } from '../../../test/server';
import { buildAnalysis, buildRun, buildScenario, mockAnalyses, RECOMMENDED } from '../../../test/analysesApi';
import { renderApp } from '../../../test/renderApp';

function open(email = 'admin@example.com') {
  authState.currentUserEmail = email;
  return renderApp({ initialEntries: ['/analyses/1/generate'] });
}

const generated = (fields = {}) =>
  buildAnalysis({
    locked: true,
    latest_runs: { generate: buildRun({ status: 'completed', done: 1 }), label: null },
    scenarios: [
      buildScenario({ outcome: '40 buses are rerouted.' }),
      buildScenario({ id: 2, scenario_id: 'sc_02', title: 'A contractor keeps a badge', set_aside: true }),
      buildScenario({ id: 101, scenario_id: 'sc_101', title: 'A cleaner finds a key', is_manual: true }),
    ],
    ...fields,
  });

const generateButton = async () =>
  within(await screen.findByRole('group', { name: 'Generate scenarios' })).getByRole('button', { name: 'Generate scenarios' });

describe('Generate step', () => {
  it('shows Recommended, 0.8, zero-shot and a count of 8 by default', async () => {
    mockAnalyses();
    open();

    const setup = await screen.findByTestId('setup-summary');
    await waitFor(() => expect(setup).toHaveTextContent('Claude Sonnet 5'));
    expect(setup).toHaveTextContent('Recommended');
    expect(setup).toHaveTextContent('0.8');
    expect(setup).toHaveTextContent('zero-shot');
    expect(await screen.findByLabelText('Number of scenarios')).toHaveValue(8);
  });

  it('puts a labelled count beside the Generate button', async () => {
    mockAnalyses();
    open();

    const group = await screen.findByRole('group', { name: 'Generate scenarios' });

    expect(within(group).getByLabelText('Number of scenarios')).toHaveValue(8);
    expect(within(group).getByRole('button', { name: 'Generate scenarios' })).toBeEnabled();
    expect(screen.getByText('The description will be locked.')).toBeInTheDocument();
  });

  it('hides the count for a prompt without one', async () => {
    mockAnalyses({ analyses: [buildAnalysis({ generation_settings: { ...RECOMMENDED.generation, prompt: 12 } })] });
    open();

    await waitFor(() => expect(screen.getByTestId('setup-summary')).toHaveTextContent('Custom'));
    expect(screen.queryByLabelText('Number of scenarios')).not.toBeInTheDocument();
  });

  it('refuses a count outside 1 to 20', async () => {
    const store = mockAnalyses();
    open();

    fireEvent.change(await screen.findByLabelText('Number of scenarios'), { target: { value: '21' } });

    expect(await generateButton()).toBeDisabled();
    expect(screen.getByText('Choose a whole number from 1 to 20.')).toBeInTheDocument();
    expect(store.requests).toHaveLength(0);
  });

  it('saves the count before starting', async () => {
    const store = mockAnalyses();
    open();

    fireEvent.change(await screen.findByLabelText('Number of scenarios'), { target: { value: '12' } });
    fireEvent.click(await generateButton());

    await waitFor(() => expect(store.requests).toHaveLength(2));
    expect(store.requests[0].body).toEqual({ generation_settings: { ...RECOMMENDED.generation, count: 12 } });
    expect(store.requests[1].path).toBe('/api/analyses/1/generate/');
  });

  it('does not start when the settings could not be saved', async () => {
    const store = mockAnalyses();
    server.use(http.patch('/api/analyses/1/', () => HttpResponse.json({ detail: 'Not saved.' }, { status: 409 })));
    open();

    fireEvent.change(await screen.findByLabelText('Number of scenarios'), { target: { value: '12' } });
    fireEvent.click(await generateButton());

    expect(await screen.findByRole('alert')).toHaveTextContent('Not saved.');
    expect(store.requests).toHaveLength(0);
  });

  it('shows why a start was refused', async () => {
    mockAnalyses();
    server.use(
      http.post('/api/analyses/1/generate/', () =>
        HttpResponse.json({ detail: 'The job queue is unavailable.', code: 'queue_unavailable' }, { status: 503 })
      )
    );
    open();

    fireEvent.click(await generateButton());

    expect(await screen.findByRole('alert')).toHaveTextContent('The job queue is unavailable.');
  });

  it('shows progress and cancels', async () => {
    const store = mockAnalyses({
      analyses: [buildAnalysis({ locked: true, latest_runs: { generate: buildRun(), label: null } })],
    });
    const user = userEvent.setup();
    open();

    expect(await screen.findByText(/Writing scenarios/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Cancel' }));

    await waitFor(() => expect(store.requests[0]?.path).toBe('/api/analyses/1/cancel/'));
  });

  it('shows the error of a failed run', async () => {
    const failed = buildRun({ status: 'failed', failed: 1, error: 'Not enough OpenRouter credit.' });
    mockAnalyses({ analyses: [buildAnalysis({ latest_runs: { generate: failed, label: null } })] });
    open();

    expect(await screen.findByRole('alert')).toHaveTextContent('Not enough OpenRouter credit.');
  });

  it('shows the notice of a finished run', async () => {
    const run = buildRun({ status: 'completed', notice: 'The model returned 3 scenarios instead of 8.' });
    mockAnalyses({ analyses: [generated({ latest_runs: { generate: run, label: null } })] });
    open();

    expect(await screen.findByText('The model returned 3 scenarios instead of 8.')).toBeInTheDocument();
  });

  it('confirms before generating again', async () => {
    const store = mockAnalyses({ analyses: [generated()] });
    const user = userEvent.setup();
    open();

    await user.click(await screen.findByRole('button', { name: 'Generate again' }));
    expect(store.requests).toHaveLength(0);
    await user.click(screen.getByRole('button', { name: 'Replace scenarios' }));

    await waitFor(() => expect(store.requests[0]?.path).toBe('/api/analyses/1/generate/'));
  });

  it('keeps a set-aside scenario in the list with a tag', async () => {
    mockAnalyses({ analyses: [generated()] });
    open();

    expect(await screen.findByText('2 included, 1 set aside')).toBeInTheDocument();
    const card = screen.getByText('A contractor keeps a badge').closest('article');
    expect(within(card).getByText('Set aside')).toBeInTheDocument();
  });

  it('gives a generated scenario only Set aside', async () => {
    const store = mockAnalyses({ analyses: [generated()] });
    const user = userEvent.setup();
    open();

    await user.click(await screen.findByRole('button', { name: 'Actions for scenario 1' }));
    expect(screen.getAllByRole('menuitem').map((item) => item.textContent)).toEqual(['Set aside']);
    await user.click(screen.getByRole('menuitem', { name: 'Set aside' }));

    await waitFor(() => expect(store.requests[0]?.body).toEqual({ set_aside: true }));
  });

  it('gives a manual scenario Edit, Set aside and Delete', async () => {
    mockAnalyses({ analyses: [generated()] });
    const user = userEvent.setup();
    open();

    await user.click(await screen.findByRole('button', { name: 'Actions for scenario 3' }));

    expect(screen.getAllByRole('menuitem').map((item) => item.textContent)).toEqual(['Edit', 'Set aside', 'Delete']);
  });

  it('edits a manual scenario', async () => {
    const store = mockAnalyses({ analyses: [generated()] });
    const user = userEvent.setup();
    open();

    await user.click(await screen.findByRole('button', { name: 'Actions for scenario 3' }));
    await user.click(screen.getByRole('menuitem', { name: 'Edit' }));
    const narrative = screen.getByRole('textbox', { name: 'What happens?' });
    await user.clear(narrative);
    await user.type(narrative, 'A key is copied.');
    await user.click(screen.getByRole('button', { name: 'Save scenario' }));

    await waitFor(() => expect(store.requests[0]?.body.narrative).toBe('A key is copied.'));
    expect(store.requests[0].path).toBe('/api/analyses/1/scenarios/101/');
  });

  it('adds from the header menu, with only What happens? required', async () => {
    const store = mockAnalyses({ analyses: [generated()] });
    const user = userEvent.setup();
    open();

    await user.click(await screen.findByRole('button', { name: 'Scenario options' }));
    await user.click(screen.getByRole('menuitem', { name: 'Add your own scenario' }));
    const save = screen.getByRole('button', { name: 'Add scenario' });
    expect(save).toBeDisabled();
    await user.type(screen.getByRole('textbox', { name: 'What happens?' }), 'A visitor plugs in a drive.');
    await user.click(save);

    await waitFor(() => expect(store.requests[0]?.body.narrative).toBe('A visitor plugs in a drive.'));
    expect((await screen.findAllByText('A visitor plugs in a drive.')).length).toBeGreaterThan(0);
  });

  it('restores a set-aside scenario from its menu', async () => {
    const store = mockAnalyses({ analyses: [generated()] });
    const user = userEvent.setup();
    open();

    const card = (await screen.findByText('A contractor keeps a badge')).closest('article');
    await user.click(within(card).getByRole('button', { name: /Actions for/ }));
    await user.click(screen.getByRole('menuitem', { name: 'Restore' }));

    await waitFor(() => expect(store.requests[0]?.body).toEqual({ set_aside: false }));
  });

  it('tags only manual cards Added manually', async () => {
    mockAnalyses({ analyses: [generated()] });
    open();

    const card = (await screen.findByText('A cleaner finds a key')).closest('article');

    expect(within(card).getByText('Added manually')).toBeInTheDocument();
    expect(screen.getAllByText('Added manually')).toHaveLength(1);
  });

  it('shows the outcome under the title', async () => {
    mockAnalyses({ analyses: [generated()] });
    open();

    const card = (await screen.findByText('An operator approves a reroute without checking')).closest('article');

    expect(within(card).getByText('Outcome: 40 buses are rerouted.')).toBeInTheDocument();
  });

  it('shows non-staff no actions', async () => {
    mockAnalyses({ analyses: [generated()] });
    open('researcher@example.com');

    expect(await screen.findByText('A cleaner finds a key')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Generate again|Actions for|Change/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Scenario options' })).not.toBeInTheDocument();
  });
});
