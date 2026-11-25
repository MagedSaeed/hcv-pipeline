import { http, HttpResponse } from 'msw';
import { paginate, requireAuth, requireCsrf, requireStaff } from './mockApi';
import { currentUser, server } from './server';

// An in-memory /api/analyses/, shaped like backend/analyses/serializers.py.

export const RECOMMENDED = {
  extraction: { model: 'anthropic/claude-sonnet-5.5', temperature: 0 },
  generation: { model: 'anthropic/claude-sonnet-5', temperature: 0.8, prompt: 'preset', examples: [], count: 8 },
  labelling: {
    model: 'openai/gpt-5.6-luna-pro',
    temperature: 0,
    prompt: 'preset',
    examples: ['preset:0', 'preset:1', 'preset:2', 'preset:3', 'preset:4'],
  },
};

const EXAMPLE_NAMES = ['NoiseApp sc_02', 'NoiseApp sc_03', 'NoiseApp sc_04', 'NoiseApp scenarios', 'oldsmar-sc_01'];

export function buildOptions() {
  const prompt = (fields) => ({ version: 1, recommended: false, takes_count: true, note: '', error: '', ...fields });
  return {
    generation: {
      prompts: [
        prompt({ ref: 'preset', name: 'Zero-shot Variant B', recommended: true }),
        prompt({ ref: 11, name: 'Diverse-generation-variant-b', note: 'writes control scenarios, not marked' }),
        prompt({ ref: 12, name: 'Countless', takes_count: false }),
        prompt({ ref: 13, name: 'Broken', error: 'resource("x") does not exist.' }),
      ],
      examples: [
        { ref: 21, name: 'oldsmar', recommended: false, error: '' },
        { ref: 22, name: 'foreign', recommended: false, error: "Cites HCV-99, which this taxonomy doesn't have." },
      ],
      defaults: RECOMMENDED.generation,
    },
    extraction: {
      fields: {
        person_or_role: { kind: 'element', section: '1. People and organizations', value_kind: 'choice', allowed: ['Citizen', 'SeniorCitizen', 'Child', 'CityOperator', 'DecisionMaker'] },
        organization: { kind: 'element', section: '1. People and organizations', value_kind: 'choice', allowed: ['GovernmentOrganization', 'PrivateSectorOrganization'] },
        interface: { kind: 'element', section: '2. System components', value_kind: 'choice', allowed: ['MobileApp', 'SelfServicePortal'] },
        data_store: { kind: 'element', section: '2. System components', value_kind: 'choice', allowed: ['DataStore'] },
        uses: { kind: 'fact', section: '3. Use and responsibility', value_kind: 'element', allowed: [] },
        retention_days: { kind: 'fact', section: '4. Data flows', value_kind: 'number', allowed: [] },
        removable_media: { kind: 'fact', section: '7. Settings', value_kind: 'boolean', allowed: [] },
        vulnerability_factor: { kind: 'fact', section: '1. People and organizations', value_kind: 'choice', allowed: ['Factor_Age_Senior', 'Factor_Disability'] },
      },
      controls: ['MultiFactorAuthentication', 'PasswordStrengthEnforcement', 'PasswordControl'],
      defaults: RECOMMENDED.extraction,
      prompt: 'System description extractor v1',
    },
    labelling: {
      prompts: [prompt({ ref: 'preset', name: 'default-labelling', recommended: true, takes_count: false })],
      examples: EXAMPLE_NAMES.map((name, index) => ({ ref: `preset:${index}`, name, recommended: true, error: '' })),
      defaults: RECOMMENDED.labelling,
    },
  };
}

export function buildScenario(fields = {}) {
  return {
    id: 1,
    scenario_id: 'sc_01',
    actor: 'PO1',
    trigger: 'An operator approves a reroute without checking',
    standing_state: '',
    asset: 'Routing Service',
    threat: 'Spoofing',
    outcome: '',
    narrative: 'PO1 approves the reroute.',
    title: 'An operator approves a reroute without checking',
    is_manual: false,
    set_aside: false,
    label_status: 'unlabelled',
    error: '',
    labels: [],
    ...fields,
  };
}

export function buildItem(fields = {}) {
  return {
    id: 1,
    kind: 'element',
    section: '1. People and organizations',
    field: 'person_or_role',
    ref: 'Residents',
    value: 'Citizen',
    original_value: 'Citizen',
    state: '',
    original_state: '',
    fact_type: 'Asserted',
    reason: '',
    other_reading: '',
    problem: '',
    decision: 'pending',
    edited: false,
    added: false,
    refers_to_rejected: false,
    ...fields,
  };
}

export function buildExtraction(fields = {}) {
  const items = fields.items ?? [];
  return {
    status: 'current',
    settings: structuredClone(RECOMMENDED.extraction),
    items,
    gaps: { not_assessable: [], no_term: [] },
    pending: items.filter((item) => item.decision === 'pending').length,
    run: null,
    ...fields,
  };
}

// An extraction that is current and fully approved, so Generate is open.
export const CHECKED = () =>
  buildExtraction({ items: [buildItem({ decision: 'approved' })] });

export function buildAnalysis(fields = {}) {
  return {
    id: 1,
    name: 'Smart Parking',
    owner_email: 'admin@example.com',
    description: 'An app lets residents pay for parking.',
    locked: false,
    taxonomy: { id: 2, vocabulary_id: 'HCV-21', version: '1.2' },
    generation_settings: structuredClone(RECOMMENDED.generation),
    labelling_settings: structuredClone(RECOMMENDED.labelling),
    steps: { system: false, generate: false, label: false, report: false },
    resume_step: 'system',
    updated_at: '2026-11-22T10:00:00Z',
    extraction_settings: structuredClone(RECOMMENDED.extraction),
    extraction: CHECKED(),
    latest_runs: { extract: null, generate: null, label: null },
    scenarios: [],
    ...fields,
  };
}

export function buildRun(fields = {}) {
  return { id: 7, kind: 'generate', status: 'running', total: 1, done: 0, failed: 0, notice: '', error: '', created_at: new Date().toISOString(), ...fields };
}

// Installs the handlers and returns the store, whose `requests` log every write.
export function mockAnalyses({ analyses = [buildAnalysis()], options = buildOptions(), report = null, models } = {}) {
  const store = { analyses, options, report, requests: [] };
  const find = (id) => store.analyses.find((row) => String(row.id) === String(id));

  function write(method, path, handle) {
    return http[method](path, async ({ request, params }) => {
      const user = currentUser();
      const refused = requireCsrf(request) ?? requireAuth(user) ?? (user?.role === 'analyst' ? null : requireStaff(user));
      if (refused) return refused;
      const text = await request.text();
      const body = text ? JSON.parse(text) : {};
      store.requests.push({ method, path: new URL(request.url).pathname, body });
      return handle({ body, params, analysis: params.id ? find(params.id) : null });
    });
  }
  const touch = (analysis) => {
    analysis.updated_at = new Date(Date.parse(analysis.updated_at) + 1000).toISOString();
  };

  server.use(
    http.get('/api/models/', () => HttpResponse.json({ count: (models ?? MODELS).length, results: models ?? MODELS })),
    http.get('/api/analyses/options/', () => HttpResponse.json(store.options)),
    http.get('/api/analyses/', ({ request }) => {
      const all = new URL(request.url).searchParams.get('scope') === 'all';
      const mine = store.analyses.filter((row) => row.owner_email === currentUser()?.email);
      return HttpResponse.json(paginate(all ? store.analyses : mine, request));
    }),
    http.get('/api/analyses/:id/report/', () => HttpResponse.json(store.report)),
    http.get('/api/analyses/:id/', ({ params }) => {
      const analysis = find(params.id);
      return analysis ? HttpResponse.json(analysis) : HttpResponse.json({ detail: 'Not found.' }, { status: 404 });
    }),
    write('post', '/api/analyses/', ({ body }) => {
      const id = store.analyses.length + 1;
      const created = buildAnalysis({ id, name: '', description: '', owner_email: currentUser().email, ...body });
      store.analyses.push(created);
      return HttpResponse.json(created, { status: 201 });
    }),
    write('patch', '/api/analyses/:id/', ({ body, analysis }) => {
      Object.assign(analysis, body);
      touch(analysis);
      return HttpResponse.json(analysis);
    }),
    write('delete', '/api/analyses/:id/', ({ analysis }) => {
      store.analyses.splice(store.analyses.indexOf(analysis), 1);
      return new HttpResponse(null, { status: 204 });
    }),
    write('post', '/api/analyses/:id/start-new/', ({ analysis }) => {
      const copy = buildAnalysis({
        id: store.analyses.length + 1,
        name: `${analysis.name} (2)`,
        description: analysis.description,
        owner_email: currentUser().email,
      });
      store.analyses.push(copy);
      return HttpResponse.json(copy, { status: 201 });
    }),
    write('post', '/api/analyses/:id/extract/', ({ analysis }) => {
      analysis.latest_runs.extract = buildRun({ kind: 'extract', status: 'queued' });
      return HttpResponse.json(analysis.latest_runs.extract, { status: 202 });
    }),
    write('post', '/api/analyses/:id/items/approve-asserted/', ({ analysis }) => {
      for (const item of analysis.extraction.items) {
        if (item.decision === 'pending' && item.fact_type === 'Asserted' && !item.problem) item.decision = 'approved';
      }
      analysis.extraction.pending = analysis.extraction.items.filter((item) => item.decision === 'pending').length;
      touch(analysis);
      return HttpResponse.json(analysis);
    }),
    write('post', '/api/analyses/:id/items/approve-section/', ({ body, analysis }) => {
      for (const item of analysis.extraction.items) {
        if (item.decision === 'pending' && item.section === body.section && !item.problem) item.decision = 'approved';
      }
      analysis.extraction.pending = analysis.extraction.items.filter((item) => item.decision === 'pending').length;
      touch(analysis);
      return HttpResponse.json(analysis);
    }),
    write('post', '/api/analyses/:id/items/', ({ body, analysis }) => {
      const field = store.options.extraction.fields[body.field];
      const item = buildItem({
        id: Math.max(0, ...analysis.extraction.items.map((row) => row.id)) + 1,
        kind: body.field === 'control' ? 'control' : field.kind,
        section: body.field === 'control' ? '8. Controls' : field.section,
        field: body.field,
        ref: body.ref,
        value: body.value,
        original_value: body.value,
        state: body.state ?? '',
        original_state: body.state ?? '',
        decision: 'approved',
        added: true,
      });
      analysis.extraction.items.push(item);
      touch(analysis);
      return HttpResponse.json(item, { status: 201 });
    }),
    write('delete', '/api/analyses/:id/items/:iid/', ({ params, analysis }) => {
      analysis.extraction.items = analysis.extraction.items.filter((row) => String(row.id) !== params.iid);
      touch(analysis);
      return new HttpResponse(null, { status: 204 });
    }),
    write('patch', '/api/analyses/:id/items/:iid/', ({ body, params, analysis }) => {
      const item = analysis.extraction.items.find((row) => String(row.id) === params.iid);
      Object.assign(item, body);
      if ('value' in body) item.edited = body.value !== item.original_value;
      analysis.extraction.pending = analysis.extraction.items.filter((row) => row.decision === 'pending').length;
      touch(analysis);
      return HttpResponse.json(item);
    }),
    write('post', '/api/analyses/:id/generate/', ({ analysis }) => {
      analysis.locked = true;
      analysis.latest_runs.generate = buildRun({ status: 'queued' });
      return HttpResponse.json(analysis.latest_runs.generate, { status: 202 });
    }),
    write('post', '/api/analyses/:id/label/', ({ analysis }) => {
      analysis.latest_runs.label = buildRun({ kind: 'label', status: 'queued', total: analysis.scenarios.length });
      return HttpResponse.json(analysis.latest_runs.label, { status: 202 });
    }),
    write('post', '/api/analyses/:id/cancel/', ({ analysis }) => {
      const run = Object.values(analysis.latest_runs).find((row) => ['queued', 'running'].includes(row?.status));
      run.status = 'cancelled';
      return HttpResponse.json(run);
    }),
    write('post', '/api/analyses/:id/scenarios/', ({ body, analysis }) => {
      const number = 101 + analysis.scenarios.filter((row) => row.is_manual).length;
      const created = buildScenario({
        id: number,
        scenario_id: `sc_${number}`,
        is_manual: true,
        trigger: '',
        actor: '',
        asset: '',
        threat: '',
        title: body.narrative,
        ...body,
      });
      analysis.scenarios.push(created);
      touch(analysis);
      return HttpResponse.json(created, { status: 201 });
    }),
    write('patch', '/api/analyses/:id/scenarios/:sid/', ({ body, params, analysis }) => {
      const scenario = analysis.scenarios.find((row) => String(row.id) === params.sid);
      Object.assign(scenario, body);
      touch(analysis);
      return HttpResponse.json(scenario);
    }),
    write('delete', '/api/analyses/:id/scenarios/:sid/', ({ params, analysis }) => {
      analysis.scenarios = analysis.scenarios.filter((row) => String(row.id) !== params.sid);
      touch(analysis);
      return new HttpResponse(null, { status: 204 });
    })
  );
  return store;
}

export const MODELS = [
  { id: 'anthropic/claude-sonnet-5', name: 'Anthropic: Claude Sonnet 5', supported_parameters: ['max_tokens'] },
  { id: 'openai/gpt-5.6-luna-pro', name: 'OpenAI: GPT-5.6 Luna Pro', supported_parameters: ['seed'] },
  { id: 'qwen/qwen-plus', name: 'Qwen: Qwen Plus', supported_parameters: ['temperature'] },
];
