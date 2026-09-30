import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { server } from '../../test/server';
import { paginated } from '../../test/mockApi';
import { setQueryClient } from '../../api/client';
import LabelScenariosModal from '../LabelScenariosModal';

function scenario(id, systemId, generationRun, fields = {}) {
  return {
    id,
    system_description: systemId,
    scenario_id: `sc_0${id}`,
    actor: '',
    trigger: '',
    standing_state: '',
    asset: '',
    threat: '',
    outcome: '',
    narrative: '',
    origin: generationRun ? 'generated' : 'seed',
    generation_run: generationRun,
    ...fields,
  };
}

let bulkBody;

beforeEach(() => {
  bulkBody = null;
  server.use(
    http.get('/api/scenarios/', () =>
      HttpResponse.json(
        paginated([
          scenario(1, 1, null, { threat: 'Remote login abuse' }),
          scenario(2, 1, null, { origin: 'manual', threat: 'Insider edits setpoints' }),
          scenario(3, 2, 20, { threat: 'Spoofed sensor readings' }),
          scenario(4, 3, 10, { threat: 'Scratch only' }),
        ])
      )
    ),
    http.get('/api/system-descriptions/', () =>
      HttpResponse.json(
        paginated([
          { id: 1, title: 'Oldsmar', origin: 'seed', is_scratch: false },
          { id: 2, title: 'Riverbend', origin: 'generated', is_scratch: false },
          { id: 3, title: 'Scratch plant', origin: 'generated', is_scratch: true },
        ])
      )
    ),
    http.get('/api/phase-runs/', () =>
      HttpResponse.json(
        paginated([
          { id: 10, phase: 2, variant: 'A', pipeline_run: null, prompt_template: 100, model: 'm', status: 'completed' },
          {
            id: 20,
            phase: 2,
            variant: 'B',
            pipeline_run: 7,
            prompt_template: 100,
            model: 'm',
            status: 'completed',
            finished_at: '2026-09-12T00:00:00Z',
          },
        ])
      )
    ),
    http.get('/api/prompts/', () => HttpResponse.json(paginated([{ id: 100, name: 'gen', version: 1 }]))),
    http.get('/api/pipeline-runs/', () =>
      HttpResponse.json(paginated([{ id: 7, experiment: 3, repetition_index: 2 }]))
    ),
    http.get('/api/experiments/', () =>
      HttpResponse.json(
        paginated([
          { id: 3, name: 'Baseline', phases: [2, 3], label_scenarios: [] },
          { id: 5, name: 'Labeller GPT', phases: [3], label_scenarios: [2, 3] },
        ])
      )
    ),
    http.post('/api/scenarios/bulk/', async ({ request }) => {
      bulkBody = await request.json();
      return HttpResponse.json({ scenario_ids: [50, 51] }, { status: 201 });
    })
  );
});

function renderModal({ initialIds = [], onApply = vi.fn(), onClose = vi.fn() } = {}) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  setQueryClient(queryClient);
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <LabelScenariosModal initialIds={initialIds} excludeExperimentId={null} onApply={onApply} onClose={onClose} />
      </MemoryRouter>
    </QueryClientProvider>
  );
  return { onApply, onClose };
}

describe('LabelScenariosModal', () => {
  it('lists non-scratch systems and shows the chosen system scenarios grouped by source', async () => {
    const user = userEvent.setup();
    renderModal();
    const systems = await screen.findByRole('list', { name: /systems/i });
    expect(within(systems).getByRole('button', { name: /oldsmar/i })).toBeInTheDocument();
    expect(within(systems).getByRole('button', { name: /riverbend.*generated/i })).toBeInTheDocument();
    expect(within(systems).queryByRole('button', { name: /scratch plant/i })).not.toBeInTheDocument();

    await user.click(within(systems).getByRole('button', { name: /oldsmar/i }));
    expect(screen.getByRole('checkbox', { name: /select all in seed & hand-written/i })).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: /sc_01/ })).toBeInTheDocument();
  });

  it('opens on the first system, and follows the source filter when that system drops out', async () => {
    const user = userEvent.setup();
    renderModal();
    expect(await screen.findByRole('heading', { name: 'Oldsmar' })).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: /sc_01/ })).toBeInTheDocument();

    const source = screen.getByRole('combobox', { name: /source/i });
    await user.selectOptions(source, within(source).getByRole('option', { name: /baseline · repetition 2/i }));
    expect(screen.getByRole('heading', { name: 'Riverbend' })).toBeInTheDocument();
  });

  it('narrows both panes to one run with the source filter, and never offers prompt test runs', async () => {
    const user = userEvent.setup();
    renderModal();
    const source = await screen.findByRole('combobox', { name: /source/i });
    expect(within(source).queryByRole('option', { name: /prompt test run/i })).not.toBeInTheDocument();
    await user.selectOptions(source, within(source).getByRole('option', { name: /baseline · repetition 2/i }));
    const systems = screen.getByRole('list', { name: /systems/i });
    expect(within(systems).getAllByRole('button').map((b) => b.textContent)).toEqual([expect.stringMatching(/Riverbend/)]);
  });

  it('selects across systems, counts them in the footer, and applies the ids', async () => {
    const user = userEvent.setup();
    const { onApply } = renderModal();
    const systems = await screen.findByRole('list', { name: /systems/i });
    await user.click(within(systems).getByRole('button', { name: /oldsmar/i }));
    await user.click(screen.getByRole('checkbox', { name: /select all in seed & hand-written/i }));
    await user.click(within(systems).getByRole('button', { name: /riverbend/i }));
    await user.click(screen.getByRole('checkbox', { name: /sc_03/ }));
    expect(screen.getByText('3 scenarios from 2 systems selected')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /^apply$/i }));
    expect(onApply).toHaveBeenCalledWith([1, 2, 3]);
    expect(bulkBody).toBeNull();
  });

  it('loads another phase-3-only experiment selection', async () => {
    const user = userEvent.setup();
    renderModal({ initialIds: [1] });
    const copy = await screen.findByRole('combobox', { name: /load scenarios from a phase-3-only experiment/i });
    expect(within(copy).queryByRole('option', { name: /baseline/i })).not.toBeInTheDocument();
    await user.selectOptions(copy, within(copy).getByRole('option', { name: /labeller gpt/i }));
    expect(screen.getByText('2 scenarios from 2 systems selected')).toBeInTheDocument();
    // Visible feedback: the choice stays shown, a note confirms it, and the pane shows copied ticks.
    expect(copy).toHaveDisplayValue('Labeller GPT');
    expect(screen.getByText('Loaded 2 scenarios from “Labeller GPT”.')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Oldsmar' })).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: /sc_02/ })).toBeChecked();
  });

  it('drafts a new system and a scenario for an existing one, saves them in one request, and selects them', async () => {
    const user = userEvent.setup();
    const { onApply } = renderModal({ initialIds: [1] });
    const systems = await screen.findByRole('list', { name: /systems/i });

    await user.click(within(systems).getByRole('button', { name: /oldsmar/i }));
    await user.click(screen.getByRole('button', { name: /add scenario/i }));
    expect(screen.getByLabelText(/^scenario id$/i)).toHaveValue('sc_03');
    await user.type(screen.getByLabelText(/^threat$/i), 'Phished operator');

    await user.click(screen.getByRole('button', { name: /new system/i }));
    await user.type(screen.getByLabelText(/^system title$/i), 'Lakeside');
    await user.click(screen.getByRole('button', { name: /add scenario/i }));
    expect(screen.getByText('New systems and scenarios are saved to the corpus when you apply.')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /^apply$/i }));
    expect(bulkBody).toEqual({
      systems: [
        expect.objectContaining({ title: 'Lakeside', scenarios: [expect.objectContaining({ scenario_id: 'sc_01' })] }),
      ],
      scenarios: [expect.objectContaining({ system_description: 1, scenario_id: 'sc_03', threat: 'Phished operator' })],
    });
    expect(onApply).toHaveBeenCalledWith([1, 50, 51]);
  });

  it('shows a draft save error next to its draft and keeps the modal open', async () => {
    server.use(
      http.post('/api/scenarios/bulk/', () =>
        HttpResponse.json(
          { scenarios: { 0: { scenario_id: ['sc_03 is used twice in this batch.'] } } },
          { status: 400 }
        )
      )
    );
    const user = userEvent.setup();
    const { onApply } = renderModal();
    const systems = await screen.findByRole('list', { name: /systems/i });
    await user.click(within(systems).getByRole('button', { name: /oldsmar/i }));
    await user.click(screen.getByRole('button', { name: /add scenario/i }));
    await user.click(screen.getByRole('button', { name: /^apply$/i }));
    expect(await screen.findByText('sc_03 is used twice in this batch.')).toBeInTheDocument();
    expect(onApply).not.toHaveBeenCalled();
  });

  it('offers no way to edit existing rows or add to a generated system', async () => {
    const user = userEvent.setup();
    renderModal();
    const systems = await screen.findByRole('list', { name: /systems/i });
    await user.click(within(systems).getByRole('button', { name: /riverbend/i }));
    expect(screen.queryByRole('button', { name: /add scenario/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('textbox', { name: /threat/i })).not.toBeInTheDocument();
  });
});
