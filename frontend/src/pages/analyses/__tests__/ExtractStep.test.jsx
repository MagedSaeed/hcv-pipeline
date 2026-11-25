import { describe, expect, it } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { authState } from '../../../test/server';
import { buildAnalysis, buildExtraction, buildItem, buildRun, buildScenario, mockAnalyses } from '../../../test/analysesApi';
import { renderApp } from '../../../test/renderApp';

const ITEMS = () => [
  buildItem({ id: 1 }),
  buildItem({ id: 2, field: 'organization', ref: 'MoveAI', value: 'Startup', original_value: 'Startup', problem: 'Not an allowed value for this field.' }),
  buildItem({ id: 3, section: '2. System components', field: 'data_store', ref: 'TripStore', value: 'DataStore', original_value: 'DataStore' }),
  buildItem({ id: 4, kind: 'fact', section: '4. Data flows', field: 'retention_days', ref: 'TripStore', value: '1095', original_value: '1095' }),
  buildItem({ id: 5, kind: 'fact', field: 'vulnerability_factor', ref: 'Residents', value: 'Factor_Disability', original_value: 'Factor_Disability', fact_type: 'Possible', reason: 'Some upload a medical letter.' }),
  buildItem({ id: 6, kind: 'control', section: '8. Controls', field: 'control', ref: 'TripStore', value: 'PasswordControl', original_value: 'PasswordControl', state: 'absent', original_state: 'absent' }),
];

function open(extraction = buildExtraction({ items: ITEMS(), gaps: { not_assessable: [{ area: 'Physical entry', reason: 'No building is described.' }], no_term: [{ statement: 'trip data shared for advertising', reason: '' }] } }), fields = {}, email = 'admin@example.com') {
  const store = mockAnalyses({ analyses: [buildAnalysis({ extraction, ...fields })] });
  authState.currentUserEmail = email;
  renderApp({ initialEntries: ['/analyses/1/extract'] });
  return store;
}

const row = (name) => screen.getByRole('group', { name });

describe('Extract step', () => {
  it('sits second among five steps', async () => {
    open();

    const steps = within(await screen.findByRole('navigation', { name: 'Analysis steps' })).getAllByRole('button');
    expect(steps.map((step) => step.textContent.replace(/[^A-Za-z]/g, ''))).toEqual(['System', 'Extract', 'Generate', 'Label', 'Report']);
    expect(screen.getByRole('heading', { name: 'Extract the system’s values' })).toBeInTheDocument();
  });

  it('shows the recommended extractor', async () => {
    open();

    const setup = await screen.findByTestId('setup-summary');
    await waitFor(() => expect(setup).toHaveTextContent('Recommended'));
    expect(setup).toHaveTextContent('claude-sonnet-5.5');
    expect(setup).toHaveTextContent('0.0');
  });

  it('groups values by section with their fact type, reason and problem', async () => {
    open();

    const people = await screen.findByRole('region', { name: '1. People and organizations' });
    expect(within(people).getByRole('group', { name: 'Residents person or role' })).toBeInTheDocument();
    const factor = row('Residents vulnerability factor');
    expect(within(factor).getByText('Possible')).toBeInTheDocument();
    expect(within(factor).getByText('Some upload a medical letter.')).toBeInTheDocument();
    expect(within(row('MoveAI organization')).getByText('Not an allowed value for this field.')).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '8. Controls' })).toBeInTheDocument();
  });

  it('lays each value out in fixed columns, the control filling its own', async () => {
    open();

    const group = await screen.findByRole('group', { name: 'MoveAI organization' });
    const select = await within(group).findByRole('combobox', { name: 'Value' });

    expect(group.firstElementChild.className).toMatch(/grid-cols-/);
    expect(select.className).toMatch(/w-full/);
  });

  it('edits a value through the allowed choices', async () => {
    const store = open();
    const user = userEvent.setup();

    const select = await within(await screen.findByRole('group', { name: 'MoveAI organization' })).findByRole('combobox', { name: 'Value' });
    expect(within(select).getAllByRole('option').map((o) => o.value)).toContain('PrivateSectorOrganization');
    await user.selectOptions(select, 'PrivateSectorOrganization');

    await waitFor(() => expect(store.requests[0]?.body).toEqual({ value: 'PrivateSectorOrganization' }));
    expect(store.requests[0].path).toBe('/api/analyses/1/items/2/');
  });

  it('edits a number and a control state', async () => {
    const store = open();
    const user = userEvent.setup();

    const days = await within(await screen.findByRole('group', { name: 'TripStore retention days' })).findByRole('spinbutton', { name: 'Value' });
    await user.clear(days);
    await user.type(days, '365');
    await user.tab();
    await user.selectOptions(within(row('TripStore control')).getByRole('combobox', { name: 'State' }), 'present');

    await waitFor(() => expect(store.requests.map((r) => r.body)).toEqual([{ value: '365' }, { state: 'present' }]));
  });

  it('approves and rejects each value', async () => {
    const store = open();
    const user = userEvent.setup();

    await user.click(within(await screen.findByRole('group', { name: 'Residents person or role' })).getByRole('button', { name: 'Approve' }));
    await user.click(within(row('TripStore data store')).getByRole('button', { name: 'Reject' }));

    await waitFor(() => expect(store.requests).toHaveLength(2));
    expect(store.requests.map((r) => [r.path, r.body])).toEqual([
      ['/api/analyses/1/items/1/', { decision: 'approved' }],
      ['/api/analyses/1/items/3/', { decision: 'rejected' }],
    ]);
  });

  it('does not offer Approve for a value with a problem', async () => {
    open();

    expect(within(await screen.findByRole('group', { name: 'MoveAI organization' })).getByRole('button', { name: 'Approve' })).toBeDisabled();
  });

  it('approves all Asserted values at once', async () => {
    const store = open();
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: 'Approve all Asserted' }));

    await waitFor(() => expect(store.requests[0]?.path).toBe('/api/analyses/1/items/approve-asserted/'));
  });

  it('approves a whole section, skipping values with a problem', async () => {
    const store = open();
    const user = userEvent.setup();

    const people = await screen.findByRole('region', { name: '1. People and organizations' });
    await user.click(within(people).getByRole('button', { name: 'Approve section' }));

    await waitFor(() => expect(store.requests[0]?.path).toBe('/api/analyses/1/items/approve-section/'));
    expect(store.requests[0].body).toEqual({ section: '1. People and organizations' });
    await waitFor(() => expect(within(row('Residents vulnerability factor')).getByRole('button', { name: 'Approve' })).toHaveAttribute('aria-pressed', 'true'));
    expect(within(row('MoveAI organization')).getByRole('button', { name: 'Approve' })).toHaveAttribute('aria-pressed', 'false');
    expect(within(row('TripStore data store')).getByRole('button', { name: 'Approve' })).toHaveAttribute('aria-pressed', 'false');
  });

  it('offers no section approval once every value there is decided', async () => {
    open(buildExtraction({ items: [buildItem({ decision: 'approved' })] }));

    const people = await screen.findByRole('region', { name: '1. People and organizations' });
    expect(within(people).queryByRole('button', { name: 'Approve section' })).not.toBeInTheDocument();
  });

  it('shows the approved values as the text the models read', async () => {
    const body = '## Structured description\n\n### 1. People and organizations\n- Residents (Citizen)\n\n## Original description\n\nAn app lets residents pay for parking.\n';
    open(buildExtraction({ items: ITEMS(), body }));
    const user = userEvent.setup();

    await user.click(await screen.findByRole('tab', { name: 'As text' }));

    const panel = screen.getByRole('tabpanel');
    expect(panel).toHaveTextContent('- Residents (Citizen)');
    expect(panel).not.toHaveTextContent('An app lets residents pay for parking.');
    expect(panel).toHaveTextContent('followed by your original description');
    await user.click(screen.getByRole('tab', { name: 'Fields' }));
    expect(screen.getByRole('region', { name: '1. People and organizations' })).toBeInTheDocument();
  });

  it('adds an element to a section', async () => {
    const store = open();
    const user = userEvent.setup();

    const components = await screen.findByRole('region', { name: '2. System components' });
    await user.click(await within(components).findByRole('button', { name: '+ Add a value' }));
    const form = within(components).getByRole('form', { name: 'Add a value' });
    await user.selectOptions(within(form).getByRole('combobox', { name: 'Field' }), 'interface');
    await user.type(within(form).getByRole('textbox', { name: 'Id' }), 'Kiosk');
    await user.selectOptions(within(form).getByRole('combobox', { name: 'Value' }), 'MobileApp');
    await user.click(within(form).getByRole('button', { name: 'Add' }));

    await waitFor(() => expect(store.requests[0]?.path).toBe('/api/analyses/1/items/'));
    expect(store.requests[0].body).toEqual({ field: 'interface', ref: 'Kiosk', value: 'MobileApp' });
    const added = await screen.findByRole('group', { name: 'Kiosk interface' });
    expect(added).toHaveTextContent('Added');
    expect(within(components).queryByRole('form', { name: 'Add a value' })).not.toBeInTheDocument();
  });

  it('adds a control on an extracted element, with its state', async () => {
    const store = open();
    const user = userEvent.setup();

    const controls = await screen.findByRole('region', { name: '8. Controls' });
    await user.click(await within(controls).findByRole('button', { name: '+ Add a value' }));
    const form = within(controls).getByRole('form', { name: 'Add a value' });
    await user.selectOptions(within(form).getByRole('combobox', { name: 'Id' }), 'TripStore');
    await user.selectOptions(within(form).getByRole('combobox', { name: 'Value' }), 'MultiFactorAuthentication');
    await user.selectOptions(within(form).getByRole('combobox', { name: 'State' }), 'present');
    await user.click(within(form).getByRole('button', { name: 'Add' }));

    await waitFor(() =>
      expect(store.requests[0]?.body).toEqual({ field: 'control', ref: 'TripStore', value: 'MultiFactorAuthentication', state: 'present' }),
    );
  });

  it('offers sections with nothing extracted, so a value can be added there', async () => {
    open();

    const use = await screen.findByRole('region', { name: '3. Use and responsibility' });
    expect(await within(use).findByRole('button', { name: '+ Add a value' })).toBeInTheDocument();
  });

  it('deletes only values the analyst added', async () => {
    const store = open(buildExtraction({ items: [...ITEMS(), buildItem({ id: 9, field: 'interface', section: '2. System components', ref: 'Kiosk', value: 'MobileApp', decision: 'approved', added: true })] }));
    const user = userEvent.setup();

    expect(within(await screen.findByRole('group', { name: 'TripStore data store' })).queryByRole('button', { name: 'Delete' })).not.toBeInTheDocument();
    await user.click(within(row('Kiosk interface')).getByRole('button', { name: 'Delete' }));

    await waitFor(() => expect(store.requests[0]).toMatchObject({ method: 'delete', path: '/api/analyses/1/items/9/' }));
    await waitFor(() => expect(screen.queryByRole('group', { name: 'Kiosk interface' })).not.toBeInTheDocument());
  });

  it('renames an element and moves a fact to another element', async () => {
    const store = open();
    const user = userEvent.setup();

    const id = within(await screen.findByRole('group', { name: 'TripStore data store' })).getByRole('textbox', { name: 'Id' });
    await user.clear(id);
    await user.type(id, 'TripDB{Enter}');
    await waitFor(() => expect(store.requests[0]?.body).toEqual({ ref: 'TripDB' }));
    expect(store.requests[0].path).toBe('/api/analyses/1/items/3/');

    await user.selectOptions(within(row('Residents vulnerability factor')).getByRole('combobox', { name: 'Id' }), 'MoveAI');
    await waitFor(() => expect(store.requests[1]?.body).toEqual({ ref: 'MoveAI' }));
  });

  it('lists what the description does not cover', async () => {
    open();

    const gaps = await screen.findByRole('region', { name: 'Not covered by the description' });
    expect(within(gaps).getByText('Physical entry')).toBeInTheDocument();
    expect(within(gaps).getByText('trip data shared for advertising')).toBeInTheDocument();
  });

  it('opens Generate only when nothing is left undecided', async () => {
    open();

    expect(await screen.findByRole('button', { name: 'Continue to generate →' })).toBeDisabled();
    expect(screen.getByText('6 values still need approving or rejecting.')).toBeInTheDocument();
  });

  it('continues to Generate when all are decided', async () => {
    open(buildExtraction({ items: [buildItem({ decision: 'approved' })] }));
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: 'Continue to generate →' }));

    expect(await screen.findByRole('heading', { name: 'Generate scenarios' })).toBeInTheDocument();
  });

  it('starts the first extraction', async () => {
    const store = open(buildExtraction({ status: 'none', items: [] }));
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: 'Extract values' }));

    await waitFor(() => expect(store.requests[0]?.path).toBe('/api/analyses/1/extract/'));
  });

  it('warns when the description changed since extraction', async () => {
    open(buildExtraction({ status: 'stale', items: ITEMS() }));

    expect(await screen.findByText(/The description changed since extraction/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Extract again' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Continue to generate →' })).toBeDisabled();
  });

  it('confirms before extracting again', async () => {
    const store = open();
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: 'Extract again' }));
    expect(store.requests).toHaveLength(0);
    await user.click(screen.getByRole('button', { name: 'Replace the values' }));

    await waitFor(() => expect(store.requests[0]?.path).toBe('/api/analyses/1/extract/'));
  });

  it('shows progress while extracting: the time so far and how long it usually takes', async () => {
    const run = buildRun({ kind: 'extract', created_at: new Date(Date.now() - 65_000).toISOString() });
    open(buildExtraction({ status: 'none', items: [], run }), { latest_runs: { extract: run, generate: null, label: null } });

    const status = await screen.findByRole('status', { name: 'Run progress' });
    expect(status).toHaveTextContent('Reading the description');
    expect(within(status).getByText(/^1:0[5-7]$/)).toHaveAttribute('aria-label', expect.stringMatching(/^Running for 1 minute 0[5-7] seconds$/));
    expect(status).toHaveTextContent('This can take a while');
  });

  it('shows edited values with what the model said', async () => {
    open(buildExtraction({ items: [buildItem({ value: 'SeniorCitizen', edited: true })] }));

    expect(await screen.findByText('Edited · the model said Citizen')).toBeInTheDocument();
  });

  it('marks values that refer to a rejected element', async () => {
    open(buildExtraction({ items: [buildItem({ id: 4, kind: 'fact', field: 'retention_days', ref: 'TripStore', value: '30', refers_to_rejected: true })] }));

    expect(await screen.findByText('Refers to a rejected element')).toBeInTheDocument();
  });

  it('is read-only once generation has started', async () => {
    open(buildExtraction({ items: ITEMS().map((item) => ({ ...item, decision: 'approved', problem: '' })) }), {
      locked: true,
      scenarios: [buildScenario()],
    });

    expect(await screen.findByText(/fixed once generation started/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument();
    expect(screen.queryByRole('combobox', { name: 'Value' })).not.toBeInTheDocument();
  });
});
