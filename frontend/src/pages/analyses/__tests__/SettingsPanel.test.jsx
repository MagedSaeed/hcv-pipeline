import { describe, expect, it } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { authState, server } from '../../../test/server';
import { buildAnalysis, buildScenario, mockAnalyses, RECOMMENDED } from '../../../test/analysesApi';
import { renderApp } from '../../../test/renderApp';

const HINT = 'This model may ignore temperature';

async function openPanel(user, { before } = {}) {
  authState.currentUserEmail = 'admin@example.com';
  renderApp({ initialEntries: ['/analyses/1/generate'] });
  before?.();
  await screen.findByLabelText('Number of scenarios');
  await user.click(await screen.findByRole('button', { name: 'Change' }));
  return screen.findByRole('dialog', { name: 'Generation settings' });
}

describe('Settings panel', () => {
  it('keeps temperature editable and shows the hint for a model without support', async () => {
    const store = mockAnalyses();
    const user = userEvent.setup();
    const panel = await openPanel(user);

    expect(await within(panel).findByText(HINT)).toBeInTheDocument();
    const temperature = within(panel).getByLabelText('Temperature');
    expect(temperature).toBeEnabled();
    expect(temperature).toHaveValue(0.8);
    fireEvent.change(temperature, { target: { value: '1.2' } });

    await waitFor(() => expect(store.analyses[0].generation_settings.temperature).toBe(1.2));
  });

  it('shows no hint for a model with support', async () => {
    mockAnalyses({
      analyses: [buildAnalysis({ generation_settings: { ...RECOMMENDED.generation, model: 'qwen/qwen-plus' } })],
    });
    const user = userEvent.setup();
    const panel = await openPanel(user);

    expect(await within(panel).findByDisplayValue(/Qwen Plus/)).toBeInTheDocument();
    expect(within(panel).queryByText(HINT)).not.toBeInTheDocument();
  });

  it('shows no hint when the catalogue fails to load', async () => {
    mockAnalyses();
    server.use(http.get('/api/models/', () => HttpResponse.json({ detail: 'down' }, { status: 502 })));
    const user = userEvent.setup();
    const panel = await openPanel(user);

    expect(await within(panel).findByText(/Model catalogue unavailable/)).toBeInTheDocument();
    expect(within(panel).queryByText(HINT)).not.toBeInTheDocument();
  });

  it('refuses a temperature outside 0 to 2', async () => {
    const store = mockAnalyses();
    const user = userEvent.setup();
    const panel = await openPanel(user);

    fireEvent.change(within(panel).getByLabelText('Temperature'), { target: { value: '3' } });

    expect(within(panel).getByText('Choose a number from 0 to 2.')).toBeInTheDocument();
    expect(store.requests).toHaveLength(0);
  });

  it('tags the research prompt, selects a listed one, shows Custom and resets', async () => {
    const store = mockAnalyses();
    const user = userEvent.setup();
    const panel = await openPanel(user);

    const research = within(panel).getByRole('radio', { name: /Zero-shot Variant B/ });
    expect(research).toBeChecked();
    expect(research.closest('label')).toHaveTextContent('Recommended');
    const diverse = within(panel).getByRole('radio', { name: /Diverse-generation-variant-b/ });
    expect(diverse.closest('label')).toHaveTextContent('writes control scenarios, not marked');

    await user.click(diverse);
    await waitFor(() => expect(store.analyses[0].generation_settings.prompt).toBe(11));
    await waitFor(() => expect(screen.getByTestId('setup-summary')).toHaveTextContent('Custom'));

    await user.click(within(panel).getByRole('button', { name: 'Reset to recommended' }));
    await waitFor(() => expect(store.analyses[0].generation_settings).toEqual(RECOMMENDED.generation));
  });

  it('selects examples, and None returns to zero-shot', async () => {
    const store = mockAnalyses();
    const user = userEvent.setup();
    const panel = await openPanel(user);

    const none = within(panel).getByRole('radio', { name: /None, zero-shot/ });
    expect(none).toBeChecked();
    await user.click(within(panel).getByRole('radio', { name: /Choose from the list/ }));
    await user.click(within(panel).getByRole('checkbox', { name: /oldsmar/ }));
    await waitFor(() => expect(store.analyses[0].generation_settings.examples).toEqual([21]));

    await user.click(none);
    await waitFor(() => expect(store.analyses[0].generation_settings.examples).toEqual([]));
  });

  it('disables entries with errors and gives the reason', async () => {
    mockAnalyses();
    const user = userEvent.setup();
    const panel = await openPanel(user);

    expect(within(panel).getByRole('radio', { name: /Broken/ })).toBeDisabled();
    expect(within(panel).getByText('resource("x") does not exist.')).toBeInTheDocument();
    await user.click(within(panel).getByRole('radio', { name: /Choose from the list/ }));
    expect(within(panel).getByRole('checkbox', { name: /foreign/ })).toBeDisabled();
    expect(within(panel).getByText("Cites HCV-99, which this taxonomy doesn't have.")).toBeInTheDocument();
  });

  it('chooses another taxonomy only until generation starts', async () => {
    const taxonomies = [
      { id: 1, vocabulary_id: 'HCV-21', version: '1.1' },
      { id: 2, vocabulary_id: 'HCV-21', version: '1.2' },
    ];
    const store = mockAnalyses();
    server.use(
      http.get('/api/taxonomies/', () => HttpResponse.json({ count: 2, next: null, previous: null, results: taxonomies }))
    );
    const user = userEvent.setup();
    const panel = await openPanel(user);

    await within(panel).findByRole('option', { name: 'HCV-21 v1.1' });
    await user.selectOptions(within(panel).getByLabelText('Taxonomy'), '1');

    await waitFor(() => expect(store.requests[0]?.body).toEqual({ taxonomy: 1 }));
  });

  it('offers zero-shot labelling when the examples do not fit the taxonomy', async () => {
    const store = mockAnalyses();
    server.use(
      http.get('/api/taxonomies/', () =>
        HttpResponse.json({ count: 1, next: null, previous: null, results: [{ id: 9, vocabulary_id: 'OTHER', version: '1' }] })
      ),
      http.patch('/api/analyses/1/', async ({ request }) => {
        const body = await request.json();
        if (!body.labelling_settings) {
          return HttpResponse.json({ labelling_settings: ['oldsmar-sc_01 cites HCV-17.'] }, { status: 400 });
        }
        Object.assign(store.analyses[0], { labelling_settings: body.labelling_settings });
        return HttpResponse.json(store.analyses[0]);
      })
    );
    const user = userEvent.setup();
    const panel = await openPanel(user);

    await within(panel).findByRole('option', { name: 'OTHER v1' });
    await user.selectOptions(within(panel).getByLabelText('Taxonomy'), '9');
    await user.click(await within(panel).findByRole('button', { name: 'Switch labelling to zero-shot and use it' }));

    await waitFor(() => expect(store.analyses[0].labelling_settings.examples).toEqual([]));
  });

  it('fixes the taxonomy once locked', async () => {
    mockAnalyses({ analyses: [buildAnalysis({ locked: true, scenarios: [buildScenario()] })] });
    const user = userEvent.setup();
    const panel = await openPanel(user);

    expect(within(panel).getByText('HCV-21 v1.2')).toBeInTheDocument();
    expect(within(panel).queryByLabelText('Taxonomy')).not.toBeInTheDocument();
  });
});
