import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { authState, server } from '../../../test/server';
import { buildAnalysis, buildScenario, mockAnalyses } from '../../../test/analysesApi';
import { renderApp } from '../../../test/renderApp';

const HEADINGS = [
  'Purpose and scope',
  'Elements',
  'People and organisations',
  'Data flows',
  'Settings and procedures in force',
  'What is not in place',
];

function open(path = '/analyses/1/system', email = 'admin@example.com') {
  authState.currentUserEmail = email;
  return renderApp({ initialEntries: [path] });
}

const nameBox = () => screen.findByLabelText('System name');
const descriptionBox = () => screen.getByLabelText('What system would you like to examine?');

describe('System step', () => {
  // Leaving saves, so let that request land before the next test's handlers replace these.
  afterEach(async () => {
    cleanup();
    await new Promise((resolve) => setTimeout(resolve, 60));
  });

  it('creates a new analysis on the first save, once', async () => {
    const store = mockAnalyses({ analyses: [] });
    const user = userEvent.setup();
    open('/analyses/new');

    await user.type(await nameBox(), 'Flood Sensors');
    expect(store.requests).toHaveLength(0);
    await user.type(descriptionBox(), 'Sensors on the wadi.');
    await user.tab();

    await waitFor(() => expect(store.analyses[0]?.description).toBe('Sensors on the wadi.'));
    expect(store.requests.filter((request) => request.method === 'post')).toHaveLength(1);
    expect(store.analyses[0].name).toBe('Flood Sensors');
  });

  it('keeps what was typed while the first save was in flight', async () => {
    const store = mockAnalyses({ analyses: [] });
    const user = userEvent.setup();
    open('/analyses/new');

    await user.type(await nameBox(), 'Flood');
    await user.tab();
    await user.type(descriptionBox(), 'Typed during the save.');
    await user.tab();

    await waitFor(() => expect(store.analyses[0]?.description).toBe('Typed during the save.'));
    expect(descriptionBox()).toHaveValue('Typed during the save.');
  });

  it('autosaves 800 ms after typing', async () => {
    const store = mockAnalyses();
    open();

    await nameBox();
    fireEvent.change(descriptionBox(), { target: { value: 'New words.' } });
    expect(store.requests).toHaveLength(0);

    await waitFor(() => expect(store.analyses[0].description).toBe('New words.'), { timeout: 2000 });
  });

  it('saves before moving to Generate', async () => {
    const store = mockAnalyses();
    open();

    await nameBox();
    fireEvent.change(descriptionBox(), { target: { value: 'Last keystrokes.' } });
    fireEvent.click(screen.getByRole('button', { name: /Continue to extraction/ }));

    expect(await screen.findByRole('heading', { name: 'Extract the system’s values' })).toBeInTheDocument();
    expect(store.analyses[0].description).toBe('Last keystrokes.');
  });

  it('saves before navigating with the stepper', async () => {
    const store = mockAnalyses();
    open();

    await nameBox();
    fireEvent.change(descriptionBox(), { target: { value: 'Via the stepper.' } });
    fireEvent.click(screen.getByRole('button', { name: /^Extract/ }));

    expect(await screen.findByRole('heading', { name: 'Extract the system’s values' })).toBeInTheDocument();
    expect(store.analyses[0].description).toBe('Via the stepper.');
  });

  it('saves when leaving by the main navigation', async () => {
    const store = mockAnalyses();
    open();

    await nameBox();
    fireEvent.change(descriptionBox(), { target: { value: 'Left the page.' } });
    fireEvent.click(screen.getByRole('link', { name: 'Analyses' }));

    await waitFor(() => expect(store.analyses[0].description).toBe('Left the page.'));
  });

  it('shows a failed save, keeps the text and stays on the step', async () => {
    mockAnalyses();
    server.use(http.patch('/api/analyses/1/', () => HttpResponse.json({ detail: 'Database is down.' }, { status: 503 })));
    open();

    await nameBox();
    fireEvent.change(descriptionBox(), { target: { value: 'Precious text.' } });
    fireEvent.click(screen.getByRole('button', { name: /Continue to extraction/ }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Database is down.');
    expect(descriptionBox()).toHaveValue('Precious text.');
  });

  it('inserts the six headings without erasing text', async () => {
    mockAnalyses();
    const user = userEvent.setup();
    open();

    await nameBox();
    await user.click(screen.getByRole('button', { name: 'Insert an outline' }));

    const text = descriptionBox().value;
    expect(text).toContain('An app lets residents pay for parking.');
    for (const heading of HEADINGS) expect(text).toContain(heading);
  });

  it('asks before the example replaces text', async () => {
    mockAnalyses();
    const user = userEvent.setup();
    open();

    await nameBox();
    await user.click(screen.getByRole('button', { name: 'Try an example' }));
    expect(descriptionBox()).toHaveValue('An app lets residents pay for parking.');
    await user.click(screen.getByRole('button', { name: 'Replace with the example' }));

    expect(descriptionBox().value).toMatch(/^SmartStop is a city service/);
    expect(descriptionBox().value).toContain('Two-step login is not used.');
    expect(screen.getByLabelText('System name')).toHaveValue('Smart Parking');
  });

  it('shows why it is locked and starts new from the description', async () => {
    const store = mockAnalyses({ analyses: [buildAnalysis({ locked: true, scenarios: [buildScenario()] })] });
    const user = userEvent.setup();
    open();

    expect(await screen.findByText(/Locked because scenarios were generated from it/)).toBeInTheDocument();
    expect(screen.queryByLabelText('What system would you like to examine?')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Start new from this description' }));

    expect(await screen.findByDisplayValue('Smart Parking (2)')).toBeInTheDocument();
    expect(store.requests[0].path).toBe('/api/analyses/1/start-new/');
  });

  it('shows non-staff a read-only description', async () => {
    mockAnalyses();
    open('/analyses/1/system', 'researcher@example.com');

    expect(await screen.findByText('An app lets residents pay for parking.')).toBeInTheDocument();
    expect(screen.queryByLabelText('System name')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Start new/ })).not.toBeInTheDocument();
  });

  it('fits the description box to the screen and scrolls inside it', async () => {
    mockAnalyses();
    open();

    await nameBox();

    expect(descriptionBox().className).toMatch(/h-\[calc\(100dvh/);
    expect(descriptionBox().className).toMatch(/overflow-y-auto/);
  });

  it('fits a locked description to the screen too', async () => {
    mockAnalyses({ analyses: [buildAnalysis({ locked: true, scenarios: [buildScenario()] })] });
    open();

    const text = await screen.findByText('An app lets residents pay for parking.');

    expect(text.className).toMatch(/max-h-\[calc\(100dvh/);
    expect(text.className).toMatch(/overflow-y-auto/);
  });

  it('sets dir="auto" on free-text fields', async () => {
    mockAnalyses();
    open();

    expect(await nameBox()).toHaveAttribute('dir', 'auto');
    expect(descriptionBox()).toHaveAttribute('dir', 'auto');
  });
});
