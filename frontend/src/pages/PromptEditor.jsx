import { useState } from 'react';
import { useAuth } from '../contexts/auth-context';
import {
  useCreatePhaseRun,
  useCreatePromptTemplate,
  useExperiments,
  usePhaseRun,
  usePhaseRunCalls,
  usePreviewPromptTemplate,
  usePromptTemplates,
  useScenarios,
  useTaxonomies,
  useUpdatePromptTemplate,
} from '../api/queries';
import CodeBlock from '../components/CodeBlock';
import ErrorBanner from '../components/ErrorBanner';
import StatusBadge from '../components/StatusBadge';
import ProgressBar from '../components/ProgressBar';

const PHASES = [
  { value: 2, label: 'Phase 2 — generation' },
  { value: 3, label: 'Phase 3 — labelling' },
];

// prompts/rendering.py's VARIANT_UNBIASED/VARIANT_GUIDED. Spelled out in
// full here rather than just "A"/"B" - this label is the whole point of
// the variant selector below (spec §10: "the editor... must say plainly
// which variant it is").
const VARIANT_COPY = {
  A: { label: 'Variant A', detail: 'taxonomy WITHHELD — must never reference {{ taxonomy }}' },
  B: { label: 'Variant B', detail: 'taxonomy SUPPLIED — renders with the taxonomy in scope' },
};

const SCRATCH_NOTICE =
  'Iterate runs are scratch: never archived, never included in any export bundle (spec §9) — ' +
  'even a run that succeeds leaves no trace beyond what you see on this screen right now.';

// PhaseRunSerializer's `error` field (a plain string) is not itself an
// axios/DRF error object - ErrorBanner (task 1/2) only knows how to read
// that shape via extractErrorMessage. Wrapping a bare string this way lets
// a phase run's own failure reason reuse the exact same banner, rather than
// this page inventing a second way to render an error message.
function asApiError(detail) {
  return { response: { data: { detail } } };
}

function groupTemplatesByName(templates) {
  const groups = new Map();
  for (const template of templates) {
    if (!groups.has(template.name)) groups.set(template.name, []);
    groups.get(template.name).push(template);
  }
  for (const versions of groups.values()) {
    versions.sort((a, b) => b.version - a.version);
  }
  return groups;
}

function parseOutputSchema(text) {
  const trimmed = text.trim();
  if (!trimmed) return {};
  const value = JSON.parse(trimmed);
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new Error('Must be a JSON object (a JSON Schema).');
  }
  return value;
}

const emptyDraft = { systemPrompt: '', userPrompt: '', outputSchemaText: '{}' };

export default function PromptEditor() {
  const { isStaff } = useAuth();

  const [phase, setPhase] = useState(2);
  const [selectedName, setSelectedName] = useState('');
  const [selectedId, setSelectedId] = useState(null);
  const [variant, setVariant] = useState('');
  const [creatingNew, setCreatingNew] = useState(false);
  const [newName, setNewName] = useState('');
  const [draft, setDraft] = useState(emptyDraft);
  const [schemaError, setSchemaError] = useState(null);

  // Preview/save context - the same fields PreviewSerializer accepts
  // (spec §9: "assembling the same fields by hand... is the whole
  // interface"). previewTaxonomyId is an explicit OVERRIDE; when blank the
  // active taxonomy is used (see previewTaxonomyValue below) rather than
  // forcing every visit to this screen to pick one by hand.
  const [previewTaxonomyId, setPreviewTaxonomyId] = useState('');
  const [previewScenarioId, setPreviewScenarioId] = useState('');
  const [previewNSystems, setPreviewNSystems] = useState('3');
  const [previewNScenarios, setPreviewNScenarios] = useState('5');

  const [previewResult, setPreviewResult] = useState(null);
  const [previewError, setPreviewError] = useState(null);
  // Snapshot of exactly what the LAST save+preview attempt was run
  // against. While it still matches the current draft/variant/context, a
  // failed attempt stays blocked - resubmitting byte-identical content
  // would only create another version that fails the identical way.
  // Anything that changes the snapshot (an edit, a variant switch, a
  // different preview taxonomy/scenario) re-enables Save immediately.
  const [lastAttemptKey, setLastAttemptKey] = useState(null);

  // Iterate panel - POST /api/phase-runs/ (spec §9/§10).
  const [iterateExperimentId, setIterateExperimentId] = useState('');
  const [iterateModel, setIterateModel] = useState('');
  const [iterateTemperature, setIterateTemperature] = useState('0');
  const [iterateNSystems, setIterateNSystems] = useState('1');
  const [iterateNScenarios, setIterateNScenarios] = useState('1');
  const [iterateSourcePhaseRunId, setIterateSourcePhaseRunId] = useState('');
  const [iterateRunId, setIterateRunId] = useState(null);

  const templatesQuery = usePromptTemplates({ phase });
  const taxonomiesQuery = useTaxonomies();
  // Only phase 3 previews/iterate runs need a scenario - no point fetching
  // it while the researcher is working on a phase 2 template.
  const scenariosQuery = useScenarios({}, { enabled: phase === 3 });
  const experimentsQuery = useExperiments();

  const previewMutation = usePreviewPromptTemplate();
  const createTemplate = useCreatePromptTemplate();
  const updateTemplate = useUpdatePromptTemplate();
  const createPhaseRun = useCreatePhaseRun();
  const iterateRunQuery = usePhaseRun(iterateRunId);
  const iterateCallsQuery = usePhaseRunCalls(iterateRunId, {
    enabled: iterateRunId != null && iterateRunQuery.data?.status === 'completed',
  });

  const templates = templatesQuery.data?.results ?? [];
  const groups = groupTemplatesByName(templates);
  const names = [...groups.keys()].sort((a, b) => a.localeCompare(b));
  const versions = selectedName ? (groups.get(selectedName) ?? []) : [];

  const taxonomies = taxonomiesQuery.data?.results ?? [];
  const activeTaxonomy = taxonomies.find((taxonomy) => taxonomy.is_active) ?? null;
  const previewTaxonomyValue = previewTaxonomyId || (activeTaxonomy ? String(activeTaxonomy.id) : '');

  const scenarios = scenariosQuery.data?.results ?? [];
  const experiments = experimentsQuery.data?.results ?? [];
  const chosenExperiment = experiments.find((experiment) => experiment.id === Number(iterateExperimentId)) ?? null;

  function clearPreviewState() {
    setPreviewResult(null);
    setPreviewError(null);
  }

  function resetEditorState() {
    setSelectedName('');
    setSelectedId(null);
    setVariant('');
    setCreatingNew(false);
    setNewName('');
    setDraft(emptyDraft);
    setSchemaError(null);
    setLastAttemptKey(null);
    clearPreviewState();
  }

  function handlePhaseChange(nextPhase) {
    setPhase(nextPhase);
    resetEditorState();
  }

  function loadVersion(template) {
    setSelectedName(template.name);
    setSelectedId(template.id);
    setCreatingNew(false);
    setDraft({
      systemPrompt: template.system_prompt,
      userPrompt: template.user_prompt,
      outputSchemaText: JSON.stringify(template.output_schema ?? {}, null, 2),
    });
    setSchemaError(null);
    setLastAttemptKey(null);
    clearPreviewState();
  }

  function handleSelectName(name) {
    const versionsForName = groups.get(name) ?? [];
    if (versionsForName.length > 0) loadVersion(versionsForName[0]);
  }

  function startNewTemplate() {
    setSelectedName('');
    setSelectedId(null);
    setCreatingNew(true);
    setNewName('');
    setVariant('');
    setDraft(emptyDraft);
    setSchemaError(null);
    setLastAttemptKey(null);
    clearPreviewState();
  }

  function updateDraftField(field, value) {
    setDraft((current) => ({ ...current, [field]: value }));
    clearPreviewState();
  }

  function handleVariantChange(nextVariant) {
    setVariant(nextVariant);
    clearPreviewState();
  }

  // The exact snapshot a save+preview attempt was run against - id
  // included, since a successful PATCH always produces a NEW id (spec
  // §5.3/§9), so the same draft text is never mistaken for a different
  // (unattempted) one just because saving moved `selectedId` forward.
  function computeAttemptKey({ id, isNew, name, forVariant, body, taxonomy, scenario }) {
    return JSON.stringify({ id, isNew, name, forVariant, body, taxonomy, scenario });
  }

  const currentAttemptKey = computeAttemptKey({
    id: selectedId,
    isNew: creatingNew,
    name: newName,
    forVariant: variant,
    body: draft,
    taxonomy: previewTaxonomyValue,
    scenario: previewScenarioId,
  });
  const blockedByUnchangedFailure = previewError != null && lastAttemptKey === currentAttemptKey;

  const missingRequiredPreviewInputs =
    phase === 2
      ? variant === '' || (variant === 'B' && !previewTaxonomyValue)
      : !previewTaxonomyValue || !previewScenarioId;

  const missingName = creatingNew ? !newName.trim() : !selectedId;
  const isSaving = createTemplate.isPending || updateTemplate.isPending || previewMutation.isPending;
  const canAttemptSave = isStaff && !missingName && !missingRequiredPreviewInputs && !blockedByUnchangedFailure && !isSaving;

  function saveBlockedReason() {
    if (!isStaff) return null; // covered by its own notice below
    if (missingName) return 'Name this template before saving.';
    if (phase === 2 && variant === '') return 'Choose Variant A or Variant B before saving — the editor must know which one it is validating.';
    if (phase === 2 && variant === 'B' && !previewTaxonomyValue) return 'Choose a taxonomy to render Variant B against.';
    if (phase === 3 && !previewTaxonomyValue) return 'Choose a taxonomy to preview a phase 3 template against.';
    if (phase === 3 && !previewScenarioId) return 'Choose a scenario to preview a phase 3 template against.';
    if (blockedByUnchangedFailure) return 'Fix the render error above before saving again — resubmitting the same content would only create another version that fails the same way.';
    return null;
  }

  async function handleSave(event) {
    event.preventDefault();
    let outputSchema;
    try {
      outputSchema = parseOutputSchema(draft.outputSchemaText);
      setSchemaError(null);
    } catch (error) {
      setSchemaError(error.message);
      return;
    }

    clearPreviewState();

    let saved;
    try {
      if (creatingNew) {
        saved = await createTemplate.mutateAsync({
          phase,
          name: newName.trim(),
          system_prompt: draft.systemPrompt,
          user_prompt: draft.userPrompt,
          output_schema: outputSchema,
        });
      } else {
        saved = await updateTemplate.mutateAsync({
          id: selectedId,
          system_prompt: draft.systemPrompt,
          user_prompt: draft.userPrompt,
          output_schema: outputSchema,
        });
      }
    } catch {
      // Surfaced below via createTemplate.error/updateTemplate.error.
      return;
    }

    setSelectedName(saved.name);
    setSelectedId(saved.id);
    setCreatingNew(false);

    // Validate the version that was just created, through the same
    // renderer a real run uses - never a client-side stand-in. The
    // preview endpoint can only render a PERSISTED template's own stored
    // fields (there is no dry-run body override on it), so this is the
    // earliest point at which the just-saved content can be checked at
    // all; see this file's header comment / the task report for why.
    //
    // Keyed by `saved.id` (the NEW version PATCH/POST just created), not
    // the pre-save `selectedId` still captured in `currentAttemptKey` -
    // otherwise this snapshot could never match a later render's own
    // `currentAttemptKey`, which reads `selectedId` AFTER the update above
    // already moved it to `saved.id`, and the save-blocked guard below
    // would never engage no matter how loudly the render failed.
    setLastAttemptKey(
      computeAttemptKey({
        id: saved.id,
        isNew: false,
        name: newName,
        forVariant: variant,
        body: draft,
        taxonomy: previewTaxonomyValue,
        scenario: previewScenarioId,
      })
    );
    const previewPayload =
      phase === 2
        ? {
            variant,
            ...(variant === 'B' ? { taxonomy: Number(previewTaxonomyValue) } : {}),
            n_systems: Number(previewNSystems || 3),
            n_scenarios_per_system: Number(previewNScenarios || 5),
          }
        : {
            taxonomy: Number(previewTaxonomyValue),
            scenario: Number(previewScenarioId),
          };

    try {
      const result = await previewMutation.mutateAsync({ id: saved.id, ...previewPayload });
      setPreviewResult(result);
    } catch (error) {
      setPreviewError(error);
    }
  }

  const canRunIterate =
    isStaff &&
    selectedId != null &&
    !creatingNew &&
    iterateModel.trim() !== '' &&
    (chosenExperiment ? chosenExperiment.taxonomy != null : activeTaxonomy != null) &&
    (phase !== 2 || variant !== '') &&
    (phase !== 3 || iterateSourcePhaseRunId !== '');

  function iterateBlockedReason() {
    if (!isStaff) return null; // covered by its own notice below
    if (creatingNew || selectedId == null) return 'Save a template above before running it here — iterate mode runs a persisted template id.';
    if (phase === 2 && variant === '') return 'Choose Variant A or Variant B above before running.';
    if (!chosenExperiment && !activeTaxonomy) return 'No taxonomy is active. Activate one on the Taxonomy screen, or borrow an experiment instead.';
    if (phase === 3 && iterateSourcePhaseRunId === '') return 'A phase 3 run needs the id of the phase 2 run whose scenarios it will label.';
    if (iterateModel.trim() === '') return 'Enter a model id to run.';
    return null;
  }

  async function handleRunIterate(event) {
    event.preventDefault();
    const derivedTaxonomyId = chosenExperiment ? chosenExperiment.taxonomy : activeTaxonomy?.id;
    const body = {
      phase,
      model: iterateModel.trim(),
      temperature: Number(iterateTemperature || 0),
      prompt_template: selectedId,
      taxonomy: derivedTaxonomyId,
    };
    if (phase === 2) body.variant = variant;
    if (phase === 3) body.source_phase_run = Number(iterateSourcePhaseRunId);
    if (iterateExperimentId) {
      body.experiment = Number(iterateExperimentId);
      if (phase === 2) {
        body.n_systems = Number(iterateNSystems || 1);
        body.n_scenarios_per_system = Number(iterateNScenarios || 1);
      }
    }
    try {
      const created = await createPhaseRun.mutateAsync(body);
      setIterateRunId(created.id);
    } catch {
      // Surfaced below via createPhaseRun.error.
    }
  }

  const iterateRun = iterateRunQuery.data ?? null;
  const iterateCalls = iterateCallsQuery.data?.results ?? [];

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-semibold text-gray-900">Prompt editor</h1>
        <p className="mt-1 text-sm text-gray-500">
          Jinja2 templates, versioned. Editing never mutates a version — it creates the next one, so
          a run's pinned template id always stays recoverable.
        </p>
        {!isStaff && (
          <p role="note" className="mt-2 text-sm text-amber-800">
            You can browse templates and history. Only staff may edit, preview, or run one.
          </p>
        )}
      </div>

      <section aria-labelledby="picker-heading" className="space-y-3 rounded-lg border border-gray-200 bg-white p-4">
        <h2 id="picker-heading" className="text-lg font-medium text-gray-900">
          Choose a template
        </h2>
        <div className="flex flex-wrap items-end gap-4">
          <div>
            <label htmlFor="phase-select" className="block text-xs font-medium text-gray-700">
              Phase
            </label>
            <select
              id="phase-select"
              value={phase}
              onChange={(event) => handlePhaseChange(Number(event.target.value))}
              className="mt-1 block rounded-md border border-gray-300 px-2 py-1.5 text-sm shadow-sm"
            >
              {PHASES.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label htmlFor="template-select" className="block text-xs font-medium text-gray-700">
              Template
            </label>
            <select
              id="template-select"
              value={creatingNew ? '' : selectedName}
              onChange={(event) => handleSelectName(event.target.value)}
              className="mt-1 block min-w-48 rounded-md border border-gray-300 px-2 py-1.5 text-sm shadow-sm"
            >
              <option value="" disabled>
                Select a template…
              </option>
              {names.map((name) => (
                <option key={name} value={name}>
                  {name} (v{groups.get(name)[0].version})
                </option>
              ))}
            </select>
          </div>

          {isStaff && (
            <button
              type="button"
              onClick={startNewTemplate}
              className="rounded-md border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-100"
            >
              New template
            </button>
          )}
        </div>

        {templatesQuery.isLoading && <p className="text-sm text-gray-500">Loading templates…</p>}
        {templatesQuery.isError && <ErrorBanner error={templatesQuery.error} />}
        {templatesQuery.isSuccess && names.length === 0 && !creatingNew && (
          <p className="text-sm text-gray-500">
            No phase {phase} templates yet. {isStaff && 'Click "New template" to write the first one.'}
          </p>
        )}
      </section>

      {(selectedId != null || creatingNew) && (
        <section aria-labelledby="editor-heading" className="space-y-4 rounded-lg border border-gray-200 bg-white p-4">
          <h2 id="editor-heading" className="text-lg font-medium text-gray-900">
            {creatingNew ? 'New template' : `Editing “${selectedName}”`}
          </h2>

          {creatingNew && (
            <div>
              <label htmlFor="new-name" className="block text-xs font-medium text-gray-700">
                Name
              </label>
              <input
                id="new-name"
                value={newName}
                onChange={(event) => setNewName(event.target.value)}
                disabled={!isStaff}
                className="mt-1 block w-full max-w-md rounded-md border border-gray-300 px-3 py-1.5 text-sm shadow-sm"
              />
            </div>
          )}

          {phase === 2 && (
            <fieldset className="space-y-2 rounded-md border border-amber-300 bg-amber-50 p-3">
              <legend className="px-1 text-sm font-semibold text-amber-900">
                Which variant is this template for?
              </legend>
              <p className="text-xs text-amber-800">
                Phase 2 has two fully independent templates, one per variant. Getting this wrong
                means editing the wrong arm of the experiment.
              </p>
              <div className="flex flex-col gap-2 sm:flex-row sm:gap-6">
                {(['A', 'B']).map((choice) => (
                  <label key={choice} className="flex items-center gap-2 text-sm text-amber-900">
                    <input
                      type="radio"
                      name="variant"
                      value={choice}
                      checked={variant === choice}
                      onChange={() => handleVariantChange(choice)}
                      disabled={!isStaff}
                    />
                    <span>
                      <strong>{VARIANT_COPY[choice].label}</strong> — {VARIANT_COPY[choice].detail}
                    </span>
                  </label>
                ))}
              </div>
              <p className="text-sm font-semibold text-amber-900">
                {variant
                  ? `Editing: ${VARIANT_COPY[variant].label} — ${VARIANT_COPY[variant].detail}`
                  : 'No variant chosen yet — preview and save are disabled until you pick one.'}
              </p>
            </fieldset>
          )}

          <div>
            <label htmlFor="system-prompt" className="block text-xs font-medium text-gray-700">
              System prompt (Jinja2)
            </label>
            <textarea
              id="system-prompt"
              value={draft.systemPrompt}
              onChange={(event) => updateDraftField('systemPrompt', event.target.value)}
              disabled={!isStaff}
              rows={4}
              spellCheck={false}
              className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-xs shadow-sm"
            />
          </div>

          <div>
            <label htmlFor="user-prompt" className="block text-xs font-medium text-gray-700">
              User prompt (Jinja2)
            </label>
            <textarea
              id="user-prompt"
              value={draft.userPrompt}
              onChange={(event) => updateDraftField('userPrompt', event.target.value)}
              disabled={!isStaff}
              rows={8}
              spellCheck={false}
              className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-xs shadow-sm"
            />
          </div>

          <div>
            <label htmlFor="output-schema" className="block text-xs font-medium text-gray-700">
              Output schema (JSON Schema, pinned into the request)
            </label>
            <textarea
              id="output-schema"
              value={draft.outputSchemaText}
              onChange={(event) => updateDraftField('outputSchemaText', event.target.value)}
              disabled={!isStaff}
              rows={4}
              spellCheck={false}
              className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-2 font-mono text-xs shadow-sm"
            />
            {schemaError && <p className="mt-1 text-xs text-red-700">{schemaError}</p>}
          </div>

          <div className="grid grid-cols-1 gap-3 rounded-md bg-gray-50 p-3 sm:grid-cols-2">
            <p className="text-xs font-medium uppercase tracking-wide text-gray-500 sm:col-span-2">
              Preview context — rendered by the real backend renderer, never synthesised here
            </p>
            {(phase === 3 || variant === 'B') && (
              <div>
                <label htmlFor="preview-taxonomy" className="block text-xs font-medium text-gray-700">
                  Taxonomy
                </label>
                <select
                  id="preview-taxonomy"
                  value={previewTaxonomyValue}
                  onChange={(event) => {
                    setPreviewTaxonomyId(event.target.value);
                    clearPreviewState();
                  }}
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
              </div>
            )}
            {phase === 3 && (
              <div>
                <label htmlFor="preview-scenario" className="block text-xs font-medium text-gray-700">
                  Scenario
                </label>
                <select
                  id="preview-scenario"
                  value={previewScenarioId}
                  onChange={(event) => {
                    setPreviewScenarioId(event.target.value);
                    clearPreviewState();
                  }}
                  className="mt-1 block w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm shadow-sm"
                >
                  <option value="">Select a scenario…</option>
                  {scenarios.map((scenario) => (
                    <option key={scenario.id} value={scenario.id}>
                      #{scenario.id} · system {scenario.system_description} · {scenario.scenario_id}
                    </option>
                  ))}
                </select>
              </div>
            )}
            {phase === 2 && (
              <>
                <div>
                  <label htmlFor="preview-n-systems" className="block text-xs font-medium text-gray-700">
                    n_systems
                  </label>
                  <input
                    id="preview-n-systems"
                    type="number"
                    min="1"
                    value={previewNSystems}
                    onChange={(event) => {
                      setPreviewNSystems(event.target.value);
                      clearPreviewState();
                    }}
                    className="mt-1 block w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm shadow-sm"
                  />
                </div>
                <div>
                  <label htmlFor="preview-n-scenarios" className="block text-xs font-medium text-gray-700">
                    n_scenarios_per_system
                  </label>
                  <input
                    id="preview-n-scenarios"
                    type="number"
                    min="1"
                    value={previewNScenarios}
                    onChange={(event) => {
                      setPreviewNScenarios(event.target.value);
                      clearPreviewState();
                    }}
                    className="mt-1 block w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm shadow-sm"
                  />
                </div>
              </>
            )}
          </div>

          {isStaff && (
            <div className="space-y-2">
              <button
                type="button"
                onClick={handleSave}
                disabled={!canAttemptSave}
                className="rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {isSaving ? 'Saving…' : 'Save & preview'}
              </button>
              <p className="text-xs text-gray-500">
                Saving creates the next version of this template, then immediately previews it
                through the real renderer — never a client-side stand-in. A version that fails to
                render stays visible in the history below like any other version, clearly marked, so
                nothing quietly looks safe.
              </p>
              {saveBlockedReason() && <p className="text-xs font-medium text-amber-800">{saveBlockedReason()}</p>}
              {createTemplate.isError && <ErrorBanner error={createTemplate.error} />}
              {updateTemplate.isError && <ErrorBanner error={updateTemplate.error} />}
            </div>
          )}

          {previewError && (
            <div className="space-y-1">
              <p className="text-sm font-semibold text-red-800">
                Render failed — this version cannot be used as-is, and there is no preview to show.
              </p>
              <ErrorBanner error={previewError} />
            </div>
          )}

          {previewResult && (
            <div className="space-y-3">
              <p className="text-sm font-semibold text-green-700">
                Preview OK
                {previewResult.leak_scan_ran
                  ? ` — isolation scan ran, guarded by taxonomy #${previewResult.guarded_by}.`
                  : phase === 2 && variant === 'A'
                    ? ' — isolation scan did NOT run (no guard taxonomy available).'
                    : '.'}
              </p>
              <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
                <CodeBlock title="Rendered system prompt" code={previewResult.system_prompt} />
                <CodeBlock title="Rendered user prompt" code={previewResult.user_prompt} />
              </div>
            </div>
          )}

          {versions.length > 0 && (
            <div>
              <h3 className="text-sm font-medium text-gray-900">Version history</h3>
              <ul className="mt-2 divide-y divide-gray-200 rounded-md border border-gray-200">
                {versions.map((version) => (
                  <li key={version.id} className="flex items-center justify-between gap-3 px-3 py-2 text-sm">
                    <span>
                      <span>Version {version.version}</span>
                      {version.is_active && (
                        <span className="ml-2 inline-flex items-center rounded-full bg-green-100 px-2 py-0.5 text-xs font-medium text-green-700">
                          active
                        </span>
                      )}
                      {version.id === selectedId && (
                        <span className="ml-2 inline-flex items-center rounded-full bg-brand-100 px-2 py-0.5 text-xs font-medium text-brand-700">
                          loaded
                        </span>
                      )}
                    </span>
                    <button
                      type="button"
                      onClick={() => loadVersion(version)}
                      className="rounded-md border border-gray-300 px-2 py-1 text-xs font-medium text-gray-700 hover:bg-gray-100"
                    >
                      View
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>
      )}

      <section aria-labelledby="iterate-heading" className="space-y-4 rounded-lg border border-gray-200 bg-white p-4">
        <h2 id="iterate-heading" className="text-lg font-medium text-gray-900">
          Iterate
        </h2>
        <p role="note" className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800">
          {SCRATCH_NOTICE}
        </p>

        {!isStaff && (
          <p role="note" className="text-sm text-amber-800">
            Only staff may run an iterate-mode call.
          </p>
        )}

        {isStaff && (
          <form onSubmit={handleRunIterate} className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <label htmlFor="iterate-experiment" className="block text-xs font-medium text-gray-700">
                Borrow inputs from experiment (optional)
              </label>
              <select
                id="iterate-experiment"
                value={iterateExperimentId}
                onChange={(event) => setIterateExperimentId(event.target.value)}
                className="mt-1 block w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm shadow-sm"
              >
                <option value="">None — built-in defaults</option>
                {experiments.map((experiment) => (
                  <option key={experiment.id} value={experiment.id}>
                    {experiment.name}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label htmlFor="iterate-model" className="block text-xs font-medium text-gray-700">
                Model
              </label>
              <input
                id="iterate-model"
                value={iterateModel}
                onChange={(event) => setIterateModel(event.target.value)}
                placeholder="openai/gpt-4o-mini"
                className="mt-1 block w-full rounded-md border border-gray-300 px-3 py-1.5 text-sm shadow-sm"
              />
            </div>

            <div>
              <label htmlFor="iterate-temperature" className="block text-xs font-medium text-gray-700">
                Temperature
              </label>
              <input
                id="iterate-temperature"
                type="number"
                step="0.1"
                min="0"
                value={iterateTemperature}
                onChange={(event) => setIterateTemperature(event.target.value)}
                className="mt-1 block w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm shadow-sm"
              />
            </div>

            {phase === 3 && (
              <div>
                <label htmlFor="iterate-source" className="block text-xs font-medium text-gray-700">
                  Source phase run id (a phase 2 run to label)
                </label>
                <input
                  id="iterate-source"
                  type="number"
                  value={iterateSourcePhaseRunId}
                  onChange={(event) => setIterateSourcePhaseRunId(event.target.value)}
                  className="mt-1 block w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm shadow-sm"
                />
              </div>
            )}

            {phase === 2 && iterateExperimentId && (
              <>
                <div>
                  <label htmlFor="iterate-n-systems" className="block text-xs font-medium text-gray-700">
                    Override n_systems (cheap to iterate: try 1)
                  </label>
                  <input
                    id="iterate-n-systems"
                    type="number"
                    min="1"
                    value={iterateNSystems}
                    onChange={(event) => setIterateNSystems(event.target.value)}
                    className="mt-1 block w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm shadow-sm"
                  />
                </div>
                <div>
                  <label htmlFor="iterate-n-scenarios" className="block text-xs font-medium text-gray-700">
                    Override n_scenarios_per_system (cheap to iterate: try 1)
                  </label>
                  <input
                    id="iterate-n-scenarios"
                    type="number"
                    min="1"
                    value={iterateNScenarios}
                    onChange={(event) => setIterateNScenarios(event.target.value)}
                    className="mt-1 block w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm shadow-sm"
                  />
                </div>
              </>
            )}

            <p className="text-xs text-gray-600 sm:col-span-2">
              Taxonomy:{' '}
              {chosenExperiment
                ? `derived from “${chosenExperiment.name}” — not a free choice`
                : activeTaxonomy
                  ? `${activeTaxonomy.vocabulary_id} v${activeTaxonomy.version} (currently active)`
                  : 'no taxonomy is currently active'}
              . Template:{' '}
              {selectedId != null ? `“${selectedName}” v${versions.find((v) => v.id === selectedId)?.version ?? '?'}` : 'none saved yet'}
              .
            </p>

            <div className="sm:col-span-2">
              <button
                type="submit"
                disabled={!canRunIterate || createPhaseRun.isPending}
                className="rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {createPhaseRun.isPending ? 'Starting…' : 'Run'}
              </button>
              {iterateBlockedReason() && (
                <p className="mt-1 text-xs font-medium text-amber-800">{iterateBlockedReason()}</p>
              )}
              {createPhaseRun.isError && <ErrorBanner error={createPhaseRun.error} />}
            </div>
          </form>
        )}

        {iterateRunId != null && (
          <div className="space-y-3 border-t border-gray-200 pt-4">
            <div className="flex items-center gap-3">
              {iterateRun && <StatusBadge status={iterateRun.status} />}
              <span className="text-sm text-gray-600">Phase run #{iterateRunId}</span>
            </div>
            {iterateRun && <ProgressBar progress={iterateRun.progress} label="This run" />}

            {iterateRun?.status === 'failed' && (
              <ErrorBanner error={asApiError(iterateRun.error || 'The run failed.')} />
            )}

            {iterateRun?.status === 'completed' && (
              <div className="space-y-3">
                {iterateCallsQuery.isLoading && <p className="text-sm text-gray-500">Loading the call…</p>}
                {iterateCallsQuery.isError && <ErrorBanner error={iterateCallsQuery.error} />}
                {iterateCalls.map((call) => (
                  <div key={call.id} className="space-y-2 rounded-md border border-gray-200 p-3">
                    <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
                      <CodeBlock title="Rendered system prompt" code={call.rendered_system_prompt} />
                      <CodeBlock title="Rendered user prompt" code={call.rendered_user_prompt} />
                    </div>
                    <CodeBlock title="Raw response" code={call.raw_response} />
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </section>
    </div>
  );
}
