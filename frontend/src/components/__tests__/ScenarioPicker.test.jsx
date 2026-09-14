import { useState } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { server } from '../../test/server';
import { paginated } from '../../test/mockApi';
import { setQueryClient } from '../../api/client';
import ScenarioPicker from '../ScenarioPicker';

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

let scenarios;

beforeEach(() => {
  scenarios = [
    scenario(1, 1, null, { threat: 'Remote login abuse', outcome: 'Chemical overdose', narrative: 'Seed narrative.' }),
    scenario(2, 1, null, { origin: 'manual', threat: 'Insider edits setpoints' }),
    scenario(3, 2, 10, { threat: 'Spoofed sensor readings' }),
    scenario(4, 2, 10, { threat: 'Denial of service' }),
    scenario(5, 3, 20, { actor: 'Contractor', threat: 'Stolen badge', narrative: 'Experiment narrative.' }),
  ];
  const run = (id, fields) => ({ id, phase: 2, pipeline_run: null, status: 'completed', ...fields });
  server.use(
    http.get('/api/scenarios/', () => HttpResponse.json(paginated(scenarios))),
    http.get('/api/system-descriptions/', () =>
      HttpResponse.json(
        paginated([
          { id: 1, title: 'Oldsmar' },
          { id: 2, title: 'Riverbend' },
          { id: 3, title: 'Lakeside' },
        ])
      )
    ),
    http.get('/api/phase-runs/', () =>
      HttpResponse.json(
        paginated([
          run(10, { variant: 'A', model: 'openai/gpt-4o-mini', prompt_template: 100, finished_at: '2026-09-10T00:00:00Z' }),
          run(20, { variant: 'B', model: 'anthropic/claude-sonnet-4', prompt_template: 101, pipeline_run: 7, finished_at: '2026-09-12T00:00:00Z' }),
        ])
      )
    ),
    http.get('/api/prompts/', () =>
      HttpResponse.json(paginated([{ id: 100, name: 'gen-a', version: 2 }, { id: 101, name: 'gen-b', version: 1 }]))
    ),
    http.get('/api/pipeline-runs/', () =>
      HttpResponse.json(paginated([{ id: 7, experiment: 3, repetition_index: 2, status: 'completed' }]))
    ),
    http.get('/api/experiments/', () => HttpResponse.json(paginated([{ id: 3, name: 'Baseline' }])))
  );
});

function renderPicker({ mode = 'single', initial, onChange = () => {} } = {}) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  setQueryClient(queryClient);
  function Controlled() {
    const [value, setValue] = useState(initial);
    return (
      <ScenarioPicker
        mode={mode}
        value={value}
        onChange={(next) => {
          onChange(next);
          setValue(next);
        }}
      />
    );
  }
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <Controlled />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

const GROUP_NAMES = /seed & hand-written|prompt test run|baseline/i;

describe('ScenarioPicker', () => {
  it('groups seed scenarios first, then runs newest first, with details and provenance', async () => {
    const user = userEvent.setup();
    renderPicker();

    await screen.findByRole('button', { name: /seed & hand-written/i });
    const groups = screen.getAllByRole('button', { name: GROUP_NAMES });
    expect(groups.map((button) => button.textContent)).toEqual([
      expect.stringMatching(/^Seed & hand-written scenarios2 scenarios/),
      expect.stringMatching(/^Baseline · repetition 2 · Variant B1 scenario · gen-b v1 · anthropic\/claude-sonnet-4/),
      expect.stringMatching(/^Prompt test run · Variant A2 scenarios · gen-a v2 · openai\/gpt-4o-mini/),
    ]);

    // Seed and the newest run are open; the older run starts collapsed.
    expect(screen.getByRole('radio', { name: /sc_01 · Oldsmar/ })).toHaveAccessibleName(/remote login abuse — chemical overdose/i);
    expect(screen.getByRole('radio', { name: /sc_05 · Lakeside/ })).toBeInTheDocument();
    expect(screen.queryByRole('radio', { name: /sc_03/ })).not.toBeInTheDocument();
    await user.click(groups[2]);
    expect(screen.getByRole('radio', { name: /sc_03 · Riverbend/ })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Details for sc_05' }));
    expect(screen.getByText('Experiment narrative.')).toBeInTheDocument();
    expect(screen.getByText('Contractor')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /how was this generated/i })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Details for sc_01' }));
    expect(screen.getByText('Seed narrative.')).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: /how was this generated/i })).toHaveLength(1);
  });

  it('search, source and variant filters narrow the groups', async () => {
    const user = userEvent.setup();
    renderPicker();
    await screen.findByRole('button', { name: /seed & hand-written/i });

    await user.type(screen.getByRole('searchbox', { name: /search scenarios/i }), 'gpt-4o');
    expect(screen.getAllByRole('button', { name: GROUP_NAMES })).toHaveLength(1);
    // A search opens matching groups.
    expect(screen.getByRole('radio', { name: /sc_04 · Riverbend/ })).toBeInTheDocument();

    await user.clear(screen.getByRole('searchbox', { name: /search scenarios/i }));
    await user.type(screen.getByRole('searchbox', { name: /search scenarios/i }), 'setpoints');
    expect(screen.getByRole('button', { name: /seed & hand-written/i })).toHaveTextContent(/1 of 2 scenarios/);
    await user.clear(screen.getByRole('searchbox', { name: /search scenarios/i }));

    await user.selectOptions(screen.getByRole('combobox', { name: 'Source' }), 'Experiments');
    expect(screen.getAllByRole('button', { name: GROUP_NAMES }).map((b) => b.textContent)).toEqual([
      expect.stringMatching(/^Baseline/),
    ]);

    await user.selectOptions(screen.getByRole('combobox', { name: 'Source' }), 'All sources');
    await user.selectOptions(screen.getByRole('combobox', { name: 'Variant' }), 'Variant A');
    expect(screen.getAllByRole('button', { name: GROUP_NAMES }).map((b) => b.textContent)).toEqual([
      expect.stringMatching(/^Prompt test run/),
    ]);
  });

  it('multiple mode: per-group select all is tri-state, with select all shown, clear and a count', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderPicker({ mode: 'multiple', initial: [], onChange });
    await screen.findByRole('button', { name: /seed & hand-written/i });
    expect(screen.getByText('0 selected')).toBeInTheDocument();

    const seedAll = screen.getByRole('checkbox', { name: /select all in seed & hand-written/i });
    await user.click(seedAll);
    expect(seedAll).toBeChecked();
    expect(onChange).toHaveBeenLastCalledWith([1, 2]);
    await user.click(screen.getByRole('checkbox', { name: /sc_02 · Oldsmar/ }));
    expect(seedAll).toBePartiallyChecked();
    expect(screen.getByText('1 selected')).toBeInTheDocument();

    await user.selectOptions(screen.getByRole('combobox', { name: 'Source' }), 'Prompt test runs');
    await user.click(screen.getByRole('button', { name: /select all shown/i }));
    expect(onChange).toHaveBeenLastCalledWith([1, 3, 4]);
    expect(screen.getByText('3 selected')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /^clear$/i }));
    expect(onChange).toHaveBeenLastCalledWith([]);
    expect(screen.getByText('0 selected')).toBeInTheDocument();
  });

  it('single mode reports the chosen id and names the selection', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderPicker({ onChange });

    await user.click(await screen.findByRole('radio', { name: /sc_05 · Lakeside/ }));
    expect(onChange).toHaveBeenLastCalledWith(5);
    expect(screen.getByText(/selected: sc_05 · lakeside \(baseline · repetition 2 · variant b\)/i)).toBeInTheDocument();
  });

  it('explains what to do when there are no scenarios at all', async () => {
    scenarios = [];
    renderPicker({ mode: 'multiple', initial: [] });
    const notice = await screen.findByRole('note');
    expect(notice).toHaveTextContent(/no scenarios to label yet/i);
    expect(within(notice).getByRole('link', { name: /corpus/i })).toHaveAttribute('href', '/resources?tab=corpus');
  });
});
