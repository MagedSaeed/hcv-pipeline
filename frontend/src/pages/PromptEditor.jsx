import { useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useAuth } from '../contexts/auth-context';
import {
  useCreatePhaseRun,
  useCreatePromptTemplate,
  useDeletePhaseRun,
  useExperiments,
  useModels,
  usePhaseRun,
  usePhaseRunCalls,
  usePreviewPromptTemplate,
  usePromptTemplates,
  useScenarios,
  useTaxonomies,
  useUpdatePromptTemplate,
} from '../api/queries';
import CodeBlock from '../components/CodeBlock';
import ConfirmDialog from '../components/ConfirmDialog';
import ErrorBanner from '../components/ErrorBanner';
import ModelPicker from '../components/ModelPicker';
import SidePanel from '../components/SidePanel';
import StatusBadge from '../components/StatusBadge';
import ProgressBar from '../components/ProgressBar';

const PHASE_LABELS = { 2: 'Phase 2 — generation', 3: 'Phase 3 — labelling' };

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

const DELETE_ITERATE_RUN_MESSAGE =
  'Its generated material — every system description and scenario it produced — goes with it. ' +
  'There is no per-row undo.';

// Task 10.5: the same isolation evidence Run detail shows per phase run
// (`leak_scan_ran`/`guarded_by`, both already on `PhaseRunSerializer`), read
// straight off THIS iterate run rather than off whatever `phase`/`variant`
// happen to be selected in the editor right now - only meaningful for a
// phase-2, variant-A run.
function IterateIsolationNote({ iterateRun }) {
  if (iterateRun.phase !== 2 || iterateRun.variant !== 'A') return null;
  if (iterateRun.leak_scan_ran) {
    return (
      <p className="text-xs font-semibold text-green-700">
        Isolation verified — guarded by taxonomy #{iterateRun.guarded_by.join(', ')}.
      </p>
    );
  }
  return (
    <p className="text-xs font-semibold text-red-700">
      Isolation NOT verified — the leak scan did not run for every call in this run. Do not treat
      this run as proof variant A stayed unbiased.
    </p>
  );
}

// PhaseRunSerializer's `error` field (a plain string) is not itself an
// axios/DRF error object - wrap it so ErrorBanner can render it the same way.
function asApiError(detail) {
  return { response: { data: { detail } } };
}

// A closed-by-default disclosure around one call detail block - kept tiny
// and local rather than a shared component since nothing else in the app
// needs this exact "click to reveal a big JSON/text blob" shape yet.
function CallSection({ title, defaultOpen = false, children }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="rounded-md border border-gray-200">
      <button
        type="button"
        onClick={() => setOpen((current) => !current)}
        aria-expanded={open}
        className="flex w-full items-center justify-between px-2 py-1 text-left text-xs font-medium text-gray-700 hover:bg-gray-50"
      >
        <span>{title}</span>
        <span className="text-gray-400">{open ? 'Hide' : 'Show'}</span>
      </button>
      {open && <div className="border-t border-gray-200 p-2">{children}</div>}
    </div>
  );
}

// One LLMCall row (pipeline.serializers.PhaseRunCallSerializer) - a compact
// header of what happened, then every raw artifact behind its own
// disclosure so a long response/request never dominates the screen. "Raw
// response" opens by default exactly when parsing failed - that is the one
// case a researcher reads it first, not last.
function PhaseRunCallCard({ call }) {
  return (
    <div className="space-y-2 rounded-md border border-gray-200 p-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-gray-600">
        <span className="font-semibold text-gray-900">Call #{call.sequence}</span>
        <span>{call.model}</span>
        <span>HTTP {call.http_status}</span>
        <span>finish: {call.finish_reason || '—'}</span>
        <span>
          {call.prompt_tokens ?? '—'} → {call.completion_tokens ?? '—'} tokens
        </span>
        <span>${Number(call.cost ?? 0).toFixed(4)}</span>
        <span className={call.parsed_ok ? 'font-medium text-green-700' : 'font-medium text-red-700'}>
          {call.parsed_ok ? 'Parsed OK' : 'Parse failed'}
        </span>
      </div>
      {!call.parsed_ok && call.parse_error && (
        <p className="text-xs text-red-700">Parse error: {call.parse_error}</p>
      )}
      {call.transport_error && <p className="text-xs text-red-700">Transport error: {call.transport_error}</p>}
      {call.finish_reason === 'length' && (
        <p role="note" className="rounded-md border border-amber-300 bg-amber-50 px-2 py-1 text-xs text-amber-800">
          The model stopped at its output limit, so the output is incomplete.
        </p>
      )}
      <div className="space-y-2">
        <CallSection title="Raw response" defaultOpen={!call.parsed_ok}>
          <CodeBlock title="Raw response" code={call.raw_response} />
        </CallSection>
        <CallSection title="Full response JSON">
          <CodeBlock title="Full response JSON" code={JSON.stringify(call.response_json ?? {}, null, 2)} />
        </CallSection>
        <CallSection title="Request sent">
          <CodeBlock title="Request sent" code={JSON.stringify(call.request_json ?? {}, null, 2)} />
        </CallSection>
        <CallSection title="Rendered prompt">
          <div className="grid grid-cols-1 gap-2 lg:grid-cols-2">
            <CodeBlock title="Rendered system prompt" code={call.rendered_system_prompt} />
            <CodeBlock title="Rendered user prompt" code={call.rendered_user_prompt} />
          </div>
        </CallSection>
      </div>
    </div>
  );
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

// This page now edits the template named by the URL - `/prompts/new?phase=`
// (empty draft) or `/prompts/:id/edit` (loads that version) - browsing by
// name/phase/version lives on PromptList/PromptView instead.
export default function PromptEditor() {
  const { isStaff } = useAuth();
  const navigate = useNavigate();
  const { id } = useParams();
  const [searchParams] = useSearchParams();
  const isEditing = id != null;
  const phaseParam = Number(searchParams.get('phase'));

  const [loadedId, setLoadedId] = useState(null);
  const [variant, setVariant] = useState('');
  const [newName, setNewName] = useState('');
  const [draft, setDraft] = useState(emptyDraft);
  const [schemaError, setSchemaError] = useState(null);
  const [testOpen, setTestOpen] = useState(false);

  // Preview/save context - the same fields PreviewSerializer accepts.
  // previewTaxonomyId is an explicit OVERRIDE; when blank the active
  // taxonomy is used (see previewTaxonomyValue below).
  const [previewTaxonomyId, setPreviewTaxonomyId] = useState('');
  const [previewScenarioId, setPreviewScenarioId] = useState('');
  const [previewNSystems, setPreviewNSystems] = useState('3');
  const [previewNScenarios, setPreviewNScenarios] = useState('5');

  const [previewResult, setPreviewResult] = useState(null);
  const [previewError, setPreviewError] = useState(null);
  // Snapshot of exactly what the LAST *successful* preview was run against.
  // Save is enabled only while this still matches the current draft/
  // variant/context; any edit, a variant switch, or a different preview
  // taxonomy/scenario invalidates it.
  const [previewedKey, setPreviewedKey] = useState(null);

  // Iterate ("Test this prompt") panel - POST /api/phase-runs/.
  const [iterateExperimentId, setIterateExperimentId] = useState('');
  const [iterateModel, setIterateModel] = useState('');
  const [iterateTemperature, setIterateTemperature] = useState('0');
  const [iterateNSystems, setIterateNSystems] = useState('1');
  const [iterateNScenarios, setIterateNScenarios] = useState('1');
  const [iterateSourcePhaseRunId, setIterateSourcePhaseRunId] = useState('');
  const [iterateRunId, setIterateRunId] = useState(null);
  const [confirmingDeleteIterateRun, setConfirmingDeleteIterateRun] = useState(false);

  // Fetch-all rather than phase-filtered: an edit route only carries an id,
  // not a phase, so the template's own row (found below) is what tells us
  // which phase this is.
  const templatesQuery = usePromptTemplates({});
  const taxonomiesQuery = useTaxonomies();
  const experimentsQuery = useExperiments();
  const modelsQuery = useModels({ enabled: isStaff });

  const previewMutation = usePreviewPromptTemplate();
  const createTemplate = useCreatePromptTemplate();
  const updateTemplate = useUpdatePromptTemplate();
  const createPhaseRun = useCreatePhaseRun();
  const deletePhaseRun = useDeletePhaseRun();
  const iterateRunQuery = usePhaseRun(iterateRunId);
  // A failed run can still carry call records (whatever ran before the
  // failure) - fetch on either terminal status, not completed only. The
  // query is naturally "refetch on status change": it stays disabled while
  // queued/running and only fires once `enabled` flips true on arrival.
  const iterateCallsQuery = usePhaseRunCalls(iterateRunId, {
    enabled:
      iterateRunId != null &&
      (iterateRunQuery.data?.status === 'completed' || iterateRunQuery.data?.status === 'failed'),
  });

  const templates = templatesQuery.data?.results ?? [];
  const currentTemplate = isEditing ? templates.find((template) => String(template.id) === id) : null;
  const phase = isEditing ? currentTemplate?.phase : phaseParam;
  // Only phase 3 previews/iterate runs need a scenario.
  const scenariosQuery = useScenarios({}, { enabled: phase === 3 });

  // React's own "adjust state when a prop changes" pattern (same as
  // ExperimentBuilder.jsx): once the addressed version has loaded, seed the
  // draft/variant from it - guarded so this only fires once per id, not on
  // every render, and re-fires whenever the id in the URL changes (Save
  // navigating to the new version, or a link to a different version).
  if (isEditing && currentTemplate && loadedId !== id) {
    setDraft({
      systemPrompt: currentTemplate.system_prompt,
      userPrompt: currentTemplate.user_prompt,
      outputSchemaText: JSON.stringify(currentTemplate.output_schema ?? {}, null, 2),
    });
    // Task D: preselect from the stored value - a legacy/missing variant on
    // a phase-2 row is "not set" (radio unselected), exactly as a brand-new
    // draft starts.
    setVariant(currentTemplate.variant ?? '');
    setSchemaError(null);
    setPreviewedKey(null);
    setPreviewResult(null);
    setPreviewError(null);
    setLoadedId(id);
  }

  const taxonomies = taxonomiesQuery.data?.results ?? [];
  const activeTaxonomy = taxonomies.find((taxonomy) => taxonomy.is_active) ?? null;
  const previewTaxonomyValue = previewTaxonomyId || (activeTaxonomy ? String(activeTaxonomy.id) : '');

  const scenarios = scenariosQuery.data?.results ?? [];
  const experiments = experimentsQuery.data?.results ?? [];
  const chosenExperiment = experiments.find((experiment) => experiment.id === Number(iterateExperimentId)) ?? null;

  if (!isEditing && phaseParam !== 2 && phaseParam !== 3) {
    return (
      <ErrorBanner
        error={asApiError('Missing or invalid phase — start a new prompt from the prompts list.')}
      />
    );
  }

  if (isEditing && (templatesQuery.isLoading || loadedId !== id)) {
    if (templatesQuery.isError) return <ErrorBanner error={templatesQuery.error} />;
    // A stale lookup (id not found YET) while a background refetch is still
    // in flight - e.g. right after Save navigated here - is not the same as
    // a genuinely missing id: wait for the refetch before calling it gone.
    if (templatesQuery.isSuccess && !currentTemplate && !templatesQuery.isFetching) {
      return <ErrorBanner error={asApiError('This prompt version could not be found.')} />;
    }
    return <p className="text-sm text-gray-500">Loading template…</p>;
  }

  function clearPreviewState() {
    setPreviewResult(null);
    setPreviewError(null);
  }

  function updateDraftField(field, value) {
    setDraft((current) => ({ ...current, [field]: value }));
    clearPreviewState();
  }

  function handleVariantChange(nextVariant) {
    setVariant(nextVariant);
    clearPreviewState();
  }

  // The exact draft+context a preview attempt was run against - keyed off
  // the id (or null for a not-yet-saved draft) so switching which version is
  // loaded never lets a stale success from a DIFFERENT version read as
  // validation for this one.
  function computeAttemptKey({ id: templateId, forVariant, body, taxonomy, scenario }) {
    return JSON.stringify({ id: templateId, forVariant, body, taxonomy, scenario });
  }

  const currentAttemptKey = computeAttemptKey({
    id: isEditing ? currentTemplate.id : null,
    forVariant: variant,
    body: draft,
    taxonomy: previewTaxonomyValue,
    scenario: previewScenarioId,
  });
  // Save is gated on a SUCCESSFUL preview of this exact content (spec §10).
  const previewedCurrentDraft = previewedKey === currentAttemptKey;

  const missingRequiredPreviewInputs =
    phase === 2
      ? variant === '' || (variant === 'B' && !previewTaxonomyValue)
      : !previewTaxonomyValue || !previewScenarioId;

  const missingName = isEditing ? false : !newName.trim();
  const isPreviewing = previewMutation.isPending;
  const isPersisting = createTemplate.isPending || updateTemplate.isPending;
  const canPreview = isStaff && !missingRequiredPreviewInputs && !isPreviewing;
  const canSave = isStaff && !missingName && previewedCurrentDraft && !isPersisting;

  // Task D: an existing phase-2 prompt's variant is fixed once saved -
  // changing it means starting a new prompt from the list instead.
  const variantLocked = isEditing && Boolean(currentTemplate?.variant);

  // A draft differs from what is actually saved (or nothing is saved yet) -
  // this decides whether "Test this prompt" reads "Save & test" or "Run
  // test" (task D). Deliberately body-only: `variant` is never sent on
  // update (it is "writable on create; copied forward on new versions" per
  // the backend contract), so a variant choice alone is never something
  // Save would actually persist.
  const hasUnsavedChanges =
    !isEditing ||
    draft.systemPrompt !== currentTemplate.system_prompt ||
    draft.userPrompt !== currentTemplate.user_prompt ||
    draft.outputSchemaText !== JSON.stringify(currentTemplate.output_schema ?? {}, null, 2);

  function previewBlockedReason() {
    if (!isStaff) return null; // covered by its own notice below
    if (phase === 2 && variant === '') return 'Choose Variant A or Variant B before previewing — the editor must know which one it is validating.';
    if (phase === 2 && variant === 'B' && !previewTaxonomyValue) return 'Choose a taxonomy to render Variant B against.';
    if (phase === 3 && !previewTaxonomyValue) return 'Choose a taxonomy to preview a phase 3 template against.';
    if (phase === 3 && !previewScenarioId) return 'Choose a scenario to preview a phase 3 template against.';
    return null;
  }

  function saveBlockedReason() {
    if (!isStaff) return null; // covered by its own notice below
    if (missingName) return 'Name this template before saving.';
    if (previewedCurrentDraft) return null;
    if (previewError) return 'Fix the render error above, then preview again before saving.';
    return 'Preview this exact content successfully before saving — nothing is saved until it renders cleanly.';
  }

  // Renders the current draft through the real backend renderer WITHOUT
  // persisting anything (spec §9/§10) - never a client-side stand-in.
  async function handlePreview() {
    let outputSchema;
    try {
      outputSchema = parseOutputSchema(draft.outputSchemaText);
      setSchemaError(null);
    } catch (error) {
      setSchemaError(error.message);
      return;
    }

    const attemptKey = currentAttemptKey;
    clearPreviewState();

    const draftFields = {
      system_prompt: draft.systemPrompt,
      user_prompt: draft.userPrompt,
      output_schema: outputSchema,
    };
    const contextFields =
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
      const result = await previewMutation.mutateAsync({
        id: isEditing ? currentTemplate.id : null,
        ...(!isEditing ? { phase } : {}),
        ...draftFields,
        ...contextFields,
      });
      setPreviewResult(result);
      setPreviewedKey(attemptKey);
    } catch (error) {
      setPreviewError(error);
    }
  }

  // Only reachable once `canSave` is true, i.e. once `handlePreview` has
  // already rendered this EXACT draft cleanly. Returns the saved row (the
  // NEW version, per useUpdatePromptTemplate's own comment) or null on
  // failure - shared by the top Save button and "Save & test" below.
  async function saveDraft() {
    let outputSchema;
    try {
      outputSchema = parseOutputSchema(draft.outputSchemaText);
      setSchemaError(null);
    } catch (error) {
      setSchemaError(error.message);
      return null;
    }

    try {
      if (isEditing) {
        return await updateTemplate.mutateAsync({
          id: currentTemplate.id,
          system_prompt: draft.systemPrompt,
          user_prompt: draft.userPrompt,
          output_schema: outputSchema,
        });
      }
      return await createTemplate.mutateAsync({
        phase,
        name: newName.trim(),
        ...(phase === 2 ? { variant } : {}),
        system_prompt: draft.systemPrompt,
        user_prompt: draft.userPrompt,
        output_schema: outputSchema,
      });
    } catch {
      // Surfaced below via createTemplate.error/updateTemplate.error.
      return null;
    }
  }

  async function handleSave(event) {
    event.preventDefault();
    const saved = await saveDraft();
    if (saved) navigate(`/prompts/${saved.id}/edit`, { replace: true });
  }

  async function runIterate(templateId) {
    const derivedTaxonomyId = chosenExperiment ? chosenExperiment.taxonomy : activeTaxonomy?.id;
    const body = {
      phase,
      model: iterateModel.trim(),
      temperature: Number(iterateTemperature || 0),
      prompt_template: templateId,
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

  // Task D: a test run needs a saved version - with unsaved changes, save
  // (creating the next version) first, then run THAT one; otherwise run the
  // version already being edited. The route is updated to the new version
  // only AFTER the run is already under way (`iterateRunId` set), so the
  // brief re-render while the new version loads never has a window where
  // the just-started run is invisible.
  async function handleTestPrompt(event) {
    event.preventDefault();
    if (hasUnsavedChanges) {
      const saved = await saveDraft();
      if (!saved) return;
      await runIterate(saved.id);
      navigate(`/prompts/${saved.id}/edit`, { replace: true });
    } else {
      await runIterate(currentTemplate.id);
    }
  }

  function testBlockedReason() {
    if (!isStaff) return null; // covered by its own notice below
    if (phase === 2 && variant === '') return 'Choose Variant A or Variant B above before testing.';
    if (!chosenExperiment && !activeTaxonomy) return 'No taxonomy is active. Activate one on the Taxonomy screen, or borrow an experiment instead.';
    if (phase === 3 && iterateSourcePhaseRunId === '') return 'A phase 3 run needs the id of the phase 2 run whose scenarios it will label.';
    if (iterateModel.trim() === '') return 'Enter a model id to run.';
    if (hasUnsavedChanges) return saveBlockedReason();
    return null;
  }

  const canTest = isStaff && testBlockedReason() == null && !createPhaseRun.isPending && !isPersisting;

  async function handleDeleteIterateRunConfirmed() {
    try {
      await deletePhaseRun.mutateAsync(iterateRunId);
      setConfirmingDeleteIterateRun(false);
      setIterateRunId(null);
    } catch {
      // Surfaced below via deletePhaseRun.isError/error - dialog stays open.
    }
  }

  const iterateRun = iterateRunQuery.data ?? null;
  const iterateCalls = iterateCallsQuery.data?.results ?? [];

  // A small status dot on the "Test this prompt" button - the only trace of
  // a run left once the panel itself is hidden (it is never unmounted, but
  // a closed panel shows nothing on screen until reopened).
  const testStatusDotClass =
    iterateRun?.status === 'failed'
      ? 'bg-red-500'
      : iterateRun?.status === 'queued' || iterateRun?.status === 'running'
        ? 'animate-pulse bg-blue-500'
        : null;

  return (
    <div className={`space-y-8 ${testOpen ? 'md:pr-[calc(45%+1.5rem)]' : ''}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900">
            {isEditing ? `Editing “${currentTemplate.name}”` : 'New prompt'}
          </h1>
          <p className="mt-1 text-sm text-gray-500">
            {PHASE_LABELS[phase]} · Jinja2 templates, versioned. Saving never mutates a version — it
            creates the next one, so a run's pinned template id always stays recoverable.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setTestOpen(true)}
            className="relative rounded-md border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-100"
          >
            Test this prompt
            {testStatusDotClass && (
              <span
                data-testid="test-status-dot"
                className={`absolute -right-1 -top-1 h-2.5 w-2.5 rounded-full ring-2 ring-white ${testStatusDotClass}`}
              />
            )}
          </button>
          {isEditing && (
            <Link
              to={`/prompts/${currentTemplate.id}`}
              className="rounded-md border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-100"
            >
              View
            </Link>
          )}
        </div>
      </div>

      <section aria-labelledby="editor-heading" className="space-y-4 rounded-lg border border-gray-200 bg-white p-4">
        <h2 id="editor-heading" className="sr-only">
          Template editor
        </h2>

        {!isEditing && (
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
            {variantLocked && (
              <p className="text-xs font-medium text-amber-900">
                Variant is fixed for an existing prompt — start a new prompt from the list to use
                the other variant.
              </p>
            )}
            <div className="flex flex-col gap-2 sm:flex-row sm:gap-6">
              {(['A', 'B']).map((choice) => (
                <label key={choice} className="flex items-center gap-2 text-sm text-amber-900">
                  <input
                    type="radio"
                    name="variant"
                    value={choice}
                    checked={variant === choice}
                    onChange={() => handleVariantChange(choice)}
                    disabled={!isStaff || variantLocked}
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
                    {scenario.is_scratch ? ' · scratch (iterate run)' : ''}
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
            <div className="flex flex-wrap items-center gap-3">
              <button
                type="button"
                onClick={handlePreview}
                disabled={!canPreview}
                className="rounded-md border border-brand-600 px-4 py-2 text-sm font-medium text-brand-700 hover:bg-brand-50 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {isPreviewing ? 'Previewing…' : 'Preview'}
              </button>
              <button
                type="button"
                onClick={handleSave}
                disabled={!canSave}
                className="rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {isPersisting ? 'Saving…' : 'Save'}
              </button>
            </div>
            <p className="text-xs text-gray-500">
              Preview renders the draft above through the real backend renderer — never a
              client-side stand-in — and persists nothing, win or lose. Save is disabled until this
              exact draft has previewed cleanly for this exact variant.
            </p>
            {previewBlockedReason() && (
              <p className="text-xs font-medium text-amber-800">{previewBlockedReason()}</p>
            )}
            {saveBlockedReason() && <p className="text-xs font-medium text-amber-800">{saveBlockedReason()}</p>}
            {createTemplate.isError && <ErrorBanner error={createTemplate.error} />}
            {updateTemplate.isError && <ErrorBanner error={updateTemplate.error} />}
          </div>
        )}

        {previewError && (
          <div className="space-y-1">
            <p className="text-sm font-semibold text-red-800">
              Render failed — nothing was saved, and there is no preview to show.
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
                : '.'}
            </p>
            <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
              <CodeBlock title="Rendered system prompt" code={previewResult.system_prompt} />
              <CodeBlock title="Rendered user prompt" code={previewResult.user_prompt} />
            </div>
          </div>
        )}
      </section>

      <SidePanel title="Test this prompt" open={testOpen} onClose={() => setTestOpen(false)}>
        <div className="space-y-4">
          <p role="note" className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800">
            {SCRATCH_NOTICE}
          </p>

          {!isStaff && (
            <p role="note" className="text-sm text-amber-800">
              Only staff may run an iterate-mode call.
            </p>
          )}

          {isStaff && (
            <form onSubmit={handleTestPrompt} className="grid grid-cols-1 gap-4 sm:grid-cols-2">
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

                <ModelPicker
                  id="iterate-model"
                  label="Model"
                  value={iterateModel}
                  onChange={setIterateModel}
                  modelsQuery={modelsQuery}
                />

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
                  {isEditing
                    ? `“${currentTemplate.name}” v${currentTemplate.version}${hasUnsavedChanges ? ' (edited, not yet saved)' : ''}`
                    : 'not saved yet — “Save & test” will create it'}
                  .
                </p>

                <div className="sm:col-span-2">
                  <button
                    type="submit"
                    disabled={!canTest || createPhaseRun.isPending}
                    className="rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {createPhaseRun.isPending
                      ? 'Starting…'
                      : hasUnsavedChanges
                        ? 'Save & test'
                        : 'Run test'}
                  </button>
                  {testBlockedReason() && (
                    <p className="mt-1 text-xs font-medium text-amber-800">{testBlockedReason()}</p>
                  )}
                  {createTemplate.isError && hasUnsavedChanges && <ErrorBanner error={createTemplate.error} />}
                  {updateTemplate.isError && hasUnsavedChanges && <ErrorBanner error={updateTemplate.error} />}
                  {createPhaseRun.isError && <ErrorBanner error={createPhaseRun.error} />}
                </div>
              </form>
            )}

            {iterateRunId != null && (
              <div className="space-y-3 border-t border-gray-200 pt-4">
                <div className="flex flex-wrap items-center gap-3">
                  {iterateRun && <StatusBadge status={iterateRun.status} />}
                  <span className="text-sm text-gray-600">Phase run #{iterateRunId}</span>
                  {isStaff && !confirmingDeleteIterateRun && (
                    <button
                      type="button"
                      onClick={() => setConfirmingDeleteIterateRun(true)}
                      className="rounded-md border border-red-600 px-2 py-1 text-xs font-medium text-red-700 hover:bg-red-50"
                    >
                      Delete this run
                    </button>
                  )}
                </div>
                {iterateRun && <ProgressBar progress={iterateRun.progress} label="This run" />}
                {iterateRun && <IterateIsolationNote iterateRun={iterateRun} />}

                {confirmingDeleteIterateRun && (
                  <ConfirmDialog
                    title={`Delete iterate run #${iterateRunId}?`}
                    message={DELETE_ITERATE_RUN_MESSAGE}
                    confirmLabel="Confirm"
                    cancelLabel="Never mind"
                    pending={deletePhaseRun.isPending}
                    onConfirm={handleDeleteIterateRunConfirmed}
                    onCancel={() => setConfirmingDeleteIterateRun(false)}
                  />
                )}
                {deletePhaseRun.isError && <ErrorBanner error={deletePhaseRun.error} />}

                {iterateRun?.status === 'failed' && (
                  <ErrorBanner error={asApiError(iterateRun.error || 'The run failed.')} />
                )}

                {(iterateRun?.status === 'completed' || iterateRun?.status === 'failed') && (
                  <div className="space-y-3">
                    {iterateCallsQuery.isLoading && <p className="text-sm text-gray-500">Loading the calls…</p>}
                    {iterateCallsQuery.isError && <ErrorBanner error={iterateCallsQuery.error} />}
                    {iterateCalls.map((call) => (
                      <PhaseRunCallCard key={call.id} call={call} />
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        </SidePanel>
    </div>
  );
}
