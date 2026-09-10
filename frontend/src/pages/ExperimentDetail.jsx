import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useAuth } from '../contexts/auth-context';
import {
  useExperiment,
  useFewShots,
  usePipelineRuns,
  usePromptTemplates,
  useRunExperiment,
  useRunOptions,
  useSystemDescriptions,
  useTaxonomies,
} from '../api/queries';
import { extractFieldErrors, hasNonFieldError } from '../api/errors';
import ErrorBanner from '../components/ErrorBanner';
import ProgressBar from '../components/ProgressBar';
import StatusBadge from '../components/StatusBadge';

// Task 10.3: before this screen, launching lived only in ExperimentBuilder's
// own in-memory state in the moment right after `handleCreate` resolved -
// navigate away, or hit the builder's own 503 `missing_api_key` hint (which
// unmounts that state to show the "Go to Settings" link), and the
// experiment was permanently stranded: no `/experiments/:id` route, no
// Dashboard link, and names are unique so even re-creating it was refused.
// This is the one place an already-created experiment can always be
// reopened, edited (via the SAME builder form, reused through PATCH) and
// launched, no matter how long ago it was created or how the researcher
// got here.

function titleLookup(rows, key = 'title') {
  const map = new Map(rows.map((row) => [row.id, row[key]]));
  return (id) => (id == null ? null : (map.get(id) ?? `#${id}`));
}

function templateLookup(templates) {
  const map = new Map(templates.map((template) => [template.id, template]));
  return (id) => {
    if (id == null) return null;
    const template = map.get(id);
    return template ? `${template.name} (v${template.version})` : `#${id}`;
  };
}

function taxonomyLookup(taxonomies) {
  const map = new Map(taxonomies.map((taxonomy) => [taxonomy.id, taxonomy]));
  return (id) => {
    if (id == null) return '(none)';
    const taxonomy = map.get(id);
    return taxonomy ? `${taxonomy.vocabulary_id} v${taxonomy.version}` : `#${id}`;
  };
}

function FieldNote({ message }) {
  if (!message) return null;
  return <p className="mt-1 text-xs text-red-700">{message}</p>;
}

// Coordinator review (final polish, item 1): the only launch-refusal fields
// this screen has an inline slot for at all - the two `<FieldNote>`s next
// to Phase 2 prompt A/B above. `RunRequestSerializer`/`Experiment.clean()`
// (backend/pipeline/views.py's `run` action, ~line 337) can refuse a launch
// keyed on plenty of OTHER fields too (`seed_systems`, `target_system`,
// `taxonomy`, `generate_systems`, `n_repetitions`, ...), or with no field at
// all (`non_field_errors`/a bare `detail`). Before this, `showLaunchBanner`
// only fired for the latter shape (`hasNonFieldError`, shared with
// ExperimentBuilder.jsx, which assumes every field error already has an
// inline slot somewhere in a FULL FORM - true there, false here: this
// screen is a read-only config summary with exactly two inline slots). A
// refusal on `seed_systems` landed in neither an inline note nor the
// banner - it simply vanished, and Launch appeared to silently do nothing.
const INLINE_LAUNCH_FIELDS = new Set(['phase2_prompt_a', 'phase2_prompt_b']);

export default function ExperimentDetail() {
  const { id } = useParams();
  const experimentId = Number(id);
  const { isStaff } = useAuth();

  const [nRepetitions, setNRepetitions] = useState('1');
  const [launchResult, setLaunchResult] = useState(null);

  const experimentQuery = useExperiment(experimentId);
  // One row per repetition, `?experiment=` (spec §9) - this screen's own
  // "runs" list; each links straight to Run detail. Polls itself exactly
  // like the Dashboard's own run list does (`usePipelineRuns`'s built-in
  // `refetchInterval`), so a repetition launched from HERE keeps moving
  // without a manual refresh.
  const runsQuery = usePipelineRuns({ experiment: experimentId });
  const runOptionsQuery = useRunOptions(experimentId);
  const repetitionsHelpText = runOptionsQuery.data?.actions?.POST?.n_repetitions?.help_text;
  const runExperiment = useRunExperiment();

  const systemsQuery = useSystemDescriptions();
  const fewshotsQuery = useFewShots();
  const phase2TemplatesQuery = usePromptTemplates({ phase: 2 });
  const phase3TemplatesQuery = usePromptTemplates({ phase: 3 });
  const taxonomiesQuery = useTaxonomies();

  if (experimentQuery.isLoading) return <p className="text-sm text-gray-500">Loading experiment…</p>;
  if (experimentQuery.isError) return <ErrorBanner error={experimentQuery.error} />;
  const experiment = experimentQuery.data;
  if (!experiment) return null;

  const runs = runsQuery.data?.results ?? [];
  const systems = systemsQuery.data?.results ?? [];
  const fewshots = fewshotsQuery.data?.results ?? [];
  const phase2Templates = phase2TemplatesQuery.data?.results ?? [];
  const phase3Templates = phase3TemplatesQuery.data?.results ?? [];
  const taxonomies = taxonomiesQuery.data?.results ?? [];

  const systemTitle = titleLookup(systems, 'title');
  const fewshotName = titleLookup(fewshots, 'name');
  const phase2PromptLabel = templateLookup(phase2Templates);
  const phase3PromptLabel = templateLookup(phase3Templates);
  const taxonomyLabel = taxonomyLookup(taxonomies);

  // The backend's own launch-time dry render (spec §10.4) refuses a
  // template that fails as its own variant with a standard DRF field
  // error keyed on `phase2_prompt_a`/`phase2_prompt_b` - read the same way
  // every other field error in this app is, and shown next to the SAME
  // field the create/edit form itself uses, even though this error arrived
  // from the LAUNCH action, not a save.
  const launchFieldErrors = extractFieldErrors(runExperiment.error);
  // Every field error THIS SCREEN has no inline slot for - falls back to a
  // visible banner naming the field, rather than disappearing the way
  // `hasNonFieldError` alone let it (see `INLINE_LAUNCH_FIELDS`'s own
  // comment above).
  const uncoveredLaunchFieldErrors = Object.entries(launchFieldErrors).filter(
    ([field]) => !INLINE_LAUNCH_FIELDS.has(field)
  );
  const showLaunchBanner =
    runExperiment.isError && (hasNonFieldError(runExperiment.error) || uncoveredLaunchFieldErrors.length > 0);
  const launchErrorCode = runExperiment.error?.response?.data?.code;

  // Coordinator review (final polish, item 1): `OPTIONS .../run/` reports
  // `n_repetitions` as 1-100 (`RunRequestSerializer`), but nothing here
  // ever enforced that client-side - the field was a bare, unclamped
  // string, and the API's own out-of-range 400 fell into exactly the
  // silent-failure gap above. Fixed in both directions: constrained here so
  // a normal user can never submit an out-of-range value through this UI at
  // all, AND `uncoveredLaunchFieldErrors` above still surfaces an
  // `n_repetitions` refusal if the API ever sends one anyway (a stale tab,
  // a future backend rule this screen doesn't know about yet).
  const parsedRepetitions = Number(nRepetitions);
  const repetitionsInvalid =
    nRepetitions.trim() === '' || !Number.isInteger(parsedRepetitions) || parsedRepetitions < 1 || parsedRepetitions > 100;

  async function handleLaunch(event) {
    event.preventDefault();
    if (repetitionsInvalid) return;
    try {
      const result = await runExperiment.mutateAsync({ id: experimentId, n_repetitions: parsedRepetitions });
      setLaunchResult(result);
    } catch {
      // Surfaced below via runExperiment.error/launchFieldErrors.
    }
  }

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900">{experiment.name}</h1>
          {experiment.description && (
            <p className="mt-1 max-w-2xl text-sm text-gray-500">{experiment.description}</p>
          )}
        </div>
        {isStaff && (
          <Link
            to={`/experiments/${experimentId}/edit`}
            className="rounded-md border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-100"
          >
            Edit
          </Link>
        )}
      </div>

      <section
        aria-labelledby="config-heading"
        data-testid="current-config"
        className="space-y-3 rounded-lg border border-gray-200 bg-white p-4"
      >
        <h2 id="config-heading" className="text-lg font-medium text-gray-900">
          Current configuration
        </h2>
        {/* Spec §9: Experiment stays completely unrestricted to edit at any
            time - "current" is named deliberately here, never "pinned":
            this is NOT what any particular repetition below actually used.
            What a repetition used is on its OWN Run detail page. */}
        <p className="text-xs text-gray-500">
          The experiment&apos;s current, live, editable configuration — not necessarily what any
          repetition below actually used. Each repetition pins its own snapshot at launch, shown on
          its own Run detail page.
        </p>
        <dl className="grid grid-cols-1 gap-3 text-sm text-gray-700 sm:grid-cols-2">
          <div>
            <dt className="font-medium text-gray-500">Taxonomy</dt>
            <dd>{taxonomyLabel(experiment.taxonomy)}</dd>
          </div>
          <div>
            <dt className="font-medium text-gray-500">Variants</dt>
            <dd>{(experiment.variants ?? []).join(', ') || '(none)'}</dd>
          </div>
          <div>
            <dt className="font-medium text-gray-500">Phase 2 model / temperature</dt>
            <dd>
              {experiment.phase2_model} · {experiment.phase2_temperature}
            </dd>
          </div>
          <div>
            <dt className="font-medium text-gray-500">Phase 3 model / temperature</dt>
            <dd>
              {experiment.phase3_model} · {experiment.phase3_temperature}
            </dd>
          </div>
          <div>
            <dt className="font-medium text-gray-500">Phase 2 prompt — Variant A</dt>
            <dd>{phase2PromptLabel(experiment.phase2_prompt_a) ?? '(none)'}</dd>
            <FieldNote message={launchFieldErrors.phase2_prompt_a} />
          </div>
          <div>
            <dt className="font-medium text-gray-500">Phase 2 prompt — Variant B</dt>
            <dd>{phase2PromptLabel(experiment.phase2_prompt_b) ?? '(none)'}</dd>
            <FieldNote message={launchFieldErrors.phase2_prompt_b} />
          </div>
          <div>
            <dt className="font-medium text-gray-500">Phase 3 prompt</dt>
            <dd>{phase3PromptLabel(experiment.phase3_prompt) ?? '(none)'}</dd>
          </div>
          <div>
            <dt className="font-medium text-gray-500">Seed systems</dt>
            <dd>{(experiment.seed_systems ?? []).map(systemTitle).join(', ') || '(none)'}</dd>
          </div>
          <div>
            <dt className="font-medium text-gray-500">Few-shots</dt>
            <dd>{(experiment.fewshots ?? []).map(fewshotName).join(', ') || '(none)'}</dd>
          </div>
          <div>
            <dt className="font-medium text-gray-500">Target system</dt>
            <dd>
              {experiment.target_system != null
                ? systemTitle(experiment.target_system)
                : '(none — generating fresh systems)'}
            </dd>
          </div>
          <div>
            <dt className="font-medium text-gray-500">Generate</dt>
            <dd>
              {[experiment.generate_systems && 'systems', experiment.generate_scenarios && 'scenarios']
                .filter(Boolean)
                .join(' + ') || 'nothing (invalid)'}
            </dd>
          </div>
          <div>
            <dt className="font-medium text-gray-500">Counts</dt>
            <dd>
              {experiment.n_systems} {experiment.n_systems === 1 ? 'system' : 'systems'} × {experiment.n_scenarios_per_system} {experiment.n_scenarios_per_system === 1 ? 'scenario' : 'scenarios'}
            </dd>
          </div>
        </dl>
      </section>

      {isStaff && (
        <section aria-labelledby="launch-heading" className="space-y-4 rounded-lg border border-gray-200 bg-white p-4">
          <h2 id="launch-heading" className="text-lg font-medium text-gray-900">
            Launch
          </h2>
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
              {/* Sourced live from `OPTIONS /api/experiments/{id}/run/`'s
                  `actions.POST.n_repetitions.help_text`, exactly as the
                  builder's own inline launch section used to read it -
                  deliberately never a hand-copied fallback string (a stale
                  copy during an outage is worse than a neutral notice). A
                  reply without the text gets that same notice, never a
                  blank line. */}
              {runOptionsQuery.isPending ? (
                <p className="mt-1 max-w-2xl text-xs text-gray-500">Loading the explanation for this field…</p>
              ) : repetitionsHelpText ? (
                <p className="mt-1 max-w-2xl text-xs text-gray-500">{repetitionsHelpText}</p>
              ) : (
                <p className="mt-1 max-w-2xl text-xs text-amber-700">
                  Could not load the explanation for this field from the server right now — this
                  does not affect the field itself, which still defaults to 1 and works normally.
                </p>
              )}
              {repetitionsInvalid && (
                <p className="mt-1 text-xs text-red-700">Enter a whole number between 1 and 100.</p>
              )}
            </div>
            <button
              type="submit"
              disabled={runExperiment.isPending || repetitionsInvalid}
              className="rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {runExperiment.isPending ? 'Launching…' : 'Launch'}
            </button>
          </form>

          {showLaunchBanner && (
            <div className="space-y-2">
              {hasNonFieldError(runExperiment.error) && <ErrorBanner error={runExperiment.error} />}
              {uncoveredLaunchFieldErrors.length > 0 && (
                <div role="alert" className="space-y-1 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                  {uncoveredLaunchFieldErrors.map(([field, message]) => (
                    <p key={field}>
                      <span className="font-semibold">{field}:</span> {message}
                    </p>
                  ))}
                </div>
              )}
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

      <section aria-labelledby="runs-heading" className="space-y-3">
        <h2 id="runs-heading" className="text-lg font-medium text-gray-900">
          Runs
        </h2>
        {runsQuery.isLoading && <p className="text-sm text-gray-500">Loading runs…</p>}
        {runsQuery.isError && <ErrorBanner error={runsQuery.error} />}
        {runsQuery.isSuccess && runs.length === 0 && (
          <p className="text-sm text-gray-500">No repetitions launched yet.</p>
        )}
        {runsQuery.isSuccess && runs.length > 0 && (
          <ul className="divide-y divide-gray-200 rounded-lg border border-gray-200 bg-white">
            {runs.map((run) => (
              <li key={run.id} className="flex items-center justify-between gap-6 px-4 py-3">
                <Link to={`/pipeline-runs/${run.id}`} className="min-w-0 flex-1 hover:underline">
                  <p className="text-sm font-medium text-gray-900">Repetition {run.repetition_index}</p>
                </Link>
                <div className="w-48 shrink-0">
                  <ProgressBar progress={run.progress} />
                </div>
                <StatusBadge status={run.status} />
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
