import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { authState } from '../../../test/server';
import { buildAnalysis, buildRun, buildScenario, mockAnalyses } from '../../../test/analysesApi';
import { renderApp } from '../../../test/renderApp';
import { stubWideScreen } from '../../../test/screen';

const buildScenarios = () => [
  buildScenario({ id: 1, title: 'Routine shift handover', outcome: 'A session opens for an outsider.' }),
  buildScenario({ id: 2, scenario_id: 'sc_02', title: 'Scheduled maintenance', outcome: 'A setting changes.', set_aside: true }),
  buildScenario({
    id: 101,
    scenario_id: 'sc_101',
    title: 'A cleaner finds a key',
    narrative: 'The cleaner enters behind an officer and copies the key.',
    outcome: '',
    is_manual: true,
    label_status: 'current',
    labels: [{ hcv_id: 'HCV-10', name: 'Tailgating', evidence: 'enters behind', rationale: 'Follows staff in.' }],
  }),
];

let scenarios;

function open(step, report = null) {
  const store = mockAnalyses({
    analyses: [
      buildAnalysis({
        locked: true,
        latest_runs: { generate: buildRun({ status: 'completed', done: 1 }), label: null },
        scenarios: buildScenarios(),
      }),
    ],
    report,
  });
  authState.currentUserEmail = 'admin@example.com';
  stubWideScreen();
  renderApp({ initialEntries: [`/analyses/1/${step}`] });
  return store;
}

const list = () => screen.findByRole('list', { name: 'Scenarios' });
const detail = () => screen.getByRole('region', { name: 'Selected scenario' });

describe('Wide screens: list and detail', () => {
  beforeEach(() => {
    scenarios = buildScenarios();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it('lists every scenario with its tags and shows the first in detail', async () => {
    open('generate');

    const items = within(await list()).getAllByRole('button');
    expect(items.map((item) => item.textContent)).toEqual([
      expect.stringContaining('Routine shift handover'),
      expect.stringContaining('Set aside'),
      expect.stringContaining('Added manually'),
    ]);
    expect(items[0]).toHaveAttribute('aria-current', 'true');
    expect(within(detail()).getByText('Outcome: A session opens for an outsider.')).toBeInTheDocument();
  });

  it('shows the chosen scenario with its actions', async () => {
    const store = open('generate');
    const user = userEvent.setup();

    await user.click(within(await list()).getByRole('button', { name: /A cleaner finds a key/ }));
    expect(within(detail()).getByRole('heading', { name: 'A cleaner finds a key' })).toBeInTheDocument();
    await user.click(within(detail()).getByRole('button', { name: /Actions for/ }));
    await user.click(screen.getByRole('menuitem', { name: 'Set aside' }));

    await waitFor(() => expect(store.requests[0]?.path).toBe('/api/analyses/1/scenarios/101/'));
  });

  it('starts with the list open, collapses it to numbers and remembers that', async () => {
    open('generate');
    const user = userEvent.setup();

    const toggle = await screen.findByRole('button', { name: 'Collapse list' });
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(within(await list()).getByText('Routine shift handover')).toBeInTheDocument();

    await user.click(toggle);

    const collapsed = await list();
    expect(within(collapsed).queryByText('Routine shift handover')).not.toBeInTheDocument();
    const third = within(collapsed).getByRole('button', { name: /A cleaner finds a key/ });
    expect(third).toHaveTextContent('3');
    await user.click(third);
    expect(within(detail()).getByRole('heading', { name: 'A cleaner finds a key' })).toBeInTheDocument();
    expect(window.localStorage.getItem('analyses:list-collapsed')).toBe('true');

    await user.click(screen.getByRole('button', { name: 'Expand list' }));
    expect(within(await list()).getByText('Routine shift handover')).toBeInTheDocument();
  });

  it('animates the list width between two sizes of the same form', async () => {
    open('generate');
    const user = userEvent.setup();

    const grid = (await list()).closest('[data-collapsed]');
    expect(grid.className).toMatch(/grid-cols-\[17rem_minmax\(0,1fr\)\]/);
    expect(grid.className).toMatch(/transition-\[grid-template-columns\]/);

    await user.click(screen.getByRole('button', { name: 'Collapse list' }));

    expect(grid.className).toMatch(/grid-cols-\[3\.5rem_minmax\(0,1fr\)\]/);
  });

  it('keeps the long list inside its own scrolling box', async () => {
    open('generate');

    const scroller = (await list()).parentElement;

    expect(scroller.className).toMatch(/overflow-y-auto/);
    expect(scroller.className).toMatch(/overflow-x-hidden/);
  });

  it('shows labels of the chosen scenario on the Label step', async () => {
    open('label');
    const user = userEvent.setup();

    await user.click(within(await list()).getByRole('button', { name: /A cleaner finds a key/ }));

    const found = within(detail()).getByRole('region', { name: /Vulnerabilities found/ });
    expect(within(found).getByText('Tailgating')).toBeInTheDocument();
    expect(within(found).getByText('enters behind')).toBeInTheDocument();
  });

  it('shows the vulnerabilities beside the list, not in it, on the Label step', async () => {
    open('label');
    const user = userEvent.setup();

    const item = within(await list()).getByRole('button', { name: /A cleaner finds a key/ });
    // The list stays short: vulnerabilities show beside it, not in it.
    expect(within(item).queryByText('Tailgating')).not.toBeInTheDocument();
    await user.click(item);

    const found = within(detail()).getByRole('region', { name: 'Vulnerabilities found (1)' });
    expect(within(found).getByText('HCV-10')).toBeInTheDocument();
    expect(within(found).getByText('Tailgating')).toBeInTheDocument();
    expect(within(found).getByText('Follows staff in.')).toBeInTheDocument();
    expect(within(detail()).getByRole('heading', { name: 'The scenario' })).toBeInTheDocument();
  });

  it('tags each labelled scenario in the list with how many vulnerabilities it has', async () => {
    open('label');

    const items = within(await list()).getAllByRole('button');
    const cleaner = items.find((item) => item.textContent.includes('A cleaner finds a key'));
    const handover = items.find((item) => item.textContent.includes('Routine shift handover'));

    expect(within(cleaner).getByLabelText('1 vulnerability')).toHaveTextContent('1 vulnerability');
    expect(within(handover).queryByLabelText(/vulnerabilit/)).not.toBeInTheDocument();
  });

  it('fades the row of the scenario being checked, and says so', async () => {
    mockAnalyses({
      analyses: [
        buildAnalysis({
          locked: true,
          latest_runs: { generate: null, label: buildRun({ kind: 'label', total: 2, working_on: 1 }) },
          scenarios: buildScenarios(),
        }),
      ],
    });
    authState.currentUserEmail = 'admin@example.com';
    stubWideScreen();
    renderApp({ initialEntries: ['/analyses/1/label'] });

    const items = within(await list()).getAllByRole('button');
    const working = items.find((item) => item.textContent.includes('Routine shift handover'));
    const waiting = items.find((item) => item.textContent.includes('A cleaner finds a key'));
    expect(working).toHaveTextContent('(being checked)');
    expect(working.firstElementChild.className).toMatch(/animate-breathe/);
    expect(waiting.firstElementChild.className).not.toMatch(/animate-breathe/);
  });

  it('jumps from a quote to its words in the scenario text', async () => {
    const scrolled = vi.fn();
    Element.prototype.scrollIntoView = scrolled;
    open('label');
    const user = userEvent.setup();

    await user.click(within(await list()).getByRole('button', { name: /A cleaner finds a key/ }));
    await user.click(within(detail()).getByRole('button', { name: 'Show “enters behind” in the scenario' }));

    expect(scrolled).toHaveBeenCalledTimes(1);
    const target = scrolled.mock.contexts[0];
    expect(target.tagName).toBe('MARK');
    expect(target).toHaveTextContent('enters behind');
    delete Element.prototype.scrollIntoView;
  });

  it('highlights the quoted words in the scenario text', async () => {
    open('label');
    const user = userEvent.setup();

    await user.click(within(await list()).getByRole('button', { name: /A cleaner finds a key/ }));

    const marked = within(detail()).getByText('enters behind', { selector: 'mark' });
    expect(marked).toHaveAttribute('title', 'Tailgating (HCV-10)');
    expect(marked.closest('dd')).toHaveTextContent('The cleaner enters behind an officer and copies the key.');
  });

  it('says Set aside once for a set-aside scenario on the Label step', async () => {
    open('label');

    const item = within(await list()).getByRole('button', { name: /Scheduled maintenance/ });

    expect(within(item).getAllByText('Set aside')).toHaveLength(1);
  });

  it('lists vulnerabilities and opens the scenario behind one', async () => {
    const report = {
      name: 'Smart Parking',
      findings: [
        {
          hcv_id: 'HCV-10',
          name: 'Tailgating',
          scenario_count: 1,
          manual_count: 1,
          only_manual: true,
          threats: [{ name: 'Spoofing', framework: 'STRIDE', qualifier: '' }],
          elements: ['RestrictedArea behind the PhysicalEntryPoint'],
          mappings: [],
          occurrences: [
            { scenario: 101, scenario_id: 'sc_101', title: 'A cleaner finds a key', outcome: '', is_manual: true, evidence: 'enters behind', rationale: 'Follows staff in.' },
          ],
        },
      ],
      scenarios: [scenarios[0], scenarios[2]],
      set_aside: [scenarios[1]],
      incomplete: { unlabelled: 0, failed: 0, stale: 0 },
      details: { taxonomy: { id: 2, vocabulary_id: 'HCV-21', version: '1.2' }, generation: null, labelling: [], calls_cost: null },
    };
    open('report', report);
    const user = userEvent.setup();

    const findings = await screen.findByRole('list', { name: 'Vulnerabilities' });
    expect(within(findings).getByRole('button', { name: /Tailgating/ })).toHaveAttribute('aria-current', 'true');
    const shown = screen.getByRole('region', { name: 'Selected vulnerability' });
    expect(within(shown).getByText('Spoofing')).toBeInTheDocument();

    await user.click(within(shown).getByRole('button', { name: /See how it could happen/ }));

    expect(within(await list()).getByRole('button', { name: /A cleaner finds a key/ })).toHaveAttribute('aria-current', 'true');
    expect(within(detail()).getByText('Tailgating')).toBeInTheDocument();
    // The number it has on the other steps, though a set-aside scenario is not in the report.
    expect(within(detail()).getByText('Scenario 3')).toBeInTheDocument();
  });
});
