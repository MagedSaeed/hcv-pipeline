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
// Experiments - read: any authenticated user (spec §9).
export function useExperiments(params = {}, options = {}) {
  return useQuery({
    queryKey: queryKeys.experiments.list(params),
    queryFn: () => fetchList('/experiments/', params),
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
export function usePhaseRun(id, options = {}) {
  return useQuery({
    queryKey: queryKeys.phaseRuns.detail(id),
    queryFn: async () => {
      const { data } = await apiClient.get(`/phase-runs/${id}/`);
      return data;
    },
    enabled: id != null,
    ...options,
  });
}
