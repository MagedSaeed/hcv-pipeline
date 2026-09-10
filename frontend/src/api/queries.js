import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiClient } from './client';

// -----------------------------------------------------------------------
// Cache keys - the ONE place every React Query key in this app is built.
//
// A key typed differently in two files (e.g. ['experiments', {status:'x'}]
// here and ['experiments', 'list', {status:'x'}] there) is invisible at
// review time and shows up as stale/duplicated cache entries that look
// exactly like a backend bug. `resourceKeys(name)` gives every resource the
// same four-level shape - all / lists / list(params) / detail(id) - plus
// `detailSub` for a nested sub-resource fetched off one instance (a
// taxonomy's hcvs, an experiment's progress). Every future task adds a
// resource by calling this once, rather than inventing its own shape.
//
// `list(params)`/`detailSub(id, sub, params)` take the raw params object,
// not a pre-serialized string: React Query's default query-key hasher
// (`hashKey` in @tanstack/query-core) recursively sorts plain-object keys
// before hashing, so `{a:1,b:2}` and `{b:2,a:1}` collide to the same cache
// entry on purpose, and callers never need to worry about param order.
function resourceKeys(resource) {
  return {
    all: [resource],
    lists: () => [resource, 'list'],
    list: (params = {}) => [resource, 'list', params],
    details: () => [resource, 'detail'],
    detail: (id) => [resource, 'detail', id],
    detailSub: (id, sub, params = {}) => [resource, 'detail', id, sub, params],
  };
}

// Every resource this API exposes (spec §9), so a later task never has to
// decide whether to add a new top-level factory here or invent a local one -
// there is always exactly one place a new key hierarchy belongs. Only the
// resources this task actually reads (experiments, pipelineRuns,
// taxonomies) have hooks below; the rest are declared now so their name and
// shape are fixed before anything imports them.
export const queryKeys = {
  experiments: resourceKeys('experiments'),
  pipelineRuns: resourceKeys('pipelineRuns'),
  phaseRuns: resourceKeys('phaseRuns'),
  calls: resourceKeys('calls'),
  taxonomies: resourceKeys('taxonomies'),
  resources: resourceKeys('resources'),
  fewshots: resourceKeys('fewshots'),
  systemDescriptions: resourceKeys('systemDescriptions'),
  scenarios: resourceKeys('scenarios'),
  prompts: resourceKeys('prompts'),
  models: resourceKeys('models'),
  labels: resourceKeys('labels'),
  // Not a list - AppSettings is a singleton row (`AppSettings.load()`), so
  // there is only ever one cache entry, never a list/detail split.
  settings: { detail: () => ['settings', 'detail'] },
};

// -----------------------------------------------------------------------
// Run status vocabulary - shared between the polling logic below and
// StatusBadge's colour map, so the set of valid statuses is spelled out
// exactly once. PipelineRun.Status and PhaseRun.Status (backend/pipeline/
// models.py) both define this exact set of six values; StatusBadge owns
// what colour/label each one gets, this module owns which ones are
// "terminal" for polling purposes.
export const RUN_STATUSES = Object.freeze({
  PENDING: 'pending',
  QUEUED: 'queued',
  RUNNING: 'running',
  COMPLETED: 'completed',
  FAILED: 'failed',
  CANCELLED: 'cancelled',
});

const TERMINAL_STATUSES = new Set([
  RUN_STATUSES.COMPLETED,
  RUN_STATUSES.FAILED,
  RUN_STATUSES.CANCELLED,
]);

export function isTerminalRunStatus(status) {
  return TERMINAL_STATUSES.has(status);
}

// How often to re-poll while at least one run is still non-terminal. Runs
// take on the order of minutes, so this is deliberately not aggressive.
export const RUN_POLL_INTERVAL_MS = 4000;

// The one place "should we still be polling?" is decided. Takes the *list*
// of runs a query just fetched (PipelineRun or PhaseRun rows, both carry
// the same six-value `status`) and returns the interval to re-poll at, or
// `false` to stop entirely - the literal fix for "a fixed interval that
// never stops will hammer the API forever on a finished experiment".
//
// `undefined`/not-yet-fetched keeps the interval alive (there is nothing
// yet to prove everything is terminal); an empty list stops it (nothing
// running means nothing to watch). Every status a real run can report
// belongs to exactly one of `RUN_STATUSES`, so `!isTerminalRunStatus(...)`
// is the entire rule - no separate "is this active" enumeration to drift
// out of sync with the terminal one.
export function runsRefetchInterval(runs) {
  if (!Array.isArray(runs)) return RUN_POLL_INTERVAL_MS;
  const anyNonTerminal = runs.some((run) => !isTerminalRunStatus(run.status));
  return anyNonTerminal ? RUN_POLL_INTERVAL_MS : false;
}

// -----------------------------------------------------------------------
// Every list endpoint in this API returns DRF's paginated envelope -
// {count, next, previous, results} - never a bare array (spec §9). This is
// the one place that gets unwrapped; every hook below returns the whole
// envelope (callers read `.results`) rather than each hook re-deciding how
// to reach into the response.
async function fetchList(url, params) {
  const { data } = await apiClient.get(url, { params });
  return data;
}

// -----------------------------------------------------------------------
// A handful of lists can genuinely outgrow one page (`PAGE_SIZE=50`) in a
// way a caller cannot safely ignore - see `useAllLabels` below for the
// concrete case. `fetchList` above deliberately stays a single fetch, same
// as every other list hook in this app (reading only the first page is the
// house style, and is fine when a screen's own scope keeps results well
// under 50). `fetchAllPages` is the one place that instead walks DRF's own
// `next` cursor to exhaustion, re-issuing each page through `apiClient`
// itself (never the raw absolute/relative URL the server handed back) so
// its baseURL and interceptors stay in the loop for every page, not only
// the first.
function nextPageNumber(next) {
  if (!next) return null;
  try {
    return new URL(next, 'http://placeholder.invalid').searchParams.get('page');
  } catch {
    return null;
  }
}

async function fetchAllPages(url, params) {
  const results = [];
  let page = null;
  for (;;) {
    const { data } = await apiClient.get(url, { params: page ? { ...params, page } : params });
    results.push(...data.results);
    page = nextPageNumber(data.next);
    if (!page) return results;
  }
}

// -----------------------------------------------------------------------
// Experiments - read: any authenticated user; create/launch: staff (spec §9).
export function useExperiments(params = {}, options = {}) {
  return useQuery({
    queryKey: queryKeys.experiments.list(params),
    queryFn: () => fetchList('/experiments/', params),
    ...options,
  });
}

export function useExperiment(id, options = {}) {
  return useQuery({
    queryKey: queryKeys.experiments.detail(id),
    queryFn: async () => {
      const { data } = await apiClient.get(`/experiments/${id}/`);
      return data;
    },
    enabled: id != null,
    ...options,
  });
}

export function useCreateExperiment() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post('/experiments/', payload);
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.experiments.all });
    },
  });
}

// `POST /api/experiments/{id}/run/` - staff; {n_repetitions} -> batch mode.
// The 202 body carries `runs` (one {id, repetition_index} per queued
// repetition - link straight to those, per task 6's own acceptance bar) and
// `detail` (pipeline/views.py's CANCEL_CAVEAT - the SAME honest caveat
// `cancel` itself repeats, spec §9: "so a frontend can surface it
// automatically the moment a batch starts"). A missing API key or an
// unreachable broker fails this with a 503 carrying a machine-readable
// `code` (`missing_api_key`/`broker_unavailable`) alongside `detail` -
// callers must branch on `error.response.data.code`, never pattern-match
// the English message (spec §9).
export function useRunExperiment() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, n_repetitions }) => {
      const { data } = await apiClient.post(`/experiments/${id}/run/`, { n_repetitions });
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.pipelineRuns.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.experiments.all });
    },
  });
}

// `OPTIONS /api/experiments/{id}/run/` - DRF's standard metadata response,
// now describing `RunRequestSerializer` (backend commit 6f8e9a0) rather than
// `ExperimentSerializer`, which is all DRF reports for every action's
// OPTIONS by default. This is the ONE HTTP-reachable source of
// `n_repetitions.help_text` (`pipeline/tests/test_experiment_api.py::
// test_options_on_run_describes_n_repetitions_help_text` pins the exact
// envelope: `{"actions": {"POST": {"n_repetitions": {"help_text": ..., ...}}}}`,
// `set(fields) == {"n_repetitions"}`) - callers read
// `data.actions.POST.n_repetitions.help_text` rather than hand-copying the
// string, so the backend's own wording and the screen can never drift.
// This is a DETAIL route (needs a real experiment id) - `enabled` must stay
// false until one exists; there is no id-less variant to fall back to.
// `staleTime: Infinity`: this is field metadata, not run state - nothing
// about it changes without a deploy, so there is nothing to ever re-fetch.
export function useRunOptions(experimentId, options = {}) {
  return useQuery({
    queryKey: queryKeys.experiments.detailSub(experimentId, 'run-options'),
    queryFn: async () => {
      const { data } = await apiClient.options(`/experiments/${experimentId}/run/`);
      return data;
    },
    enabled: experimentId != null,
    staleTime: Infinity,
    ...options,
  });
}

// -----------------------------------------------------------------------
// The OpenRouter model catalogue (llm/models_api.py) - cached an hour
// server-side. Shaped `{count, results}`, NOT the paginated envelope every
// other list endpoint returns (no `next`/`previous` - it is never paged),
// so this deliberately reads through the same `fetchList` unwrap-by-
// `.results` convention as everything else rather than inventing a second
// response shape for callers to special-case.
export function useModels(options = {}) {
  return useQuery({
    queryKey: queryKeys.models.list(),
    queryFn: () => fetchList('/models/'),
    ...options,
  });
}

// -----------------------------------------------------------------------
// Pipeline runs (repetitions) - drives the Dashboard's "recent runs" list.
// `refetchInterval` is derived from the data this same query just fetched,
// via `runsRefetchInterval` above: once every run on the page is terminal,
// React Query stops scheduling further automatic refetches on its own,
// with no separate timer to remember to clear.
export function usePipelineRuns(params = {}, options = {}) {
  return useQuery({
    queryKey: queryKeys.pipelineRuns.list(params),
    queryFn: () => fetchList('/pipeline-runs/', params),
    refetchInterval: (query) => runsRefetchInterval(query.state.data?.results),
    ...options,
  });
}

// One repetition, read alone - Run detail's top-level subject. Polling
// reuses the exact same "stop once terminal" decision every other run-status
// poll in this app uses (`runsRefetchInterval`), wrapping the single fetched
// row in a one-element array - the same trick `usePhaseRun` (task 5) already
// applies, so there is still only ONE place that decides when polling stops,
// and it drives off `status`, never off a progress number (task 7's own
// note: "note it drives off run status, not progress numbers").
export function usePipelineRun(id, options = {}) {
  return useQuery({
    queryKey: queryKeys.pipelineRuns.detail(id),
    queryFn: async () => {
      const { data } = await apiClient.get(`/pipeline-runs/${id}/`);
      return data;
    },
    enabled: id != null,
    refetchInterval: (query) => runsRefetchInterval(query.state.data ? [query.state.data] : undefined),
    ...options,
  });
}

// `POST /api/pipeline-runs/{id}/cancel/` - staff; pending/queued/running ->
// cancelled. Marks the run (and its still-open phase runs) dead so it can be
// deleted; does NOT interrupt a worker already mid-call - the response's own
// `detail` repeats that caveat (pipeline/views.py's CANCEL_CAVEAT), which
// RunDetail shows verbatim rather than writing a second copy that could
// drift from it. A terminal run is refused with a 409
// (`code: "run_not_cancellable"`).
export function useCancelPipelineRun() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id) => {
      const { data } = await apiClient.post(`/pipeline-runs/${id}/cancel/`);
      return data;
    },
    onSuccess: (_data, id) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.pipelineRuns.detail(id) });
      queryClient.invalidateQueries({ queryKey: queryKeys.pipelineRuns.lists() });
      queryClient.invalidateQueries({ queryKey: queryKeys.phaseRuns.all });
    },
  });
}

// `DELETE /api/pipeline-runs/{id}/` - staff; 409 while pending/queued/running
// (`code: "run_in_flight"`) - cancel first. Cascades to this repetition's
// phase runs, calls, labels and every system/scenario it generated (spec
// §5.2: "Deletion is per run, never per row").
export function useDeletePipelineRun() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id) => {
      await apiClient.delete(`/pipeline-runs/${id}/`);
      return id;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.pipelineRuns.all });
    },
  });
}

// `GET /api/pipeline-runs/{id}/export/?format=json|csv` - this ONE
// repetition's archival bundle. Fetched as `arraybuffer` rather than
// axios/XHR's own `responseType: 'blob'` - under msw's Node interceptors,
// requesting `blob` directly makes `@mswjs/interceptors` reconstruct the
// response through undici's own `Response`/body-extraction internals,
// which - verified directly, not just suspected - throws `TypeError:
// object.stream is not a function` regardless of what shape the mock
// handler's own body is. `arraybuffer` sidesteps that reconstruction
// entirely and is exactly as capable of carrying an opaque binary export
// (a JSON document or a zip); `downloadBlobResponse` (RunDetail.jsx) wraps
// the bytes in a `Blob` itself, client-side, only at the point they are
// actually handed to `URL.createObjectURL`. Never parsed/re-encoded either
// way - this app never constructs the download FILENAME itself; the
// response object's `headers['content-disposition']` is what the caller
// reads the server's own filename off of.
export function useExportPipelineRun() {
  return useMutation({
    mutationFn: ({ id, format }) =>
      apiClient.get(`/pipeline-runs/${id}/export/`, {
        params: { format },
        responseType: 'arraybuffer',
      }),
  });
}

// Every `PhaseRun` under one repetition (or matching any other `?pipeline_run=
// &phase=&variant=&status=&experiment=` filter, spec §9) - Run detail's
// per-phase progress list. Same "stop once every row is terminal" polling as
// every other list of runs in this app.
export function usePhaseRuns(params = {}, options = {}) {
  return useQuery({
    queryKey: queryKeys.phaseRuns.list(params),
    queryFn: () => fetchList('/phase-runs/', params),
    refetchInterval: (query) => runsRefetchInterval(query.state.data?.results),
    ...options,
  });
}

// `POST /api/phase-runs/{id}/retry/` - staff; phase 3 (labelling) only,
// re-enqueues only the scenarios whose call failed. A cancelled or
// in-flight run refuses with a 409 carrying its own `code`
// (`retry_run_cancelled`/`retry_run_in_flight`) - RunDetail shows the API's
// `detail` for that rather than inventing its own wording.
export function useRetryPhaseRun() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id) => {
      const { data } = await apiClient.post(`/phase-runs/${id}/retry/`);
      return data;
    },
    onSuccess: (_data, id) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.phaseRuns.detail(id) });
      queryClient.invalidateQueries({ queryKey: queryKeys.phaseRuns.lists() });
    },
  });
}

// -----------------------------------------------------------------------
// Taxonomy - read-only over the API (arrives by import command).
export function useTaxonomies(options = {}) {
  return useQuery({
    queryKey: queryKeys.taxonomies.list(),
    queryFn: () => fetchList('/taxonomies/'),
    ...options,
  });
}

export function useTaxonomyHcvs(taxonomyId, options = {}) {
  return useQuery({
    queryKey: queryKeys.taxonomies.detailSub(taxonomyId, 'hcvs'),
    queryFn: () => fetchList(`/taxonomies/${taxonomyId}/hcvs/`),
    enabled: taxonomyId != null,
    ...options,
  });
}

// -----------------------------------------------------------------------
// Settings - staff only (IsAdminUser). GET returns the masked key and
// which source is in effect; PATCH sets a new key. Never handles or stores
// the real value - `openrouter_key` is write_only on the backend, so a
// successful PATCH's response body never echoes back what was sent.
async function fetchSettings() {
  const { data } = await apiClient.get('/settings/');
  return data;
}

export function useSettings(options = {}) {
  return useQuery({
    queryKey: queryKeys.settings.detail(),
    queryFn: fetchSettings,
    ...options,
  });
}

export function useUpdateSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload) => {
      const { data } = await apiClient.patch('/settings/', payload);
      return data;
    },
    onSuccess: (data) => {
      queryClient.setQueryData(queryKeys.settings.detail(), data);
    },
  });
}

// -----------------------------------------------------------------------
// Resources (ResourceFile) - any authenticated user (spec §9's CRUD
// endpoints carry no staff restriction of their own). Upload goes through
// this SAME `apiClient` instance as every other request - passing a
// `FormData` body straight through, with no `Content-Type` header set by
// hand, is deliberate: axios detects `FormData` and lets the XHR adapter
// set `multipart/form-data` WITH the boundary itself. Setting the header
// manually here would fix the value without the boundary parameter the
// browser actually used to separate parts, breaking the upload silently.
// The CSRF request interceptor (client.js) doesn't care either way - it
// only ever touches `X-CSRFToken`, matching on the unsafe HTTP method, so
// CSRF still applies to this request exactly as it does to a JSON one.
export function useResources(params = {}, options = {}) {
  return useQuery({
    queryKey: queryKeys.resources.list(params),
    queryFn: () => fetchList('/resources/', params),
    ...options,
  });
}

export function useCreateResource() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (formData) => {
      const { data } = await apiClient.post('/resources/', formData);
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.resources.all });
    },
  });
}

export function useDeleteResource() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id) => {
      await apiClient.delete(`/resources/${id}/`);
      return id;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.resources.all });
    },
  });
}

// -----------------------------------------------------------------------
// Few-shots (FewShotExample) - ?phase=2|3. `resolved_input`/`resolved_output`
// (FewShotExampleSerializer) are read-only, server-computed fields: the
// text of whichever side is file-backed is read from the file at RESPONSE
// time, not stored on the row - callers must render these fields as given,
// never recompute them from `input_text`/`input_file` client-side.
export function useFewShots(params = {}, options = {}) {
  return useQuery({
    queryKey: queryKeys.fewshots.list(params),
    queryFn: () => fetchList('/fewshots/', params),
    ...options,
  });
}

export function useCreateFewShot() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post('/fewshots/', payload);
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.fewshots.all });
    },
  });
}

// -----------------------------------------------------------------------
// System descriptions and scenarios (corpus). `?origin=&variant=&run=
// &phase_run=` on both; `?system=` additionally on scenarios. `origin
// ="generated"` rows are read-only at the API (corpus.permissions.
// ReadOnlyIfGenerated) - `is_editable` is already embedded on every row so a
// page never has to re-derive that from `origin` itself.
export function useSystemDescriptions(params = {}, options = {}) {
  return useQuery({
    queryKey: queryKeys.systemDescriptions.list(params),
    queryFn: () => fetchList('/system-descriptions/', params),
    ...options,
  });
}

export function useUpdateSystemDescription() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, ...payload }) => {
      const { data } = await apiClient.patch(`/system-descriptions/${id}/`, payload);
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.systemDescriptions.all });
    },
  });
}

export function useScenarios(params = {}, options = {}) {
  return useQuery({
    queryKey: queryKeys.scenarios.list(params),
    queryFn: () => fetchList('/scenarios/', params),
    ...options,
  });
}

export function useUpdateScenario() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, ...payload }) => {
      const { data } = await apiClient.patch(`/scenarios/${id}/`, payload);
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.scenarios.all });
    },
  });
}

// -----------------------------------------------------------------------
// A single PhaseRun, read alone (not the list). Corpus is the one screen
// that needs this: a system description or scenario row never carries its
// own `generation_run`/`pipeline_run` over the API (SystemDescriptionSerializer
// and ScenarioSerializer both omit it - see corpus/serializers.py), so the
// ONLY way to tell whether a `?phase_run=` filter is currently showing
// iterate-mode scratch material is to ask the PhaseRun itself whether its
// own `pipeline_run` is null (spec §5.4: "null for ad-hoc iterate-mode
// runs"). Reading any authenticated user can do - PhaseRunViewSet is
// ReadOnlyModelViewSet plus two staff-only exceptions that don't include
// retrieve (pipeline/views.py).
//
// `refetchInterval` reuses the exact same "stop once terminal" decision
// every other run-status poll in this app uses (`runsRefetchInterval`,
// above) - wrapping the single fetched row in a one-element array is all
// that function needs, so there is still only ONE place that decides when
// polling stops. Corpus (task 4) only ever looks at rows that are already
// terminal, so this is a no-op there; the iterate panel (task 5) is what
// actually needs a phase run's status to keep moving while a worker runs
// it, without polling forever once it lands on completed/failed/cancelled.
export function usePhaseRun(id, options = {}) {
  return useQuery({
    queryKey: queryKeys.phaseRuns.detail(id),
    queryFn: async () => {
      const { data } = await apiClient.get(`/phase-runs/${id}/`);
      return data;
    },
    enabled: id != null,
    refetchInterval: (query) => runsRefetchInterval(query.state.data ? [query.state.data] : undefined),
    ...options,
  });
}

// -----------------------------------------------------------------------
// Prompt templates (prompts.PromptTemplate) - versioned, phase 2 or 3.
// Read: any authenticated user; create/update/preview: staff only
// (prompts.views.StaffWrites). `PATCH /api/prompts/{id}/` never mutates the
// addressed row - the view creates and returns the NEXT version instead
// (201, spec §5.3/§9) - so every caller of `useUpdatePromptTemplate` must
// re-point whatever it considers "the current template" at the response's
// own `id`, never assume the id it patched is still the live one.
export function usePromptTemplates(params = {}, options = {}) {
  return useQuery({
    queryKey: queryKeys.prompts.list(params),
    queryFn: () => fetchList('/prompts/', params),
    ...options,
  });
}

export function useCreatePromptTemplate() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post('/prompts/', payload);
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.prompts.all });
    },
  });
}

export function useUpdatePromptTemplate() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, ...payload }) => {
      const { data } = await apiClient.patch(`/prompts/${id}/`, payload);
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.prompts.all });
    },
  });
}

// `POST /api/prompts/{id}/preview/` (an existing template) or
// `POST /api/prompts/preview/` (no id yet, phase required in the body
// instead) renders exactly what a real run would send - the SAME renderer,
// the same isolation guards (spec §9: preview "does not diverge from what a
// real run sends"). Passing `system_prompt`/`user_prompt`/`output_schema` in
// the payload renders that DRAFT instead of the addressed row's own stored
// text, and persists nothing either way - no new version, no row - which is
// what lets the editor validate an in-progress edit before saving it rather
// than having to save first to find out it was broken (backend commit
// 65f6387, `prompts/views.py::_resolve_template`). Omitting the id routes to
// the id-less action for a template that has never been saved at all.
// A 400 here (StrictUndefined's `PromptRenderError`, or a TaxonomyLeak) is
// not a mutation failure to recover from - it is the guarantee working - so
// this deliberately has no `onError`/retry handling beyond what the caller
// does with the rejected promise.
export function usePreviewPromptTemplate() {
  return useMutation({
    mutationFn: async ({ id, ...payload }) => {
      const url = id != null ? `/prompts/${id}/preview/` : '/prompts/preview/';
      const { data } = await apiClient.post(url, payload);
      return data;
    },
  });
}

// -----------------------------------------------------------------------
// Iterate mode - `POST /api/phase-runs/` (staff only): one phase, ad-hoc,
// outside any Experiment/PipelineRun (spec §9/§5.4). Deliberately never
// archived and never in an export bundle - PromptEditor.jsx's iterate panel
// is the one place in this app that says so, every time it is shown, not
// only after a run finishes.
export function useCreatePhaseRun() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post('/phase-runs/', payload);
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.phaseRuns.all });
    },
  });
}

// Every LLMCall archived under one phase run, in full (LLMCallSerializer -
// rendered_system_prompt/rendered_user_prompt/raw_response, untruncated).
// Paginated like every other list endpoint; callers read `.results`.
export function usePhaseRunCalls(phaseRunId, options = {}) {
  return useQuery({
    queryKey: queryKeys.phaseRuns.detailSub(phaseRunId, 'calls'),
    queryFn: () => fetchList(`/phase-runs/${phaseRunId}/calls/`),
    enabled: phaseRunId != null,
    ...options,
  });
}

// -----------------------------------------------------------------------
// Labels (pipeline.Label) - `?scenario=&phase_run=&hcv=&source=&run=
// &variant=`. `run`/`variant` reach the label through the labelling
// PhaseRun that produced it (`phase_run__pipeline_run_id`/`phase_run__
// variant` - pipeline/views.py::LabelViewSet.get_queryset): Label itself
// carries neither field, but the backend does that hop server-side now, so
// a caller no longer has to resolve a PhaseRun id first and fan out one
// `?phase_run=` request per id the way the Labels screen originally had
// to (see `useAllLabels` below for the hook that replaced that fan-out).
// `?run=` is the REPETITION (`PipelineRun`) - an ad-hoc iterate-mode
// PhaseRun (`pipeline_run` null) is deliberately unreachable through it;
// `?phase_run=` still reaches it directly.
//
// `useLabels` is the plain single-PAGE hook every other resource gets -
// correct for a caller whose own scope (e.g. one known `phase_run`, or
// `source=` narrowing further) safely stays under `PAGE_SIZE` (50).
export function useLabels(params = {}, options = {}) {
  return useQuery({
    queryKey: queryKeys.labels.list(params),
    queryFn: () => fetchList('/labels/', params),
    ...options,
  });
}

// The Labels screen renders one scenario x HCV matrix PER VARIANT, each
// scoped to one repetition via `?run=<id>&variant=<A|B>` directly on
// `/api/labels/` - no more resolving a PhaseRun id through `/api/phase-runs/`
// first. But a single repetition's phase-3 labelling run can produce far
// more than one page of labels (at the app's own defaults, 3 systems x 5
// scenarios x 21 HCVs = 315 - more than six pages of PAGE_SIZE=50), so
// reading only `useLabels`'s first page here would silently render a "no
// label" marker for cells that WERE labelled past row 50 - exactly the
// false negative this screen must never produce. `useAllLabels` walks
// every page via `fetchAllPages` and returns the complete result set for
// these params, not just what fit on page one.
export function useAllLabels(params = {}, options = {}) {
  return useQuery({
    queryKey: queryKeys.labels.list(params),
    queryFn: async () => ({ results: await fetchAllPages('/labels/', params) }),
    ...options,
  });
}
