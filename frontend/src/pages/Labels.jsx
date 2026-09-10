import { useState } from 'react';
import {
  useAllLabels,
  useScenarios,
  useSystemDescriptions,
  useTaxonomies,
  useTaxonomyHcvs,
} from '../api/queries';
import EmptyState from '../components/EmptyState';
import ErrorBanner from '../components/ErrorBanner';

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

// One variant's own scenario x HCV matrix. Spec §10: "Variants A and B
// generate different scenarios, so there is no row-by-row comparison to
// draw" - this component only ever reads ITS OWN variant's scenarios and
// labels; it has no reference to the other variant's data at all, so
// there is nothing here that could accidentally zip the two together by
// row index.
function VariantMatrix({ variant, runId, hcvFilter, hcvs, systemTitleById }) {
  const scenariosQuery = useScenarios({ variant, run: runId });
  // `/api/labels/` filters directly by `?run=&variant=` now (the labelling
  // PhaseRun's own `pipeline_run`/`variant`, resolved server-side -
  // pipeline/views.py::LabelViewSet.get_queryset) - no more resolving this
  // variant's phase-3 PhaseRun id(s) through `/api/phase-runs/` first and
  // fanning out one `?phase_run=` label request per id. `useAllLabels`,
  // not `useLabels`: a single repetition can label far more than one page
  // (PAGE_SIZE=50) of scenario x HCV pairs, and a matrix built from only
  // the first page would render a labelled cell as blank - see that
  // hook's own comment in queries.js.
  const labelsQuery = useAllLabels({ run: runId, variant, ...(hcvFilter ? { hcv: hcvFilter } : {}) });

  const scenarios = scenariosQuery.data?.results ?? [];
  // The HCV filter narrows which COLUMN is shown, never which columns
  // exist - `hcvs` itself (the full taxonomy) is always the source of
  // truth for the column set, regardless of which HCVs happen to have a
  // label in this variant. This is the one line responsible for "an HCV
  // with no labels in a variant is visibly empty rather than absent": it
  // is never filtered down to only the HCVs the fetched labels happen to
  // mention.
  const columns = hcvFilter ? hcvs.filter((hcv) => hcv.hcv_id === hcvFilter) : hcvs;

  const labels = labelsQuery.data?.results ?? [];
  const labelByKey = new Map(labels.map((label) => [`${label.scenario}:${label.hcv_id}`, label]));

  const isLoading = scenariosQuery.isLoading || labelsQuery.isLoading;
  const queryError = scenariosQuery.error || labelsQuery.error;
  const isError = scenariosQuery.isError || labelsQuery.isError;

  return (
    <section aria-labelledby={`variant-${variant}-heading`} data-testid={`variant-${variant}-matrix`} className="space-y-3">
      <h2 id={`variant-${variant}-heading`} className="text-lg font-medium text-gray-900">
        Variant {variant}
      </h2>

      {isError && <ErrorBanner error={queryError} />}
      {isLoading && !isError && <p className="text-sm text-gray-500">Loading variant {variant}…</p>}

      {!isLoading && !isError && scenarios.length === 0 && (
        <EmptyState
          title={`No variant ${variant} scenarios for this run`}
          message="This run may not have generated scenarios for this variant yet."
        />
      )}

      {!isLoading && !isError && scenarios.length > 0 && (
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

export default function Labels() {
  const [form, setForm] = useState(emptyFilters);
  const [applied, setApplied] = useState(emptyFilters);

  function handleApply(event) {
    event.preventDefault();
    setApplied(form);
  }

  function handleClear() {
    setForm(emptyFilters);
    setApplied(emptyFilters);
  }

  const taxonomiesQuery = useTaxonomies();
  const taxonomies = taxonomiesQuery.data?.results ?? [];
  const activeTaxonomy = taxonomies.find((taxonomy) => taxonomy.is_active) ?? null;
  const hcvsQuery = useTaxonomyHcvs(activeTaxonomy?.id, { enabled: activeTaxonomy != null });
  const hcvs = hcvsQuery.data?.results ?? [];

  const systemsQuery = useSystemDescriptions();
  const systems = systemsQuery.data?.results ?? [];
  const systemTitleById = new Map(systems.map((system) => [system.id, system.title]));

  const runId = applied.run !== '' ? Number(applied.run) : null;
  const variantsToShow = applied.variant ? [applied.variant] : VARIANTS;

  return (
    <div data-testid="labels-page" className="space-y-8">
      <div>
        <h1 className="text-2xl font-semibold text-gray-900">Labels</h1>
        <p className="mt-1 max-w-3xl text-sm text-gray-500">
          One scenario × HCV matrix per variant. Variant A and variant B generate different
          scenarios by design — read the two matrices side by side rather than expecting matching
          rows between them. This screen presents what was labelled; drawing a conclusion from it
          is the researcher&apos;s job, not this page&apos;s.
        </p>
      </div>

      <form
        onSubmit={handleApply}
        aria-label="Labels filters"
        className="grid grid-cols-1 gap-3 rounded-lg border border-gray-200 bg-white p-4 sm:grid-cols-2 lg:grid-cols-4"
      >
        <div>
          <label htmlFor="labels-filter-run" className="block text-xs font-medium text-gray-700">
            Run (repetition id)
          </label>
          <input
            id="labels-filter-run"
            type="number"
            value={form.run}
            onChange={(event) => setForm((current) => ({ ...current, run: event.target.value }))}
            className="mt-1 block w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm shadow-sm"
          />
        </div>
        <div>
          <label htmlFor="labels-filter-variant" className="block text-xs font-medium text-gray-700">
            Variant
          </label>
          <select
            id="labels-filter-variant"
            value={form.variant}
            onChange={(event) => setForm((current) => ({ ...current, variant: event.target.value }))}
            className="mt-1 block w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm shadow-sm"
          >
            <option value="">All variants</option>
            <option value="A">A</option>
            <option value="B">B</option>
          </select>
        </div>
        <div>
          <label htmlFor="labels-filter-hcv" className="block text-xs font-medium text-gray-700">
            HCV
          </label>
          <select
            id="labels-filter-hcv"
            value={form.hcv}
            onChange={(event) => setForm((current) => ({ ...current, hcv: event.target.value }))}
            className="mt-1 block w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm shadow-sm"
          >
            <option value="">All HCVs</option>
            {hcvs.map((hcv) => (
              <option key={hcv.id} value={hcv.hcv_id}>
                {hcv.hcv_id} — {hcv.name}
              </option>
            ))}
          </select>
        </div>
        <div className="flex items-end gap-2">
          <button
            type="submit"
            className="rounded-md bg-brand-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-brand-700"
          >
            Apply filters
          </button>
          <button
            type="button"
            onClick={handleClear}
            className="rounded-md border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-100"
          >
            Clear
          </button>
        </div>
      </form>

      {taxonomiesQuery.isError && <ErrorBanner error={taxonomiesQuery.error} />}
      {taxonomiesQuery.isSuccess && !activeTaxonomy && (
        <EmptyState
          title="No active taxonomy"
          message="Import and activate a taxonomy on the Taxonomy screen before reading labels."
        />
      )}
      {hcvsQuery.isError && <ErrorBanner error={hcvsQuery.error} />}
      {systemsQuery.isError && <ErrorBanner error={systemsQuery.error} />}

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
          message="Enter a repetition (run) id above and click Apply to see its labels. A scenario x HCV matrix reads one repetition's variant A against its variant B; without a run to scope it, the matrix would mix scenarios from every repetition ever run into one table."
        />
      )}

      {runId != null && activeTaxonomy && hcvsQuery.isSuccess && (
        <div className="space-y-8">
          {variantsToShow.map((variant) => (
            <VariantMatrix
              key={variant}
              variant={variant}
              runId={runId}
              hcvFilter={applied.hcv}
              hcvs={hcvs}
              systemTitleById={systemTitleById}
            />
          ))}
        </div>
      )}
    </div>
  );
}
