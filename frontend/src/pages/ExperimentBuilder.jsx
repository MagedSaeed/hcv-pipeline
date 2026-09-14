import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useAuth } from '../contexts/auth-context';
import {
  useCreateExperiment,
  useExperiment,
  useFewShots,
  useModels,
  usePromptTemplates,
  useSystemDescriptions,
  useTaxonomies,
  useUpdateExperiment,
} from '../api/queries';
import { extractFieldErrors, hasNonFieldError } from '../api/errors';
import ErrorBanner from '../components/ErrorBanner';
import ModelPicker from '../components/ModelPicker';

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
  // Phase 2 always runs; phase 3 (labelling its output) is optional.
  runPhase3: true,
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

// Task 10.3: this form is now reused for BOTH creating (`/experiments/new`,
// no `id`) and editing (`/experiments/:id/edit`, PATCHes in place) - an
// Experiment stays freely editable at any time (spec §9), so there is no
// separate "read-only once launched" state to account for. Turns an
// Experiment row (ExperimentSerializer's own field names) into this form's
// own string-keyed shape - the inverse of `handleSave`'s payload below.
function formFromExperiment(experiment) {
  return {
    name: experiment.name ?? '',
    description: experiment.description ?? '',
    taxonomy: experiment.taxonomy != null ? String(experiment.taxonomy) : '',
    runPhase3: (experiment.phases ?? [2, 3]).includes(3),
    phase2Model: experiment.phase2_model ?? '',
    phase2Temperature: String(experiment.phase2_temperature ?? '1'),
    phase2PromptA: experiment.phase2_prompt_a != null ? String(experiment.phase2_prompt_a) : '',
    phase2PromptB: experiment.phase2_prompt_b != null ? String(experiment.phase2_prompt_b) : '',
    phase3Model: experiment.phase3_model ?? '',
    phase3Temperature: String(experiment.phase3_temperature ?? '0'),
    phase3Prompt: experiment.phase3_prompt != null ? String(experiment.phase3_prompt) : '',
    seedSystemIds: (experiment.seed_systems ?? []).map(String),
    fewshotIds: (experiment.fewshots ?? []).map(String),
    generateSystems: experiment.generate_systems ?? true,
    generateScenarios: experiment.generate_scenarios ?? true,
    targetSystem: experiment.target_system != null ? String(experiment.target_system) : '',
    nSystems: String(experiment.n_systems ?? '3'),
    nScenariosPerSystem: String(experiment.n_scenarios_per_system ?? '5'),
    variantA: (experiment.variants ?? ['A', 'B']).includes('A'),
    variantB: (experiment.variants ?? ['A', 'B']).includes('B'),
  };
}

// Names the lead's backend seeds by default (common.md's API contract) -
// one per phase-2 variant plus the phase-3 template - so a brand-new
// experiment starts with a runnable configuration instead of three blank
// selects. Prefers the ACTIVE version of that name, falling back to the
// highest version number if somehow none is active.
function pickDefaultTemplate(templates, name) {
  const matches = templates.filter((template) => template.name === name);
  if (matches.length === 0) return null;
  return matches.find((template) => template.is_active) ?? matches.reduce((best, t) => (t.version > best.version ? t : best));
}

export default function ExperimentBuilder() {
  const { isStaff } = useAuth();
  const navigate = useNavigate();
  // Present only at `/experiments/:id/edit` - `/experiments/new` renders
  // this same component with no `id` at all, i.e. create mode.
  const { id } = useParams();
  const isEditing = id != null;

  const [form, setForm] = useState(emptyForm);
  // Edit mode only: the form starts empty and is filled in once the
  // existing experiment has loaded - `loadedId` tracks WHICH experiment's
  // values are currently in the form, so navigating from editing one
  // experiment straight to editing another (unlikely, but not impossible)
  // re-seeds the form instead of leaving the previous one's values behind.
  const [loadedId, setLoadedId] = useState(null);

  const existingExperimentQuery = useExperiment(id, { enabled: isEditing });

  // React's own "adjust state when a prop changes" pattern - setState
  // called directly in the render body (never inside a useEffect: that
  // would run this AFTER an already-committed render showed the stale
  // `emptyForm` defaults for one frame, and cascades into a second
  // unnecessary render besides). Guarded by `loadedId !== id` so this only
  // fires once per experiment actually loaded, not on every render.
  if (isEditing && existingExperimentQuery.data && loadedId !== id) {
    setForm(formFromExperiment(existingExperimentQuery.data));
    setLoadedId(id);
  }

  const modelsQuery = useModels({ enabled: isStaff });
  const taxonomiesQuery = useTaxonomies({ enabled: isStaff });
  const phase2TemplatesQuery = usePromptTemplates({ phase: 2 }, { enabled: isStaff });
  const phase3TemplatesQuery = usePromptTemplates({ phase: 3 }, { enabled: isStaff });
  const systemsQuery = useSystemDescriptions({}, { enabled: isStaff });
  const fewshotsQuery = useFewShots({}, { enabled: isStaff });

  const createExperiment = useCreateExperiment();
  const updateExperiment = useUpdateExperiment();
  const saveExperiment = isEditing ? updateExperiment : createExperiment;

  // User feedback: "have one default prompt for both variant[s]... as a
  // default there should be one for each variant." CREATE mode only, once
  // both templates lists have loaded - fills in only whichever of the three
  // prompt fields is still empty, so a value the researcher (or an earlier
  // run of this same effect) already set is never overwritten.
  useEffect(() => {
    if (isEditing) return;
    if (!phase2TemplatesQuery.isSuccess || !phase3TemplatesQuery.isSuccess) return;
    const phase2 = phase2TemplatesQuery.data?.results ?? [];
    const phase3 = phase3TemplatesQuery.data?.results ?? [];
    setForm((current) => {
      let next = current;
      if (!next.phase2PromptA) {
        const match = pickDefaultTemplate(phase2, 'default-generation-variant-a');
        if (match) next = { ...next, phase2PromptA: String(match.id) };
      }
      if (!next.phase2PromptB) {
        const match = pickDefaultTemplate(phase2, 'default-generation-variant-b');
        if (match) next = { ...next, phase2PromptB: String(match.id) };
      }
      if (!next.phase3Prompt) {
        const match = pickDefaultTemplate(phase3, 'default-labelling');
        if (match) next = { ...next, phase3Prompt: String(match.id) };
      }
      return next;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isEditing, phase2TemplatesQuery.isSuccess, phase3TemplatesQuery.isSuccess]);

  if (!isStaff) {
    return (
      <div className="space-y-2">
        <h1 className="text-2xl font-semibold text-gray-900">{isEditing ? 'Edit experiment' : 'New experiment'}</h1>
        <p role="status" className="text-sm text-gray-500">
          Only staff can create or edit an experiment.
        </p>
      </div>
    );
  }

  // Edit mode: hold the form until the real values have loaded, rather
  // than briefly rendering the blank `emptyForm` defaults (which would
  // otherwise flash, and could even be submitted if a click landed in that
  // window).
  if (isEditing && (existingExperimentQuery.isLoading || loadedId !== id)) {
    if (existingExperimentQuery.isError) return <ErrorBanner error={existingExperimentQuery.error} />;
    return <p className="text-sm text-gray-500">Loading experiment…</p>;
  }

  const taxonomies = taxonomiesQuery.data?.results ?? [];
  const phase2Templates = phase2TemplatesQuery.data?.results ?? [];
  const phase3Templates = phase3TemplatesQuery.data?.results ?? [];
  const systems = systemsQuery.data?.results ?? [];
  const fewshots = fewshotsQuery.data?.results ?? [];
  // Task 10.5: the backend refuses ANY generated-origin row as a seed or a
  // target — scratch (an iterate run's own output) or from a real launched
  // repetition alike — because its prose is taxonomy-DERIVED, not a literal
  // quote of it, so none of the text-scanning isolation guards can catch it
  // (spec §5.4: "the origin check is the only thing that closes this
  // route"). Offering a choice the backend will only ever refuse is worse
  // than no choice at all — a researcher who picks one gets a 400 with no
  // way to tell in advance which system was the problem. Both pickers below
  // read from this filtered list, never the raw `systems` above.
  const selectableSystems = systems.filter((system) => system.origin !== 'generated');

  // Task 6's own acceptance bar: map every field-shaped DRF error onto the
  // field it names; fall back to ErrorBanner only for what's left
  // (`detail`/`non_field_errors`) — never both for the same body.
  const fieldErrors = extractFieldErrors(saveExperiment.error);
  const showSaveBanner = saveExperiment.isError && hasNonFieldError(saveExperiment.error);

  function updateField(field, value) {
    setForm((current) => ({ ...current, [field]: value }));
  }

  function toggleInList(field, itemId) {
    setForm((current) => {
      const idStr = String(itemId);
      const list = current[field];
      return {
        ...current,
        [field]: list.includes(idStr) ? list.filter((entry) => entry !== idStr) : [...list, idStr],
      };
    });
  }

  async function handleSave(event) {
    event.preventDefault();
    const variants = [...(form.variantA ? ['A'] : []), ...(form.variantB ? ['B'] : [])];
    const payload = {
      name: form.name,
      description: form.description,
      taxonomy: form.taxonomy ? Number(form.taxonomy) : null,
      phases: form.runPhase3 ? [2, 3] : [2],
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
      const saved = isEditing
        ? await updateExperiment.mutateAsync({ id, ...payload })
        : await createExperiment.mutateAsync(payload);
      // Task 10.3: launching (and everything else about an already-created
      // experiment) now lives on its own detail screen, not inline here -
      // landing there straight after create is what keeps a freshly-made
      // experiment from ever being stranded (the old inline "Launch"
      // section only existed for the one render right after `handleSave`
      // resolved; navigating away, or a launch attempt's own 503 hint
      // unmounting it, lost it for good with no route back).
      navigate(`/experiments/${saved.id}`);
    } catch {
      // Surfaced below via saveExperiment.error/fieldErrors.
    }
  }

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-semibold text-gray-900">{isEditing ? 'Edit experiment' : 'New experiment'}</h1>
        <p className="mt-1 text-sm text-gray-500">
          Choose the models, prompts, taxonomy and inputs this comparison will use. You can edit the
          experiment later; each repetition keeps the settings it was launched with, so an edit never
          changes a repetition that already ran.
        </p>
      </div>

      <form onSubmit={handleSave} className="space-y-6 rounded-lg border border-gray-200 bg-white p-4">
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

        <fieldset className="space-y-2 rounded-md border border-gray-200 p-3">
          <legend className="px-1 text-sm font-semibold text-gray-900">Phases</legend>
          <label htmlFor="phase-2" className="flex items-center gap-2 text-sm text-gray-500">
            <input id="phase-2" type="checkbox" checked disabled />
            Phase 2 — generate scenarios
            <span className="text-xs">(always runs: phase 3 labels what it generates)</span>
          </label>
          <label htmlFor="phase-3" className="flex items-center gap-2 text-sm text-gray-700">
            <input
              id="phase-3"
              type="checkbox"
              checked={form.runPhase3}
              onChange={(event) => updateField('runPhase3', event.target.checked)}
            />
            Phase 3 — label the generated scenarios
          </label>
          <p className="text-xs text-gray-500">
            {form.runPhase3
              ? 'Each repetition generates scenarios, then labels them against the taxonomy.'
              : 'Each repetition only generates scenarios; nothing is labelled.'}
          </p>
          <FieldError message={fieldErrors.phases} />
        </fieldset>

        <fieldset className="space-y-3 rounded-md border border-gray-200 p-3">
          <legend className="px-1 text-sm font-semibold text-gray-900">Phase 2 — generation</legend>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <ModelPicker
              id="phase2-model"
              label="Phase 2 model"
              value={form.phase2Model}
              onChange={(value) => updateField('phase2Model', value)}
              modelsQuery={modelsQuery}
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

        {form.runPhase3 && (
          <fieldset className="space-y-3 rounded-md border border-gray-200 p-3">
            <legend className="px-1 text-sm font-semibold text-gray-900">Phase 3 — labelling</legend>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <ModelPicker
                id="phase3-model"
                label="Phase 3 model"
                value={form.phase3Model}
                onChange={(value) => updateField('phase3Model', value)}
                modelsQuery={modelsQuery}
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
        )}

        <fieldset className="space-y-2 rounded-md border border-gray-200 p-3">
          <legend className="px-1 text-sm font-semibold text-gray-900">Seed systems</legend>
          <p className="text-xs text-gray-500">
            Only seed and hand-written systems are listed. Generated systems can&apos;t be seeds: their text
            already reflects what a model produced, which would skew the comparison.
          </p>
          {selectableSystems.map((system) => (
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
                {selectableSystems.map((system) => (
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
                Systems per repetition
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
                Scenarios per system
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

        {showSaveBanner && <ErrorBanner error={saveExperiment.error} />}

        <button
          type="submit"
          disabled={saveExperiment.isPending}
          className="rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {saveExperiment.isPending ? 'Saving…' : isEditing ? 'Save changes' : 'Create experiment'}
        </button>
      </form>
    </div>
  );
}
