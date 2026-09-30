import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  useAllLabels,
  useExperiments,
  usePhaseRuns,
  usePipelineRuns,
  useScenarios,
  useSystemDescriptions,
  useTaxonomies,
  useTaxonomyHcvs,
} from '../api/queries';
import EmptyState from '../components/EmptyState';
import ErrorBanner from '../components/ErrorBanner';
import SearchableSelect from '../components/SearchableSelect';

// This screen is where the experiment's actual result is read, so it is
// held to a stricter rule than every other screen in the app: it computes
// NOTHING. No count, percentage, coverage score, or A-vs-B delta - spec
// §11 defers metrics explicitly, and at the repetition counts this app
// runs at, a difference between the two variants is sampling noise, not a
// finding. The researcher reads the two matrices and draws the
// conclusion; this file only ever renders what was labelled, verbatim.
const emptyFilters = { run: '', variant: '', hcv: '' };

const VARIANTS = ['A', 'B'];

function scenarioLabel(scenario, systemTitleById) {
  const systemTitle = systemTitleById.get(scenario.system_description) ?? `System #${scenario.system_description}`;
  return `${systemTitle} / ${scenario.scenario_id}`;
}

function taxonomyLabel(taxonomy) {
  return taxonomy ? `${taxonomy.vocabulary_id} v${taxonomy.version}` : null;
}

// Newest-first: finished (falling back to started) timestamp, then id as a
// stable tiebreak for rows sharing a timestamp - same convention Corpus.jsx
// uses for its own run selects.
function byRecency(a, b) {
  return (b.finished_at ?? b.started_at ?? '').localeCompare(a.finished_at ?? a.started_at ?? '') || b.id - a.id;
}

// task 10.2: this screen used to build its column set from the ACTIVE
// taxonomy, not the one the labelling PhaseRun(s) actually pinned
// (`PhaseRunSerializer.taxonomy`, spec §5.1's own pinning rule - the same
// principle RunDetail's "pinned vs. current configuration" split already
// makes visible for a repetition's OTHER inputs). Activating a newer
// taxonomy version would then silently show an OLDER run's labels under
// the NEW vocabulary - a version that may not even contain the HCVs those
// labels were actually written against, dropping them from the matrix with
// no indication anything was wrong.
//
// One variant's own phase-3 labelling run(s) are what actually determine
// its columns now. Ordinarily there is exactly one such PhaseRun per
// (run, variant) - `retry` re-enqueues the SAME row, it never creates a
// second one - but this still resolves defensively rather than assuming
// that always holds: `status: 'none'` (no phase-3 run found yet for this
// variant), `'mismatch'` (more than one distinct taxonomy id pinned across
// them - say so plainly rather than picking one and silently merging), or
// `'ok'` (exactly one - the only case that actually renders a matrix).
function resolvePinnedTaxonomy(phaseRuns) {
  const ids = [...new Set(phaseRuns.map((phaseRun) => phaseRun.taxonomy).filter((id) => id != null))];
  if (ids.length === 0) return { status: 'none', taxonomyId: null, ids };
  if (ids.length > 1) return { status: 'mismatch', taxonomyId: null, ids };
  return { status: 'ok', taxonomyId: ids[0], ids };
}

// One cell of the matrix. A label is a genuine mark you can hover (or
// focus, for keyboard users) to read its evidence and rationale; NO label
// is rendered as an explicit, visually distinct empty marker in the exact
// same grid position - never an omitted <td>, which is what would make an
// entire unlabelled HCV column look identical to one that was never
// queried at all.
function LabelCell({ label, cellLabel, testId }) {
  const [open, setOpen] = useState(false);

  if (!label) {
    return (
      <td data-testid={testId} className="border-l border-gray-100 px-2 py-2 text-center align-middle">
        <span aria-hidden="true" className="text-gray-300">
          –
        </span>
        <span className="sr-only">No label for {cellLabel}.</span>
      </td>
    );
  }

  const tooltipId = `label-tooltip-${label.id}`;

  return (
    <td data-testid={testId} className="relative border-l border-gray-100 px-2 py-2 text-center align-middle">
      <button
        type="button"
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        aria-describedby={open ? tooltipId : undefined}
        aria-label={`Label for ${cellLabel}`}
        className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-brand-600 text-[10px] font-bold leading-none text-white hover:bg-brand-700 focus:outline-none focus:ring-2 focus:ring-brand-400"
      >
        <span aria-hidden="true">●</span>
      </button>
      {open && (
        <div
          role="tooltip"
          id={tooltipId}
          className="absolute left-1/2 z-20 mt-1 w-64 -translate-x-1/2 rounded-md border border-gray-300 bg-white p-2 text-left text-xs normal-case text-gray-700 shadow-lg"
        >
          <p>
            <span className="font-semibold text-gray-900">Evidence:</span> {label.evidence || '(none recorded)'}
          </p>
          <p className="mt-1">
            <span className="font-semibold text-gray-900">Rationale:</span> {label.rationale || '(none recorded)'}
          </p>
          <p className="mt-1 text-gray-500">Source: {label.source}</p>
        </div>
      )}
    </td>
  );
}

// One scenario x HCV matrix over one set of scenarios and its labelling run(s),
// wrapped per variant by VariantMatrix below. Spec §10: "Variants A and B
// generate different scenarios, so there is no row-by-row comparison to
// draw" - each instance reads only its own scenarios and labels, so two
// variants' matrices can never be zipped together by row index.
function LabelMatrix({
  testId,
  heading,
  what,
  scenarioParams,
  phaseRunParams,
  labelParams,
  noScenariosTitle,
  noScenariosMessage,
  hcvFilter,
  taxonomies,
  activeTaxonomy,
  systemTitleById,
}) {
  const scenariosQuery = useScenarios(scenarioParams);

  // The labelling PhaseRun(s) THIS variant's own scenarios/labels came
  // from - read directly to resolve which taxonomy was actually pinned
  // (see `resolvePinnedTaxonomy` above), never the active one.
  const phaseRunsQuery = usePhaseRuns(phaseRunParams);
  const phaseRuns = phaseRunsQuery.data?.results ?? [];
  const pinned = resolvePinnedTaxonomy(phaseRuns);

  const hcvsQuery = useTaxonomyHcvs(pinned.taxonomyId, { enabled: pinned.status === 'ok' });
  // `/api/labels/` filters directly by `?run=&variant=` now (the labelling
  // PhaseRun's own `pipeline_run`/`variant`, resolved server-side -
  // pipeline/views.py::LabelViewSet.get_queryset) - no more resolving this
  // variant's phase-3 PhaseRun id(s) through `/api/phase-runs/` first and
  // fanning out one `?phase_run=` label request per id. `useAllLabels`,
  // not `useLabels`: a single repetition can label far more than one page
  // (PAGE_SIZE=50) of scenario x HCV pairs, and a matrix built from only
  // the first page would render a labelled cell as blank - see that
  // hook's own comment in queries.js. Held off until the pinned taxonomy
  // is resolved - rendering labels against a column set that might still
  // turn out to be a mismatch would be showing a comparison this screen
  // has not actually confirmed is safe to draw.
  const labelsQuery = useAllLabels(
    { ...labelParams, ...(hcvFilter ? { hcv: hcvFilter } : {}) },
    { enabled: pinned.status === 'ok' }
  );

  const scenarios = scenariosQuery.data?.results ?? [];
  // The HCV filter narrows which COLUMN is shown, never which columns
  // exist - `hcvs` itself (this variant's own PINNED taxonomy) is always
  // the source of truth for the column set, regardless of which HCVs
  // happen to have a label in this variant. This is the one line
  // responsible for "an HCV with no labels in a variant is visibly empty
  // rather than absent": it is never filtered down to only the HCVs the
  // fetched labels happen to mention.
  const hcvs = hcvsQuery.data?.results ?? [];
  const columns = hcvFilter ? hcvs.filter((hcv) => hcv.hcv_id === hcvFilter) : hcvs;

  const labels = labelsQuery.data?.results ?? [];
  const labelByKey = new Map(labels.map((label) => [`${label.scenario}:${label.hcv_id}`, label]));

  const isLoading =
    phaseRunsQuery.isLoading ||
    scenariosQuery.isLoading ||
    (pinned.status === 'ok' && (hcvsQuery.isLoading || labelsQuery.isLoading));
  const queryError = phaseRunsQuery.error || scenariosQuery.error || hcvsQuery.error || labelsQuery.error;
  const isError = phaseRunsQuery.isError || scenariosQuery.isError || hcvsQuery.isError || labelsQuery.isError;

  const pinnedTaxonomy = pinned.status === 'ok' ? taxonomies.find((taxonomy) => taxonomy.id === pinned.taxonomyId) : null;
  const differsFromActive =
    pinnedTaxonomy && activeTaxonomy != null && pinnedTaxonomy.id !== activeTaxonomy.id;

  return (
    <section aria-labelledby={`${testId}-heading`} data-testid={testId} className="space-y-3">
      <h2 id={`${testId}-heading`} className="text-lg font-medium text-gray-900">
        {heading}
      </h2>

      {isError && <ErrorBanner error={queryError} />}
      {isLoading && !isError && <p className="text-sm text-gray-500">Loading {what}…</p>}

      {!isLoading && !isError && pinned.status === 'ok' && pinnedTaxonomy && (
        <p className="text-xs text-gray-500">
          Columns: <span className="font-medium text-gray-700">{taxonomyLabel(pinnedTaxonomy)}</span> — the
          taxonomy {what}&apos;s labelling run used
          {differsFromActive
            ? `, not the currently active ${taxonomyLabel(activeTaxonomy)}.`
            : '.'}
        </p>
      )}

      {!isLoading && !isError && pinned.status === 'mismatch' && (
        <EmptyState
          title={`${heading} used more than one taxonomy version`}
          message="Its runs used different taxonomy versions, which can't be shown in one table. Open each run to compare them."
        />
      )}

      {!isLoading && !isError && pinned.status === 'none' && (
        <EmptyState
          title={`No labelling run for ${what}`}
          message={`This run may not have reached phase 3 for ${what} yet.`}
        />
      )}

      {!isLoading && !isError && pinned.status === 'ok' && scenarios.length === 0 && (
        <EmptyState
          title={noScenariosTitle}
          message={noScenariosMessage}
        />
      )}

      {!isLoading && !isError && pinned.status === 'ok' && scenarios.length > 0 && (
        <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white">
          <table className="min-w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-gray-200 bg-gray-50">
                <th scope="col" className="sticky left-0 bg-gray-50 px-3 py-2 text-left text-xs font-medium uppercase tracking-wide text-gray-500">
                  Scenario
                </th>
                {columns.map((hcv) => (
                  <th
                    key={hcv.id}
                    scope="col"
                    title={hcv.name}
                    className="border-l border-gray-100 px-2 py-2 text-center text-xs font-medium uppercase tracking-wide text-gray-500"
                  >
                    {hcv.hcv_id}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {scenarios.map((scenario) => {
                const rowLabel = scenarioLabel(scenario, systemTitleById);
                return (
                  <tr key={scenario.id}>
                    <th scope="row" className="sticky left-0 bg-white px-3 py-2 text-left text-xs font-medium text-gray-900">
                      {rowLabel}
                    </th>
                    {columns.map((hcv) => (
                      <LabelCell
                        key={hcv.id}
                        label={labelByKey.get(`${scenario.id}:${hcv.hcv_id}`)}
                        cellLabel={`${rowLabel} × ${hcv.hcv_id}`}
                        testId={`cell-${scenario.id}-${hcv.hcv_id}`}
                      />
                    ))}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function VariantMatrix({ variant, runId, ...shared }) {
  return (
    <LabelMatrix
      testId={`variant-${variant}-matrix`}
      heading={`Variant ${variant}`}
      what={`variant ${variant}`}
      scenarioParams={{ variant, run: runId }}
      phaseRunParams={{ pipeline_run: runId, variant, phase: 3 }}
      labelParams={{ run: runId, variant }}
      noScenariosTitle={`No variant ${variant} scenarios for this run`}
      noScenariosMessage="This run may not have generated scenarios for this variant yet."
      {...shared}
    />
  );
}

// A phase-3-only run labels chosen corpus scenarios, so there is one matrix and no variant.
function ChosenMatrix({ runId, chosenCount, ...shared }) {
  return (
    <LabelMatrix
      testId="chosen-matrix"
      heading={`${chosenCount} chosen ${chosenCount === 1 ? 'scenario' : 'scenarios'}`}
      what="this run"
      scenarioParams={{ labelled_in_run: runId }}
      phaseRunParams={{ pipeline_run: runId, phase: 3 }}
      labelParams={{ run: runId }}
      noScenariosTitle="The chosen scenarios were deleted"
      noScenariosMessage="None of the scenarios this run was launched with still exist."
      {...shared}
    />
  );
}

export default function Labels() {
  // Run detail links here with `?run=<id>` preselected (task 10.2) - typing
  // a repetition id by hand was the only way in before this. Read once at
  // mount as the initial filter state; a filter changed afterward through
  // the form itself is the source of truth from then on, exactly like
  // every other filter on this screen.
  const [searchParams] = useSearchParams();
  const presetFilters = { ...emptyFilters, run: searchParams.get('run') ?? '' };

  const [filters, setFilters] = useState(presetFilters);

  function handleClear() {
    setFilters(emptyFilters);
  }

  // Options for the "Experiment run" select - same label shape as Corpus.jsx's.
  const pipelineRunsQuery = usePipelineRuns();
  const experimentsQuery = useExperiments();
  const experimentsById = new Map((experimentsQuery.data?.results ?? []).map((experiment) => [experiment.id, experiment]));
  const runOptions = [...(pipelineRunsQuery.data?.results ?? [])].sort(byRecency).map((run) => ({
    value: String(run.id),
    label: `${experimentsById.get(run.experiment)?.name ?? 'Experiment'} · repetition ${run.repetition_index}`,
  }));

  const taxonomiesQuery = useTaxonomies();
  const taxonomies = taxonomiesQuery.data?.results ?? [];
  const activeTaxonomy = taxonomies.find((taxonomy) => taxonomy.is_active) ?? null;
  // The top HCV filter's own OPTIONS list only - a pure narrowing control
  // (`?hcv=<hcv_id>`), not a column source any more (each VariantMatrix
  // resolves its own columns from its own pinned taxonomy above). An
  // hcv_id is a stable identifier across taxonomy versions, so populating
  // this from whichever taxonomy happens to be active is a reasonable,
  // non-blocking convenience - it is never used to decide what a matrix
  // shows.
  const hcvsQuery = useTaxonomyHcvs(activeTaxonomy?.id, { enabled: activeTaxonomy != null });
  const hcvs = hcvsQuery.data?.results ?? [];

  const systemsQuery = useSystemDescriptions();
  const systems = systemsQuery.data?.results ?? [];
  const systemTitleById = new Map(systems.map((system) => [system.id, system.title]));

  const runId = filters.run !== '' ? Number(filters.run) : null;
  const variantsToShow = filters.variant ? [filters.variant] : VARIANTS;
  const selectedRun = (pipelineRunsQuery.data?.results ?? []).find((run) => run.id === runId) ?? null;
  const runPhases = selectedRun?.config?.phases;
  const labelsOnlyRun = Array.isArray(runPhases) && runPhases.length === 1 && runPhases[0] === 3;
  const chosenCount = selectedRun?.config?.label_scenario_ids?.length ?? 0;

  return (
    <div data-testid="labels-page" className="space-y-8">
      <div>
        <h1 className="text-2xl font-semibold text-gray-900">Labels</h1>
        <p className="mt-1 max-w-3xl text-sm text-gray-500">
          One scenario × HCV matrix per variant, or a single matrix for a run that labels chosen
          scenarios. Variant A and variant B generate different scenarios by design — read the two
          matrices side by side rather than expecting matching rows between them. This screen presents what was labelled; drawing a conclusion from it
          is the researcher&apos;s job, not this page&apos;s.
        </p>
      </div>

      <div
        role="group"
        aria-label="Labels filters"
        className="grid grid-cols-1 items-end gap-4 rounded-lg border border-gray-200 bg-white p-4 sm:grid-cols-2 lg:grid-cols-4"
      >
        <SearchableSelect
          id="labels-filter-run"
          label="Experiment run"
          value={filters.run}
          onChange={(value) => setFilters((current) => ({ ...current, run: value }))}
          options={runOptions}
          placeholder="Choose a run…"
        />
        {!labelsOnlyRun && (
          <div>
            <label htmlFor="labels-filter-variant" className="block text-xs font-medium text-gray-700">
              Variant
            </label>
            <select
              id="labels-filter-variant"
              value={filters.variant}
              onChange={(event) => setFilters((current) => ({ ...current, variant: event.target.value }))}
              className="mt-1 block h-9 w-full rounded-md border border-gray-300 px-2 text-sm shadow-sm"
            >
              <option value="">All variants</option>
              <option value="A">A</option>
              <option value="B">B</option>
            </select>
          </div>
        )}
        <div>
          <label htmlFor="labels-filter-hcv" className="block text-xs font-medium text-gray-700">
            HCV
          </label>
          <select
            id="labels-filter-hcv"
            value={filters.hcv}
            onChange={(event) => setFilters((current) => ({ ...current, hcv: event.target.value }))}
            className="mt-1 block h-9 w-full rounded-md border border-gray-300 px-2 text-sm shadow-sm"
          >
            <option value="">All HCVs</option>
            {hcvs.map((hcv) => (
              <option key={hcv.id} value={hcv.hcv_id}>
                {hcv.hcv_id} — {hcv.name}
              </option>
            ))}
          </select>
        </div>
        <div>
          <button
            type="button"
            onClick={handleClear}
            className="h-9 rounded-md border border-gray-300 px-3 text-sm font-medium text-gray-700 hover:bg-gray-100"
          >
            Clear
          </button>
        </div>
      </div>

      {taxonomiesQuery.isError && <ErrorBanner error={taxonomiesQuery.error} />}
      {systemsQuery.isError && <ErrorBanner error={systemsQuery.error} />}
      {(pipelineRunsQuery.isError || experimentsQuery.isError) && (
        <ErrorBanner error={pipelineRunsQuery.error || experimentsQuery.error} />
      )}

      {/* A run stays required even though `/api/labels/` can now filter by
          `?variant=` alone. This is not about bounding a request fan-out
          anymore (there is none) - it is that a scenario x HCV matrix is a
          read of ONE repetition's variant A vs variant B. Drop the run
          scope and a single "variant A" matrix would mix scenarios (and
          possibly taxonomy versions) from every repetition ever run into
          one table - not a comparison a researcher could draw anything
          from, and a real risk of paging through thousands of rows for no
          reason. */}
      {runId == null && (
        <EmptyState
          title="Select a run"
          message="Choose an experiment run above to see its labels. A scenario x HCV matrix reads one repetition's variant A against its variant B; without a run to scope it, the matrix would mix scenarios from every repetition ever run into one table."
        />
      )}

      {runId != null && (
        <div className="space-y-8">
          {labelsOnlyRun ? (
            <ChosenMatrix
              runId={runId}
              chosenCount={chosenCount}
              hcvFilter={filters.hcv}
              taxonomies={taxonomies}
              activeTaxonomy={activeTaxonomy}
              systemTitleById={systemTitleById}
            />
          ) : (
            variantsToShow.map((variant) => (
              <VariantMatrix
                key={variant}
                variant={variant}
                runId={runId}
                hcvFilter={filters.hcv}
                taxonomies={taxonomies}
                activeTaxonomy={activeTaxonomy}
                systemTitleById={systemTitleById}
              />
            ))
          )}
        </div>
      )}
    </div>
  );
}
