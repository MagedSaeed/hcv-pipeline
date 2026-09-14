import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useAuth } from '../contexts/auth-context';
import {
  useFewShots,
  usePreviewPromptTemplate,
  usePromptTemplates,
  useScenarios,
  useSystemDescriptions,
  useTaxonomies,
} from '../api/queries';
import CodeBlock from '../components/CodeBlock';
import ErrorBanner from '../components/ErrorBanner';

const PHASE_LABELS = { 2: 'Phase 2 — generation', 3: 'Phase 3 — labelling' };
const VARIANT_LABELS = { A: 'Variant A', B: 'Variant B' };

function toggleId(list, id) {
  const idStr = String(id);
  return list.includes(idStr) ? list.filter((entry) => entry !== idStr) : [...list, idStr];
}

// The context inputs for a *rendered* preview - phase 2's seed systems/
// few-shots/generate toggles/counts, or phase 3's scenario/few-shots/
// taxonomy. No equivalent component exists on PromptEditor.jsx yet (its own
// preview panel only ever asked for variant/taxonomy/counts, never actual
// resources) - built fresh here rather than half-extracting one.
function RenderInputs({
  phase,
  variant,
  systems,
  fewshots,
  taxonomies,
  scenarios,
  seedSystemIds,
  onToggleSeedSystem,
  fewshotIds,
  onToggleFewshot,
  taxonomyId,
  onTaxonomyChange,
  scenarioId,
  onScenarioChange,
  generateSystems,
  onGenerateSystemsChange,
  generateScenarios,
  onGenerateScenariosChange,
  nSystems,
  onNSystemsChange,
  nScenariosPerSystem,
  onNScenariosChange,
}) {
  return (
    <div className="grid grid-cols-1 gap-3 rounded-md bg-gray-50 p-3 sm:grid-cols-2">
      {phase === 2 && variant === 'B' && (
        <div>
          <label htmlFor="render-taxonomy" className="block text-xs font-medium text-gray-700">
            Taxonomy
          </label>
          <select
            id="render-taxonomy"
            value={taxonomyId}
            onChange={(event) => onTaxonomyChange(event.target.value)}
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
        <>
          <div>
            <label htmlFor="render-scenario" className="block text-xs font-medium text-gray-700">
              Scenario
            </label>
            <select
              id="render-scenario"
              value={scenarioId}
              onChange={(event) => onScenarioChange(event.target.value)}
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
          <div>
            <label htmlFor="render-taxonomy-p3" className="block text-xs font-medium text-gray-700">
              Taxonomy
            </label>
            <select
              id="render-taxonomy-p3"
              value={taxonomyId}
              onChange={(event) => onTaxonomyChange(event.target.value)}
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
        </>
      )}

      {phase === 2 && (
        <>
          <div className="flex items-center gap-4">
            <label className="flex items-center gap-2 text-sm text-gray-700">
              <input type="checkbox" checked={generateSystems} onChange={(event) => onGenerateSystemsChange(event.target.checked)} />
              Generate systems
            </label>
            <label className="flex items-center gap-2 text-sm text-gray-700">
              <input type="checkbox" checked={generateScenarios} onChange={(event) => onGenerateScenariosChange(event.target.checked)} />
              Generate scenarios
            </label>
          </div>
          <div className="flex gap-3">
            <div>
              <label htmlFor="render-n-systems" className="block text-xs font-medium text-gray-700">
                Systems
              </label>
              <input
                id="render-n-systems"
                type="number"
                min="1"
                value={nSystems}
                onChange={(event) => onNSystemsChange(event.target.value)}
                className="mt-1 block w-24 rounded-md border border-gray-300 px-2 py-1.5 text-sm shadow-sm"
              />
            </div>
            <div>
              <label htmlFor="render-n-scenarios" className="block text-xs font-medium text-gray-700">
                Scenarios per system
              </label>
              <input
                id="render-n-scenarios"
                type="number"
                min="1"
                value={nScenariosPerSystem}
                onChange={(event) => onNScenariosChange(event.target.value)}
                className="mt-1 block w-24 rounded-md border border-gray-300 px-2 py-1.5 text-sm shadow-sm"
              />
            </div>
          </div>
          <fieldset className="rounded-md border border-gray-200 bg-white p-2 sm:col-span-2">
            <legend className="px-1 text-xs font-semibold text-gray-700">Seed systems</legend>
            <div className="max-h-32 space-y-1 overflow-y-auto">
              {systems.map((system) => (
                <label key={system.id} htmlFor={`render-seed-${system.id}`} className="flex items-center gap-2 text-sm text-gray-700">
                  <input
                    id={`render-seed-${system.id}`}
                    type="checkbox"
                    checked={seedSystemIds.includes(String(system.id))}
                    onChange={() => onToggleSeedSystem(system.id)}
                  />
                  {system.title}
                </label>
              ))}
              {systems.length === 0 && <p className="text-xs text-gray-500">No seed systems yet.</p>}
            </div>
          </fieldset>
        </>
      )}

      <fieldset className="rounded-md border border-gray-200 bg-white p-2 sm:col-span-2">
        <legend className="px-1 text-xs font-semibold text-gray-700">Few-shots</legend>
        <div className="max-h-32 space-y-1 overflow-y-auto">
          {fewshots.map((fewshot) => (
            <label key={fewshot.id} htmlFor={`render-fewshot-${fewshot.id}`} className="flex items-center gap-2 text-sm text-gray-700">
              <input
                id={`render-fewshot-${fewshot.id}`}
                type="checkbox"
                checked={fewshotIds.includes(String(fewshot.id))}
                onChange={() => onToggleFewshot(fewshot.id)}
              />
              {fewshot.name}
            </label>
          ))}
          {fewshots.length === 0 && <p className="text-xs text-gray-500">No active few-shots for this phase.</p>}
        </div>
      </fieldset>
    </div>
  );
}

// Read-only view of one prompt template version - name, phase/variant, the
// template fields (or, toggled, the RENDERED prompt against real resources),
// and a selector to switch between every version of this same name (Edit
// lives on its own route, PromptEditor.jsx).
export default function PromptView() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { isStaff } = useAuth();

  const [displayMode, setDisplayMode] = useState('template'); // 'template' | 'rendered'
  const [seedSystemIds, setSeedSystemIds] = useState([]);
  const [fewshotIds, setFewshotIds] = useState([]);
  const [renderTaxonomyId, setRenderTaxonomyId] = useState('');
  const [renderScenarioId, setRenderScenarioId] = useState('');
  const [generateSystems, setGenerateSystems] = useState(true);
  const [generateScenarios, setGenerateScenarios] = useState(true);
  const [nSystems, setNSystems] = useState('1');
  const [nScenariosPerSystem, setNScenariosPerSystem] = useState('3');
  const [defaultsForId, setDefaultsForId] = useState(null);
  const [renderedResult, setRenderedResult] = useState(null);

  const templatesQuery = usePromptTemplates({});
  const templates = templatesQuery.data?.results ?? [];
  const current = templates.find((template) => String(template.id) === id);

  // Every hook below must run every render (rules of hooks) - the
  // "not loaded yet"/"not found" returns happen further down instead.
  // `POST /api/prompts/{id}/preview/` is staff-only server-side (same as
  // the editor's own Preview) - gated here too so a non-staff viewer never
  // fires a request that can only 403.
  const renderModeActive = displayMode === 'rendered' && current != null && isStaff;
  const systemsQuery = useSystemDescriptions({}, { enabled: renderModeActive });
  const fewshotsQuery = useFewShots({ phase: current?.phase }, { enabled: renderModeActive });
  const scenariosQuery = useScenarios({}, { enabled: renderModeActive && current?.phase === 3 });
  const taxonomiesQuery = useTaxonomies({ enabled: renderModeActive });
  const previewMutation = usePreviewPromptTemplate();

  const systems = (systemsQuery.data?.results ?? []).filter((system) => system.origin === 'seed');
  const fewshotsForPhase = (fewshotsQuery.data?.results ?? []).filter((fewshot) => fewshot.is_active);
  const scenarios = scenariosQuery.data?.results ?? [];
  const taxonomies = taxonomiesQuery.data?.results ?? [];
  const activeTaxonomy = taxonomies.find((taxonomy) => taxonomy.is_active) ?? null;

  const supportingDataReady =
    renderModeActive &&
    systemsQuery.isSuccess &&
    fewshotsQuery.isSuccess &&
    taxonomiesQuery.isSuccess &&
    (current?.phase !== 3 || scenariosQuery.isSuccess);

  // Fill in the defaults spec'd for the Rendered tab (all seed systems, all
  // active few-shots for this phase, the active taxonomy, the first seed
  // scenario) exactly once per template id - the same guarded
  // "adjust state when data arrives" pattern PromptEditor.jsx/
  // ExperimentBuilder.jsx already use, so this never overwrites a choice
  // the researcher already made for this same version.
  if (supportingDataReady && defaultsForId !== current.id) {
    setSeedSystemIds(systems.map((system) => String(system.id)));
    setFewshotIds(fewshotsForPhase.map((fewshot) => String(fewshot.id)));
    setRenderTaxonomyId(activeTaxonomy ? String(activeTaxonomy.id) : '');
    if (current.phase === 3) {
      const defaultScenario = scenarios.find((scenario) => scenario.origin === 'seed') ?? scenarios[0] ?? null;
      setRenderScenarioId(defaultScenario ? String(defaultScenario.id) : '');
    }
    setGenerateSystems(true);
    setGenerateScenarios(true);
    setNSystems('3');
    setNScenariosPerSystem('5');
    setDefaultsForId(current.id);
  }

  const defaultsReady = current != null && defaultsForId === current.id;
  const canRender =
    renderModeActive &&
    defaultsReady &&
    (current.phase !== 2 || Boolean(current.variant)) &&
    (current.phase !== 2 || current.variant !== 'B' || Boolean(renderTaxonomyId)) &&
    (current.phase !== 3 || (Boolean(renderTaxonomyId) && Boolean(renderScenarioId)));

  const renderContext =
    current?.phase === 2
      ? {
          variant: current.variant,
          ...(current.variant === 'B' ? { taxonomy: Number(renderTaxonomyId) } : {}),
          seed_systems: seedSystemIds.map(Number),
          fewshots: fewshotIds.map(Number),
          generate_systems: generateSystems,
          generate_scenarios: generateScenarios,
          n_systems: Number(nSystems || 1),
          n_scenarios_per_system: Number(nScenariosPerSystem || 3),
        }
      : {
          taxonomy: Number(renderTaxonomyId),
          scenario: Number(renderScenarioId),
          fewshots: fewshotIds.map(Number),
        };
  const renderRequestKey = canRender ? JSON.stringify({ id: current.id, context: renderContext }) : null;

  // Renders the STORED version (no `system_prompt`/`user_prompt` in the
  // body - `usePreviewPromptTemplate` only treats it as a draft when those
  // are present) against real resources, automatically whenever the tab
  // opens or an input changes.
  useEffect(() => {
    if (!renderRequestKey) return undefined;
    let cancelled = false;
    setRenderedResult(null);
    previewMutation
      .mutateAsync({ id: current.id, ...renderContext })
      .then((result) => {
        if (!cancelled) setRenderedResult(result);
      })
      .catch(() => {
        // Surfaced below via previewMutation.isError/error.
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [renderRequestKey]);

  if (templatesQuery.isLoading) return <p className="text-sm text-gray-500">Loading prompt…</p>;
  if (templatesQuery.isError) return <ErrorBanner error={templatesQuery.error} />;
  if (!current) {
    return <ErrorBanner error={{ response: { data: { detail: 'This prompt version could not be found.' } } }} />;
  }

  const versions = templates
    .filter((template) => template.phase === current.phase && template.name === current.name)
    .sort((a, b) => b.version - a.version);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900">{current.name}</h1>
          <p className="mt-1 text-sm text-gray-500">
            {PHASE_LABELS[current.phase]}
            {current.phase === 2 && current.variant && ` · ${VARIANT_LABELS[current.variant] ?? current.variant}`}
            {current.is_active && ' · active'}
          </p>
        </div>
        {isStaff && (
          <Link
            to={`/prompts/${current.id}/edit`}
            className="rounded-md bg-brand-600 px-3 py-2 text-sm font-medium text-white hover:bg-brand-700"
          >
            Edit
          </Link>
        )}
      </div>

      <div>
        <label htmlFor="version-select" className="block text-xs font-medium text-gray-700">
          Version
        </label>
        <select
          id="version-select"
          value={current.id}
          onChange={(event) => navigate(`/prompts/${event.target.value}`)}
          className="mt-1 block rounded-md border border-gray-300 px-2 py-1.5 text-sm shadow-sm"
        >
          {versions.map((version) => (
            <option key={version.id} value={version.id}>
              Version {version.version}
              {version.is_active ? ' (active)' : ''}
            </option>
          ))}
        </select>
      </div>

      <div className="inline-flex rounded-md border border-gray-300 p-0.5" role="tablist" aria-label="Prompt display mode">
        {[
          { key: 'template', label: 'Template' },
          { key: 'rendered', label: 'Rendered' },
        ].map((tab) => (
          <button
            key={tab.key}
            type="button"
            role="tab"
            aria-selected={displayMode === tab.key}
            onClick={() => setDisplayMode(tab.key)}
            className={`rounded px-3 py-1 text-sm font-medium ${
              displayMode === tab.key ? 'bg-brand-600 text-white' : 'text-gray-600 hover:bg-gray-100'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {displayMode === 'template' && (
        <>
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
            <CodeBlock title="System prompt" code={current.system_prompt} />
            <CodeBlock title="User prompt" code={current.user_prompt} />
          </div>
          <CodeBlock title="Output schema" code={JSON.stringify(current.output_schema ?? {}, null, 2)} />
        </>
      )}

      {displayMode === 'rendered' && (
        <div className="space-y-3">
          <p className="text-xs text-gray-500">
            Filled in with the inputs below, exactly as a run would send it. No model is called.
          </p>
          {!isStaff ? (
            <p role="note" className="text-sm text-amber-800">
              Only staff may render this prompt.
            </p>
          ) : current.phase === 2 && !current.variant ? (
            <p className="text-sm text-amber-700">This prompt has no variant set, so it can&apos;t be rendered.</p>
          ) : !defaultsReady ? (
            <p className="text-sm text-gray-500">Loading render inputs…</p>
          ) : (
            <>
              <RenderInputs
                phase={current.phase}
                variant={current.variant}
                systems={systems}
                fewshots={fewshotsForPhase}
                taxonomies={taxonomies}
                scenarios={scenarios}
                seedSystemIds={seedSystemIds}
                onToggleSeedSystem={(sysId) => setSeedSystemIds((current2) => toggleId(current2, sysId))}
                fewshotIds={fewshotIds}
                onToggleFewshot={(fsId) => setFewshotIds((current2) => toggleId(current2, fsId))}
                taxonomyId={renderTaxonomyId}
                onTaxonomyChange={setRenderTaxonomyId}
                scenarioId={renderScenarioId}
                onScenarioChange={setRenderScenarioId}
                generateSystems={generateSystems}
                onGenerateSystemsChange={setGenerateSystems}
                generateScenarios={generateScenarios}
                onGenerateScenariosChange={setGenerateScenarios}
                nSystems={nSystems}
                onNSystemsChange={setNSystems}
                nScenariosPerSystem={nScenariosPerSystem}
                onNScenariosChange={setNScenariosPerSystem}
              />

              {previewMutation.isPending && <p className="text-sm text-gray-500">Rendering…</p>}
              {previewMutation.isError && <ErrorBanner error={previewMutation.error} />}
              {renderedResult && (
                <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
                  <CodeBlock title="Rendered system prompt" code={renderedResult.system_prompt} />
                  <CodeBlock title="Rendered user prompt" code={renderedResult.user_prompt} />
                </div>
              )}
            </>
          )}
        </div>
      )}

      <Link to="/prompts" className="text-sm font-medium text-brand-700 hover:text-brand-800">
        ← Back to prompts
      </Link>
    </div>
  );
}
