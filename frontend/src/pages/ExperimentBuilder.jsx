import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '../contexts/auth-context';
import {
  useCreateExperiment,
  useFewShots,
  useModels,
  usePromptTemplates,
  useRunExperiment,
  useSystemDescriptions,
  useTaxonomies,
} from '../api/queries';
import { extractFieldErrors, hasNonFieldError } from '../api/errors';
import ErrorBanner from '../components/ErrorBanner';

// Verbatim from `RunRequestSerializer.n_repetitions.help_text`
// (backend/pipeline/serializers.py) — the field `POST
// /api/experiments/{id}/run/` reads (pipeline/views.py's `ExperimentViewSet.run`).
//
// There is no HTTP-reachable way to fetch this live. `OPTIONS
// /api/experiments/{id}/run/` looks like the obvious route — DRF's
// SimpleMetadata.determine_actions calls `view.get_serializer()` for the
// route's POST method — but `ExperimentViewSet` never overrides
// `get_serializer_class()` per-action (only `PhaseRunViewSet` does, for
// `create`), so that call always returns `ExperimentSerializer`, never
// `RunRequestSerializer`: `run`'s own serializer is only ever instantiated
// inline inside the action method's body. Verified empirically against a
// real Django test client (in-process, no live server, no network) rather
// than only reasoned about — see the task report. So this is a hand-copy,
// not a live fetch, kept BYTE-FOR-BYTE identical to the backend string
// (the same source `pipeline/tests/test_experiment_api.py::
// test_n_repetitions_help_text_...` asserts substrings against) so the two
// cannot silently drift apart in MEANING, even though nothing enforces
// that mechanically the way a truly live-fetched value would.
const N_REPETITIONS_HELP_TEXT =
  "How many times to repeat this exact configuration. LLM output is stochastic, so a single repetition's difference between variant A (taxonomy withheld) and variant B (taxonomy supplied) could just be sampling noise — you can't tell a real effect from luck off one run. Repeating the same configuration several times is what turns a one-off difference into a finding you can trust. Each repetition costs roughly $1.61 and takes about 23 minutes at the default configuration (3 systems x 5 scenarios, both variants, 32 provider calls), so the total cost is straightforwardly this number times $1.61. Wall-clock time is a bit different: all repetitions are queued together and run as workers become free (2 at a time by default), so they overlap rather than running strictly in sequence — 1 repetition still takes about 23 minutes, 2 finish together in about the same 23 minutes, and each further pair adds roughly another 23 minutes while it waits for a free worker. The default of 1 is deliberately conservative; raise it once you're ready to turn a promising one-off run into a real result.";

// corpus.models.Origin.choices, spelled out here the same way Corpus.jsx's
// ORIGIN_LABELS does — a generated row is a previous run's own output;
// Experiment.clean() refuses it as a seed or a target (pipeline/models.py's
// generated_seed_systems_error/generated_target_system_error), and the note
// below each list says so up front rather than only after a 400.
const ORIGIN_LABELS = { seed: 'seed', generated: 'generated', manual: 'manual' };

function FieldError({ message }) {
  if (!message) return null;
  return <p className="mt-1 text-xs text-red-700">{message}</p>;
}

const emptyForm = {
  name: '',
  description: '',
  taxonomy: '',
  phase2Model: '',
  phase2Temperature: '1',
  phase2PromptA: '',
  phase2PromptB: '',
  phase3Model: '',
  phase3Temperature: '0',
  phase3Prompt: '',
  seedSystemIds: [],
  fewshotIds: [],
  generateSystems: true,
  generateScenarios: true,
  targetSystem: '',
  nSystems: '3',
  nScenariosPerSystem: '5',
  variantA: true,
  variantB: true,
};

function ModelField({ id, label, value, onChange, modelsQuery, models, fieldError }) {
  return (
    <div>
      <label htmlFor={id} className="block text-xs font-medium text-gray-700">
        {label}
      </label>
      {modelsQuery.isError ? (
        // Only swap to a plain text field on a genuine failure (no key
        // configured, catalogue unreachable) - NOT while merely loading, so
        // this field's element identity never flips between an <input> and
        // a <select> mid-fetch, which would silently strand a reference a
        // caller grabbed before the swap.
        <input
          id={id}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          placeholder="openai/gpt-4o-mini"
          className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-1.5 text-sm shadow-sm"
        />
      ) : (
        <select
          id={id}
          value={value}
          disabled={!modelsQuery.isSuccess}
          onChange={(event) => onChange(event.target.value)}
          className="mt-1 block w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm shadow-sm"
        >
          <option value="">Select a model…</option>
          {models.map((model) => (
            <option key={model.id} value={model.id}>
              {model.name ? `${model.name} (${model.id})` : model.id}
            </option>
          ))}
        </select>
      )}
      {modelsQuery.isError && (
        <p className="mt-1 text-xs text-amber-700">Model catalogue unavailable — enter a model id by hand.</p>
      )}
      <FieldError message={fieldError} />
    </div>
  );
}

export default function ExperimentBuilder() {
  const { isStaff } = useAuth();

  const [form, setForm] = useState(emptyForm);
  const [savedExperiment, setSavedExperiment] = useState(null);
  const [nRepetitions, setNRepetitions] = useState('1');
  const [launchResult, setLaunchResult] = useState(null);

  const modelsQuery = useModels({ enabled: isStaff });
  const taxonomiesQuery = useTaxonomies({ enabled: isStaff });
  const phase2TemplatesQuery = usePromptTemplates({ phase: 2 }, { enabled: isStaff });
  const phase3TemplatesQuery = usePromptTemplates({ phase: 3 }, { enabled: isStaff });
  const systemsQuery = useSystemDescriptions({}, { enabled: isStaff });
  const fewshotsQuery = useFewShots({}, { enabled: isStaff });

  const createExperiment = useCreateExperiment();
  const runExperiment = useRunExperiment();

  if (!isStaff) {
    return (
      <div className="space-y-2">
        <h1 className="text-2xl font-semibold text-gray-900">New experiment</h1>
        <p role="status" className="text-sm text-gray-500">
          Only staff can create or launch an experiment.
        </p>
      </div>
    );
  }

  const models = modelsQuery.data?.results ?? [];
  const taxonomies = taxonomiesQuery.data?.results ?? [];
  const phase2Templates = phase2TemplatesQuery.data?.results ?? [];
  const phase3Templates = phase3TemplatesQuery.data?.results ?? [];
  const systems = systemsQuery.data?.results ?? [];
  const fewshots = fewshotsQuery.data?.results ?? [];

  // Task 6's own acceptance bar: map every field-shaped DRF error onto the
  // field it names; fall back to ErrorBanner only for what's left
  // (`detail`/`non_field_errors`) — never both for the same body.
  const fieldErrors = extractFieldErrors(createExperiment.error);
  const showCreateBanner = createExperiment.isError && hasNonFieldError(createExperiment.error);
  const launchErrorCode = runExperiment.error?.response?.data?.code;

  function updateField(field, value) {
    setForm((current) => ({ ...current, [field]: value }));
  }

  function toggleInList(field, id) {
    setForm((current) => {
      const idStr = String(id);
      const list = current[field];
      return {
        ...current,
        [field]: list.includes(idStr) ? list.filter((entry) => entry !== idStr) : [...list, idStr],
      };
    });
  }

  async function handleCreate(event) {
    event.preventDefault();
    const variants = [...(form.variantA ? ['A'] : []), ...(form.variantB ? ['B'] : [])];
    const payload = {
      name: form.name,
      description: form.description,
      taxonomy: form.taxonomy ? Number(form.taxonomy) : null,
      phase2_model: form.phase2Model,
      phase2_temperature: Number(form.phase2Temperature || 0),
      phase2_prompt_a: form.phase2PromptA ? Number(form.phase2PromptA) : null,
      phase2_prompt_b: form.phase2PromptB ? Number(form.phase2PromptB) : null,
      phase3_model: form.phase3Model,
      phase3_temperature: Number(form.phase3Temperature || 0),
      phase3_prompt: form.phase3Prompt ? Number(form.phase3Prompt) : null,
      seed_systems: form.seedSystemIds.map(Number),
      fewshots: form.fewshotIds.map(Number),
      generate_systems: form.generateSystems,
      generate_scenarios: form.generateScenarios,
      target_system: !form.generateSystems && form.targetSystem ? Number(form.targetSystem) : null,
      n_systems: Number(form.nSystems || 0),
      n_scenarios_per_system: Number(form.nScenariosPerSystem || 0),
      variants,
    };
    try {
      const created = await createExperiment.mutateAsync(payload);
      setSavedExperiment(created);
      setLaunchResult(null);
    } catch {
      // Surfaced below via createExperiment.error/fieldErrors.
    }
  }

  async function handleLaunch(event) {
    event.preventDefault();
    try {
      const result = await runExperiment.mutateAsync({
        id: savedExperiment.id,
        n_repetitions: Number(nRepetitions || 1),
      });
      setLaunchResult(result);
    } catch {
      // Surfaced below via runExperiment.error - branch on .code, below.
    }
  }

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-semibold text-gray-900">New experiment</h1>
        <p className="mt-1 text-sm text-gray-500">
          Pin the models, prompts, taxonomy and inputs this comparison will use. The experiment
          stays freely editable afterward — each launched repetition pins its own snapshot of the
          seed systems, few-shots, target system, toggles and counts, so a later edit here can
          never rewrite what a repetition already used.
        </p>
      </div>

      <form onSubmit={handleCreate} className="space-y-6 rounded-lg border border-gray-200 bg-white p-4">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div>
            <label htmlFor="name" className="block text-xs font-medium text-gray-700">
              Name
            </label>
            <input
              id="name"
              value={form.name}
              onChange={(event) => updateField('name', event.target.value)}
              className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-1.5 text-sm shadow-sm"
            />
            <FieldError message={fieldErrors.name} />
          </div>
          <div>
            <label htmlFor="taxonomy" className="block text-xs font-medium text-gray-700">
              Taxonomy
            </label>
            <select
              id="taxonomy"
              value={form.taxonomy}
              onChange={(event) => updateField('taxonomy', event.target.value)}
              className="mt-1 block w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm shadow-sm"
            >
              <option value="">Select a taxonomy…</option>
              {taxonomies.map((taxonomy) => (
                <option key={taxonomy.id} value={taxonomy.id}>
                  {taxonomy.vocabulary_id} v{taxonomy.version}
                  {taxonomy.is_active ? ' (active)' : ''}
                </option>
              ))}
            </select>
            <FieldError message={fieldErrors.taxonomy} />
          </div>
        </div>

        <div>
          <label htmlFor="description" className="block text-xs font-medium text-gray-700">
            Description
          </label>
          <textarea
            id="description"
            value={form.description}
            onChange={(event) => updateField('description', event.target.value)}
            rows={2}
            className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-1.5 text-sm shadow-sm"
          />
        </div>

        <fieldset className="space-y-3 rounded-md border border-gray-200 p-3">
          <legend className="px-1 text-sm font-semibold text-gray-900">Phase 2 — generation</legend>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <ModelField
              id="phase2-model"
              label="Phase 2 model"
              value={form.phase2Model}
              onChange={(value) => updateField('phase2Model', value)}
              modelsQuery={modelsQuery}
              models={models}
              fieldError={fieldErrors.phase2_model}
            />
            <div>
              <label htmlFor="phase2-temperature" className="block text-xs font-medium text-gray-700">
                Phase 2 temperature
              </label>
              <input
                id="phase2-temperature"
                type="number"
                step="0.1"
                min="0"
                value={form.phase2Temperature}
                onChange={(event) => updateField('phase2Temperature', event.target.value)}
                className="mt-1 block w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm shadow-sm"
              />
              <FieldError message={fieldErrors.phase2_temperature} />
            </div>
            <div>
              <label htmlFor="phase2-prompt-a" className="block text-xs font-medium text-gray-700">
                Phase 2 prompt — Variant A
              </label>
              <select
                id="phase2-prompt-a"
                value={form.phase2PromptA}
                onChange={(event) => updateField('phase2PromptA', event.target.value)}
                className="mt-1 block w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm shadow-sm"
              >
                <option value="">Select a template…</option>
                {phase2Templates.map((template) => (
                  <option key={template.id} value={template.id}>
                    {template.name} (v{template.version})
                  </option>
                ))}
              </select>
              <FieldError message={fieldErrors.phase2_prompt_a} />
            </div>
            <div>
              <label htmlFor="phase2-prompt-b" className="block text-xs font-medium text-gray-700">
                Phase 2 prompt — Variant B
              </label>
              <select
                id="phase2-prompt-b"
                value={form.phase2PromptB}
                onChange={(event) => updateField('phase2PromptB', event.target.value)}
                className="mt-1 block w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm shadow-sm"
              >
                <option value="">Select a template…</option>
                {phase2Templates.map((template) => (
                  <option key={template.id} value={template.id}>
                    {template.name} (v{template.version})
                  </option>
                ))}
              </select>
              <FieldError message={fieldErrors.phase2_prompt_b} />
            </div>
          </div>
        </fieldset>

        <fieldset className="space-y-3 rounded-md border border-gray-200 p-3">
          <legend className="px-1 text-sm font-semibold text-gray-900">Phase 3 — labelling</legend>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <ModelField
              id="phase3-model"
              label="Phase 3 model"
              value={form.phase3Model}
              onChange={(value) => updateField('phase3Model', value)}
              modelsQuery={modelsQuery}
              models={models}
              fieldError={fieldErrors.phase3_model}
            />
            <div>
              <label htmlFor="phase3-temperature" className="block text-xs font-medium text-gray-700">
                Phase 3 temperature
              </label>
              <input
                id="phase3-temperature"
                type="number"
                step="0.1"
                min="0"
                value={form.phase3Temperature}
                onChange={(event) => updateField('phase3Temperature', event.target.value)}
                className="mt-1 block w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm shadow-sm"
              />
              <FieldError message={fieldErrors.phase3_temperature} />
            </div>
            <div>
              <label htmlFor="phase3-prompt" className="block text-xs font-medium text-gray-700">
                Phase 3 prompt
              </label>
              <select
                id="phase3-prompt"
                value={form.phase3Prompt}
                onChange={(event) => updateField('phase3Prompt', event.target.value)}
                className="mt-1 block w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm shadow-sm"
              >
                <option value="">Select a template…</option>
                {phase3Templates.map((template) => (
                  <option key={template.id} value={template.id}>
                    {template.name} (v{template.version})
                  </option>
                ))}
              </select>
              <FieldError message={fieldErrors.phase3_prompt} />
            </div>
          </div>
        </fieldset>

        <fieldset className="space-y-2 rounded-md border border-gray-200 p-3">
          <legend className="px-1 text-sm font-semibold text-gray-900">Seed systems</legend>
          <p className="text-xs text-gray-500">
            A generated-origin system is refused as a seed — its prose already reflects what a
            prior run produced, so seeding with it would contaminate the comparison.
          </p>
          {systems.map((system) => (
            <label key={system.id} htmlFor={`seed-system-${system.id}`} className="flex items-center gap-2 text-sm text-gray-700">
              <input
                id={`seed-system-${system.id}`}
                type="checkbox"
                checked={form.seedSystemIds.includes(String(system.id))}
                onChange={() => toggleInList('seedSystemIds', system.id)}
              />
              <span>
                {system.title}{' '}
                <span className="text-xs text-gray-500">({ORIGIN_LABELS[system.origin] ?? system.origin})</span>
              </span>
            </label>
          ))}
          <FieldError message={fieldErrors.seed_systems} />
        </fieldset>

        <fieldset className="space-y-2 rounded-md border border-gray-200 p-3">
          <legend className="px-1 text-sm font-semibold text-gray-900">Few-shots</legend>
          {fewshots.map((fewshot) => (
            <label key={fewshot.id} htmlFor={`fewshot-${fewshot.id}`} className="flex items-center gap-2 text-sm text-gray-700">
              <input
                id={`fewshot-${fewshot.id}`}
                type="checkbox"
                checked={form.fewshotIds.includes(String(fewshot.id))}
                onChange={() => toggleInList('fewshotIds', fewshot.id)}
              />
              <span>
                {fewshot.name} <span className="text-xs text-gray-500">(phase {fewshot.phase})</span>
              </span>
            </label>
          ))}
          <FieldError message={fieldErrors.fewshots} />
        </fieldset>

        <fieldset className="space-y-2 rounded-md border border-gray-200 p-3">
          <legend className="px-1 text-sm font-semibold text-gray-900">What to generate</legend>
          <label htmlFor="generate-systems" className="flex items-center gap-2 text-sm text-gray-700">
            <input
              id="generate-systems"
              type="checkbox"
              checked={form.generateSystems}
              onChange={(event) => updateField('generateSystems', event.target.checked)}
            />
            Generate systems
          </label>
          <label htmlFor="generate-scenarios" className="flex items-center gap-2 text-sm text-gray-700">
            <input
              id="generate-scenarios"
              type="checkbox"
              checked={form.generateScenarios}
              onChange={(event) => updateField('generateScenarios', event.target.checked)}
            />
            Generate scenarios
          </label>
          <FieldError message={fieldErrors.generate_systems} />
          <FieldError message={fieldErrors.generate_scenarios} />

          {!form.generateSystems && (
            <div>
              <label htmlFor="target-system" className="block text-xs font-medium text-gray-700">
                Target system
              </label>
              <select
                id="target-system"
                value={form.targetSystem}
                onChange={(event) => updateField('targetSystem', event.target.value)}
                className="mt-1 block w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm shadow-sm"
              >
                <option value="">Select a system…</option>
                {systems.map((system) => (
                  <option key={system.id} value={system.id}>
                    {system.title} ({ORIGIN_LABELS[system.origin] ?? system.origin})
                  </option>
                ))}
              </select>
              <FieldError message={fieldErrors.target_system} />
            </div>
          )}

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <label htmlFor="n-systems" className="block text-xs font-medium text-gray-700">
                Systems per repetition (n_systems)
              </label>
              <input
                id="n-systems"
                type="number"
                min="1"
                value={form.nSystems}
                onChange={(event) => updateField('nSystems', event.target.value)}
                className="mt-1 block w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm shadow-sm"
              />
              <FieldError message={fieldErrors.n_systems} />
            </div>
            <div>
              <label htmlFor="n-scenarios" className="block text-xs font-medium text-gray-700">
                Scenarios per system (n_scenarios_per_system)
              </label>
              <input
                id="n-scenarios"
                type="number"
                min="1"
                value={form.nScenariosPerSystem}
                onChange={(event) => updateField('nScenariosPerSystem', event.target.value)}
                className="mt-1 block w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm shadow-sm"
              />
              <FieldError message={fieldErrors.n_scenarios_per_system} />
            </div>
          </div>
        </fieldset>

        <fieldset className="space-y-2 rounded-md border border-gray-200 p-3">
          <legend className="px-1 text-sm font-semibold text-gray-900">Variants</legend>
          <label htmlFor="variant-a" className="flex items-center gap-2 text-sm text-gray-700">
            <input
              id="variant-a"
              type="checkbox"
              checked={form.variantA}
              onChange={(event) => updateField('variantA', event.target.checked)}
            />
            Variant A
          </label>
          <label htmlFor="variant-b" className="flex items-center gap-2 text-sm text-gray-700">
            <input
              id="variant-b"
              type="checkbox"
              checked={form.variantB}
              onChange={(event) => updateField('variantB', event.target.checked)}
            />
            Variant B
          </label>
          <FieldError message={fieldErrors.variants} />
        </fieldset>

        {showCreateBanner && <ErrorBanner error={createExperiment.error} />}

        <button
          type="submit"
          disabled={createExperiment.isPending}
          className="rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {createExperiment.isPending ? 'Creating…' : 'Create experiment'}
        </button>
      </form>

      {savedExperiment && (
        <section className="space-y-4 rounded-lg border border-gray-200 bg-white p-4">
          <h2 className="text-lg font-medium text-gray-900">Launch “{savedExperiment.name}”</h2>
          <form onSubmit={handleLaunch} className="space-y-3">
            <div>
              <label htmlFor="repetitions" className="block text-xs font-medium text-gray-700">
                Repetitions
              </label>
              <input
                id="repetitions"
                type="number"
                min="1"
                max="100"
                value={nRepetitions}
                onChange={(event) => setNRepetitions(event.target.value)}
                className="mt-1 block w-32 rounded-md border border-gray-300 px-2 py-1.5 text-sm shadow-sm"
              />
              <p className="mt-1 max-w-2xl text-xs text-gray-500">{N_REPETITIONS_HELP_TEXT}</p>
            </div>
            <button
              type="submit"
              disabled={runExperiment.isPending}
              className="rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {runExperiment.isPending ? 'Launching…' : 'Launch'}
            </button>
          </form>

          {runExperiment.isError && (
            <div className="space-y-2">
              <ErrorBanner error={runExperiment.error} />
              {launchErrorCode === 'missing_api_key' && (
                <p className="text-sm text-amber-800">
                  <Link to="/settings" className="font-medium underline">
                    Go to Settings
                  </Link>{' '}
                  to configure an OpenRouter key before launching.
                </p>
              )}
            </div>
          )}

          {launchResult && (
            <div className="space-y-2 rounded-md border border-amber-300 bg-amber-50 p-3">
              <p className="text-sm text-amber-800">{launchResult.detail}</p>
              <ul className="list-disc space-y-1 pl-5 text-sm">
                {launchResult.runs.map((run) => (
                  <li key={run.id}>
                    <Link to={`/pipeline-runs/${run.id}`} className="font-medium text-brand-700 underline">
                      Repetition {run.repetition_index}
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>
      )}
    </div>
  );
}
