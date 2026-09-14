import { describe, expect, it } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { authState, server } from '../../test/server';
import { paginated } from '../../test/mockApi';
import { setQueryClient } from '../../api/client';
import GenerationProvenance from '../GenerationProvenance';

function renderProvenance(phaseRunId) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  setQueryClient(queryClient);
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <GenerationProvenance phaseRunId={phaseRunId} />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe('GenerationProvenance', () => {
  it('shows the seed systems, few-shots and target a prompt test chose, naming deleted ones', async () => {
    const user = userEvent.setup();
    authState.currentUserEmail = 'admin@example.com';
    server.use(
      http.get('/api/phase-runs/88/', () =>
        HttpResponse.json({ id: 88, pipeline_run: null, phase: 2, variant: 'A', model: 'openai/gpt-4o-mini', temperature: 0, prompt_template: 12, taxonomy: 2, experiment: null, n_systems: 1, n_scenarios_per_system: 3, seed_systems: [1, 404], fewshots: [], target_system: 2, generate_systems: false, generate_scenarios: true, status: 'completed', error: '', progress: { done: 1, total: 1, failed: 0, percent: 100 }, finished_at: null })
      ),
      http.get('/api/prompts/12/', () => HttpResponse.json({ id: 12, name: 'default-generation-variant-a', version: 1 })),
      http.get('/api/phase-runs/88/calls/', () => HttpResponse.json(paginated([]))),
      http.get('/api/system-descriptions/', () =>
        HttpResponse.json(paginated([{ id: 1, title: 'Oldsmar water plant' }, { id: 2, title: 'Tram depot' }]))
      ),
      http.get('/api/fewshots/', () => HttpResponse.json(paginated([{ id: 21, name: 'oldsmar' }])))
    );
    renderProvenance(88);

    await user.click(screen.getByRole('button', { name: /how was this generated/i }));
    const dialog = screen.getByRole('dialog');
    expect(await within(dialog).findByText('Oldsmar water plant, a deleted system')).toBeInTheDocument();
    expect(within(dialog).getByText('(none)')).toBeInTheDocument();
    expect(within(dialog).getByText('Tram depot')).toBeInTheDocument();
    expect(within(dialog).getByText('1 × 3')).toBeInTheDocument();
  });
});
