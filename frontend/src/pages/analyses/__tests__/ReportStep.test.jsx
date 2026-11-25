import { describe, expect, it } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { authState } from '../../../test/server';
import { buildAnalysis, buildScenario, mockAnalyses } from '../../../test/analysesApi';
import { renderApp } from '../../../test/renderApp';

const threat = (name, framework, qualifier = '') => ({ name, framework, qualifier });
const label = (hcv_id, name) => ({ hcv_id, name, evidence: 'the quote', rationale: 'the reason' });
const occurrence = (fields = {}) => ({
  scenario: 1,
  scenario_id: 'sc_01',
  title: 'An operator approves a reroute without checking',
  outcome: '40 buses are rerouted.',
  is_manual: false,
  evidence: 'the quote',
  rationale: 'the reason',
  ...fields,
});

function buildReport(fields = {}) {
  const generated = buildScenario({
    label_status: 'current',
    threat: 'GeneratorThreat',
    labels: [label('HCV-13', 'Inadequate Privacy Settings')],
  });
  const manual = buildScenario({
    id: 101,
    scenario_id: 'sc_101',
    title: 'A cleaner finds a key',
    is_manual: true,
    threat: '',
    label_status: 'current',
    labels: [label('HCV-19', 'Unreported Security Incidents'), label('HCV-13', 'Inadequate Privacy Settings')],
  });
  const manualOccurrence = occurrence({
    scenario: 101,
    scenario_id: 'sc_101',
    title: manual.title,
    outcome: '',
    is_manual: true,
  });
  return {
    name: 'Smart Parking',
    findings: [
      {
        hcv_id: 'HCV-13',
        name: 'Inadequate Privacy Settings',
        scenario_count: 2,
        manual_count: 1,
        only_manual: false,
        threats: [
          threat('Data disclosure', 'LINDDUN'),
          threat('Information disclosure', 'STRIDE', 'if through mobile app permissions'),
        ],
        elements: ['The Service itself', 'Service delivered by the MobileApp'],
        mappings: [
          {
            condition_class: 'UnscopedDevicePermissionCondition',
            element: 'Service delivered by the MobileApp',
            rule: 'E13c',
            stride: ['Information disclosure'],
            linddun: ['Data disclosure'],
            qualifier: 'if through mobile app permissions',
          },
        ],
        occurrences: [occurrence(), manualOccurrence],
      },
      {
        hcv_id: 'HCV-19',
        name: 'Unreported Security Incidents',
        scenario_count: 1,
        manual_count: 1,
        only_manual: true,
        threats: [],
        elements: [],
        mappings: [],
        occurrences: [manualOccurrence],
      },
    ],
    scenarios: [generated, manual],
    set_aside: [buildScenario({ id: 3, title: 'A contractor keeps a badge', set_aside: true })],
    incomplete: { unlabelled: 0, failed: 0, stale: 0 },
    details: {
      taxonomy: { id: 2, vocabulary_id: 'HCV-21', version: '1.2' },
      extraction: {
        model: 'anthropic/claude-sonnet-5.5',
        temperature: 0,
        prompt: 'System description extractor v1',
        is_custom: false,
        approved: 30,
        edited: 2,
        added: 1,
        rejected: 4,
      },
      generation: {
        model: 'anthropic/claude-sonnet-5',
        temperature: 0.8,
        prompt: 'Zero-shot Variant B v1',
        examples: [],
        count: 8,
        is_custom: false,
        preset_version: '1',
      },
      labelling: [
        {
          model: 'openai/gpt-5.6-luna-pro',
          temperature: 0,
          prompt: 'default-labelling v1',
          examples: ['a', 'b', 'c', 'd', 'e'],
          count: null,
          is_custom: true,
          preset_version: '1',
        },
      ],
      calls_cost: 0.1234,
    },
    ...fields,
  };
}

async function open(fields) {
  mockAnalyses({
    analyses: [buildAnalysis({ locked: true, scenarios: [buildScenario()] })],
    report: buildReport(fields),
  });
  authState.currentUserEmail = 'admin@example.com';
  renderApp({ initialEntries: ['/analyses/1/report'] });
  return screen.findByTestId('report-screen');
}

describe('Report step', () => {
  it('titles the report with the count', async () => {
    const report = await open();

    expect(within(report).getByRole('heading', { name: 'Smart Parking: 2 potential vulnerabilities' })).toBeInTheDocument();
  });

  it('shows threats by framework, the qualifier and the unmapped sentence', async () => {
    const report = await open();

    expect(within(report).getByText('Data disclosure').closest('[data-framework]')).toHaveAttribute('data-framework', 'LINDDUN');
    const stride = within(report).getByText('Information disclosure').closest('[data-framework]');
    expect(stride).toHaveAttribute('data-framework', 'STRIDE');
    expect(stride).toHaveTextContent('if through mobile app permissions');
    expect(within(report).getByText('No threat mapping defined for this vulnerability')).toBeInTheDocument();
  });

  it('shows manual tag, count and Only in manually added scenarios on both tabs', async () => {
    const report = await open();
    const user = userEvent.setup();

    expect(within(report).getByText(/found in 2 scenarios · 1 added manually/)).toBeInTheDocument();
    expect(within(report).getByText('Only in manually added scenarios')).toBeInTheDocument();
    expect(within(report).getAllByText('Added manually').length).toBeGreaterThan(0);

    await user.click(within(report).getByRole('tab', { name: 'Scenarios' }));

    expect(within(report).getAllByText('Added manually')).toHaveLength(1);
  });

  it('shows the outcome of each scenario under its title in Why', async () => {
    const report = await open();

    expect(within(report).getByText('Outcome: 40 buses are rerouted.')).toBeInTheDocument();
  });

  it('slides the Scenarios tab in from the right and Vulnerabilities from the left', async () => {
    const report = await open();
    const user = userEvent.setup();

    expect(within(report).getByRole('tabpanel').className).not.toMatch(/animate-/);

    await user.click(within(report).getByRole('tab', { name: 'Scenarios' }));
    expect(within(report).getByRole('tabpanel').className).toMatch(/animate-slide-from-right/);
    expect(within(report).getByRole('tabpanel').className).toMatch(/motion-reduce:animate-none/);

    await user.click(within(report).getByRole('tab', { name: 'Vulnerabilities' }));
    expect(within(report).getByRole('tabpanel').className).toMatch(/animate-slide-from-left/);
  });

  it('always shows the disclaimer', async () => {
    const report = await open({ findings: [] });

    expect(
      within(report).getByText(
        "These are potential vulnerabilities found in generated or analyst-written scenarios. Check them against the real system. A vulnerability that is not listed may still be present."
      )
    ).toBeInTheDocument();
  });

  it('counts the incomplete in a banner', async () => {
    const report = await open({ incomplete: { unlabelled: 2, failed: 1, stale: 3 } });

    expect(within(report).getByRole('status')).toHaveTextContent(
      '6 scenarios are not in this report: 2 not labelled, 1 failed, 3 out of date.'
    );
  });

  it('lists what was set aside', async () => {
    const report = await open();

    expect(within(within(report).getByRole('region', { name: 'Set aside' })).getByText('A contractor keeps a badge')).toBeInTheDocument();
  });

  it('shows the generator threat only in scenario details, with its title', async () => {
    const report = await open();
    const user = userEvent.setup();

    expect(within(report).queryByText('GeneratorThreat')).not.toBeInTheDocument();

    await user.click(within(report).getAllByRole('button', { name: /See how it could happen/ })[0]);

    expect(within(report).getByText('GeneratorThreat')).toBeInTheDocument();
    expect(within(report).getByText('Threat suggested by the generator')).toBeInTheDocument();
  });

  it('holds ids, rules, taxonomy, setups and the temperature hint in research details', async () => {
    const report = await open();

    const details = within(report).getByText('Research details').closest('details');
    for (const text of ['HCV-13', 'E13c', 'UnscopedDevicePermissionCondition', 'HCV-21 v1.2', 'anthropic/claude-sonnet-5',
      'Zero-shot Variant B v1', 'zero-shot', 'few-shot, 5 worked examples', 'Recommended', 'Custom',
      'Extraction: Recommended', 'System description extractor v1', '30 values approved (2 edited, 1 added), 4 rejected']) {
      expect(details).toHaveTextContent(text);
    }
    expect((await within(details).findAllByText('This model may ignore temperature')).length).toBe(2);
  });

  it('links Download PDF to the PDF at the top and again above the research details', async () => {
    const report = await open();

    const top = within(report).getByRole('link', { name: 'Download PDF' });
    const bottom = within(report).getByRole('link', { name: 'Download as PDF' });

    for (const link of [top, bottom]) {
      expect(link).toHaveAttribute('href', '/api/analyses/1/report/pdf/');
      expect(link).toHaveAttribute('download');
    }
    const details = within(report).getByText('Research details').closest('details');
    expect(bottom.compareDocumentPosition(details) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.queryByTestId('report-print')).not.toBeInTheDocument();
  });
});
