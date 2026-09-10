import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { NOT_AUTHENTICATED_BODY, PERMISSION_DENIED_BODY, paginate, requireCsrf } from './mockApi';

// Response shapes here mirror backend/core/serializers.py's UserSerializer
// (id, email, name, is_staff, must_change_password) and the actual status
// codes/bodies backend/core/views.py produces - not invented shapes. In
// particular:
//   - an unauthenticated request gets 403 (not 401): DRF's
//     SessionAuthentication has no WWW-Authenticate challenge, so
//     rest_framework coerces NotAuthenticated's usual 401 down to 403. See
//     backend/core/tests/test_auth_api.py::test_me_requires_authentication.
//   - a bad login is a whole-serializer ValidationError, which DRF reports
//     as {"non_field_errors": [...]}, per LoginSerializer.validate() in
//     backend/core/serializers.py.
// NOT_AUTHENTICATED_BODY/PERMISSION_DENIED_BODY now live in ./mockApi (the
// one place every test file's CSRF/staff mock guards read them from too,
// so this file and every screen test always agree on the exact wording);
// re-exported here unchanged so anything already importing them from
// `test/server` keeps working.
export { NOT_AUTHENTICATED_BODY };

const SEED_USERS = {
  'researcher@example.com': {
    id: 1,
    email: 'researcher@example.com',
    name: 'Researcher',
    is_staff: false,
    must_change_password: false,
    password: 'correct-horse',
  },
  'newhire@example.com': {
    id: 2,
    email: 'newhire@example.com',
    name: '',
    is_staff: false,
    must_change_password: true,
    password: 'temp-password',
  },
  'admin@example.com': {
    id: 3,
    email: 'admin@example.com',
    name: 'Admin',
    is_staff: true,
    must_change_password: false,
    password: 'admin-pass',
  },
};

function cloneSeedUsers() {
  return Object.fromEntries(Object.entries(SEED_USERS).map(([email, user]) => [email, { ...user }]));
}

let users = cloneSeedUsers();

// A tiny in-memory stand-in for Django's session, mutable so a test can log
// in, act, and log out and see the next request reflect it - the same
// sequencing a real session cookie would produce.
export const authState = { currentUserEmail: null };

export function resetAuthState() {
  users = cloneSeedUsers();
  authState.currentUserEmail = null;
}

function toUserResponse(user) {
  const { password: _password, ...rest } = user;
  return rest;
}

// Exported so a screen test's OWN write handlers (Corpus.test.jsx,
// PromptEditor.test.jsx, etc.) can build an accurate `requireStaff`/
// `requireAuth` guard (test/mockApi.js) off the CURRENT logged-in user's
// real `is_staff`, rather than each file hardcoding "admin@example.com is
// staff" as a second, separately-maintained fact.
export function currentUser() {
  return authState.currentUserEmail ? users[authState.currentUserEmail] : null;
}

// Every default list handler below (taxonomies, experiments, pipeline
// runs) builds its response through `paginate` (./mockApi) - DRF's real
// PageNumberPagination shape, honouring `?page=`. These fixtures are small
// today (they stay under PAGE_SIZE=50), so `paginate` returns the same
// single-page envelope a hand-rolled `{count, next: null, previous: null,
// results}` would - but using the real implementation here too means this
// default handler stays correct even if a fixture ever grows past one
// page, rather than only the bespoke handlers a screen test installs via
// `server.use()` (see e.g. Corpus.test.jsx/Labels.test.jsx) being trusted
// to get pagination right.

// ---------------------------------------------------------------------
// AppSettings (backend/core/models.py / core/keys.py). Mirrors
// AppSettingsSerializer: `openrouter_key` is write_only (a PATCH never
// echoes it back), `openrouter_key_masked` is `core.keys.mask_key` - first
// and last 4 characters, "..." between, or 8 asterisks for anything
// shorter - and `key_source` is exactly "settings" | "environment" |
// "none", matching `core.keys.key_source()`'s resolution order (the
// stored key wins over the environment variable).
function maskKey(value) {
  const trimmed = (value || '').trim();
  if (!trimmed) return '';
  if (trimmed.length <= 8) return '*'.repeat(8);
  return `${trimmed.slice(0, 4)}...${trimmed.slice(-4)}`;
}

const APP_SETTINGS_SEED = {
  storedKey: '',
  environmentConfigured: false,
  updatedAt: '2026-09-01T00:00:00Z',
};

let appSettings = { ...APP_SETTINGS_SEED };

function appSettingsKeySource() {
  if (appSettings.storedKey) return 'settings';
  if (appSettings.environmentConfigured) return 'environment';
  return 'none';
}

function appSettingsResponse() {
  return {
    openrouter_key_masked: maskKey(appSettings.storedKey),
    key_source: appSettingsKeySource(),
    updated_at: appSettings.updatedAt,
  };
}

// ---------------------------------------------------------------------
// Taxonomy (backend/taxonomy/serializers.py). A real HCV-21 import has 21
// rows per taxonomy version; the fixture below matches that count so a
// test can assert nothing arbitrarily truncates the list.
function buildHcvs(taxonomyId, count = 21) {
  return Array.from({ length: count }, (_, index) => ({
    id: taxonomyId * 100 + index + 1,
    hcv_id: `HCV-${String(index + 1).padStart(2, '0')}`,
    name: `Vulnerability condition ${index + 1}`,
    definition: `Definition text for HCV ${index + 1}.`,
    condition_classes: ['physical'],
    order: index + 1,
  }));
}

const TAXONOMIES_SEED = [
  {
    id: 1,
    vocabulary_id: 'hcv-21',
    version: '1',
    notes: '',
    is_active: false,
    hcv_count: 21,
    created_at: '2026-01-01T00:00:00Z',
  },
  {
    id: 2,
    vocabulary_id: 'hcv-21',
    version: '2',
    notes: 'Current release',
    is_active: true,
    hcv_count: 21,
    created_at: '2026-02-01T00:00:00Z',
  },
];

const HCVS_BY_TAXONOMY = {
  1: buildHcvs(1),
  2: buildHcvs(2),
};

// ---------------------------------------------------------------------
// Pipeline (backend/pipeline/serializers.py). Deliberately an ALL-TERMINAL
// baseline: Login/client tests that incidentally render past Dashboard
// must not pick up a live poll loop they never asked for. A test that
// specifically exercises "runs while active, stops once terminal" installs
// its own handler via `server.use()` instead of relying on this default.
const EXPERIMENTS_SEED = [
  {
    id: 1,
    name: 'Baseline HCV-21 comparison',
    description: 'Variant A vs B over the default seed corpus.',
    taxonomy: 2,
    phase2_model: 'openai/gpt-4o-mini',
    phase2_temperature: 0.7,
    phase2_prompt_a: 1,
    phase2_prompt_b: 2,
    phase3_model: 'openai/gpt-4o-mini',
    phase3_temperature: 0,
    phase3_prompt: 3,
    seed_systems: [],
    fewshots: [],
    generate_systems: true,
    generate_scenarios: true,
    target_system: null,
    n_systems: 3,
    n_scenarios_per_system: 5,
    variants: ['A', 'B'],
    progress: { done: 32, total: 32, failed: 0, percent: 100 },
    run_count: 1,
    created_by: 3,
    created_at: '2026-03-01T00:00:00Z',
  },
];

const PIPELINE_RUNS_SEED = [
  {
    id: 1,
    experiment: 1,
    repetition_index: 1,
    status: 'completed',
    config: {},
    progress: { done: 32, total: 32, failed: 0, percent: 100 },
    started_at: '2026-03-01T00:01:00Z',
    finished_at: '2026-03-01T00:24:00Z',
  },
];

let experiments = EXPERIMENTS_SEED.map((experiment) => ({ ...experiment, progress: { ...experiment.progress } }));
let pipelineRuns = PIPELINE_RUNS_SEED.map((run) => ({ ...run, progress: { ...run.progress } }));

// Resets everything this file added beyond auth (AppSettings, experiments,
// pipeline runs) back to its seed state. Auth itself stays under
// `resetAuthState` above, unchanged from task 1, since nothing here needs
// to touch it.
export function resetFixtures() {
  appSettings = { ...APP_SETTINGS_SEED };
  experiments = EXPERIMENTS_SEED.map((experiment) => ({ ...experiment, progress: { ...experiment.progress } }));
  pipelineRuns = PIPELINE_RUNS_SEED.map((run) => ({ ...run, progress: { ...run.progress } }));
}

export const handlers = [
  http.get('/api/auth/csrf/', () => {
    // A real Set-Cookie response header isn't reliably picked up by
    // document.cookie through msw's Node interceptors under jsdom, so this
    // stands in for the browser applying it. The client only cares that
    // the cookie exists afterward, which is exactly what this guarantees.
    document.cookie = 'csrftoken=test-csrf-token; path=/';
    return HttpResponse.json({ detail: 'CSRF cookie set.' });
  }),

  http.post('/api/auth/login/', async ({ request }) => {
    const body = await request.json();
    const user = users[body.email];
    if (!user || user.password !== body.password) {
      return HttpResponse.json({ non_field_errors: ['Invalid email or password.'] }, { status: 400 });
    }
    authState.currentUserEmail = user.email;
    // Django's login() rotates the CSRF token (see the backend test cited
    // above); simulate that rotation too, so a test can catch a client
    // that wrongly cached the pre-login token string.
    document.cookie = 'csrftoken=rotated-csrf-token; path=/';
    return HttpResponse.json(toUserResponse(user));
  }),

  http.get('/api/auth/me/', () => {
    const user = currentUser();
    if (!user) {
      return HttpResponse.json(NOT_AUTHENTICATED_BODY, { status: 403 });
    }
    return HttpResponse.json(toUserResponse(user));
  }),

  http.post('/api/auth/logout/', ({ request }) => {
    if (!currentUser()) {
      return HttpResponse.json(NOT_AUTHENTICATED_BODY, { status: 403 });
    }
    // Logout is the exact case the real backend test cited above (spec:
    // "an unsafe request on a real session needs a CSRF token") exercises -
    // authenticated, unsafe, and CSRF-checked.
    const csrfRefusal = requireCsrf(request);
    if (csrfRefusal) return csrfRefusal;
    authState.currentUserEmail = null;
    return new HttpResponse(null, { status: 204 });
  }),

  http.post('/api/auth/change-password/', async ({ request }) => {
    const user = currentUser();
    if (!user) {
      return HttpResponse.json(NOT_AUTHENTICATED_BODY, { status: 403 });
    }
    const csrfRefusal = requireCsrf(request);
    if (csrfRefusal) return csrfRefusal;
    const body = await request.json();
    if (body.current_password !== user.password) {
      return HttpResponse.json({ current_password: ['Current password is incorrect.'] }, { status: 400 });
    }
    user.password = body.new_password;
    user.must_change_password = false;
    return HttpResponse.json(toUserResponse(user));
  }),

  // Staff only (IsAdminUser) - GET/PATCH backend/core/views.AppSettingsView.
  http.get('/api/settings/', () => {
    const user = currentUser();
    if (!user) return HttpResponse.json(NOT_AUTHENTICATED_BODY, { status: 403 });
    if (!user.is_staff) return HttpResponse.json(PERMISSION_DENIED_BODY, { status: 403 });
    return HttpResponse.json(appSettingsResponse());
  }),

  http.patch('/api/settings/', async ({ request }) => {
    const user = currentUser();
    if (!user) return HttpResponse.json(NOT_AUTHENTICATED_BODY, { status: 403 });
    if (!user.is_staff) return HttpResponse.json(PERMISSION_DENIED_BODY, { status: 403 });
    const csrfRefusal = requireCsrf(request);
    if (csrfRefusal) return csrfRefusal;
    const body = await request.json();
    if (typeof body.openrouter_key === 'string') {
      appSettings.storedKey = body.openrouter_key;
    }
    appSettings.updatedAt = new Date().toISOString();
    return HttpResponse.json(appSettingsResponse());
  }),

  // Read: any authenticated user (backend/taxonomy/views.py's
  // TaxonomyViewSet is a ReadOnlyModelViewSet, no extra permission class).
  // Built through `paginate` (./mockApi), not the single-page `paginated`:
  // these fixtures are small today, but a caller (Labels.jsx,
  // PromptEditor.jsx) that ever paged past one page must see this handler
  // actually honour `?page=`, the same as every other list endpoint here.
  http.get('/api/taxonomies/', ({ request }) => {
    const user = currentUser();
    if (!user) return HttpResponse.json(NOT_AUTHENTICATED_BODY, { status: 403 });
    return HttpResponse.json(paginate(TAXONOMIES_SEED, request));
  }),

  http.get('/api/taxonomies/:id/hcvs/', ({ request, params }) => {
    const user = currentUser();
    if (!user) return HttpResponse.json(NOT_AUTHENTICATED_BODY, { status: 403 });
    const hcvs = HCVS_BY_TAXONOMY[Number(params.id)] ?? [];
    return HttpResponse.json(paginate(hcvs, request));
  }),

  // Read: any authenticated user (backend/pipeline/views.py's
  // ExperimentViewSet/PipelineRunViewSet use StaffWrites - reads are open).
  http.get('/api/experiments/', ({ request }) => {
    const user = currentUser();
    if (!user) return HttpResponse.json(NOT_AUTHENTICATED_BODY, { status: 403 });
    return HttpResponse.json(paginate(experiments, request));
  }),

  // usePipelineRuns (api/queries.js) now fetches every page - it backs both
  // the Dashboard's run list AND the poll-gate decision of when to stop
  // polling (task 10.1: with n_repetitions allowing up to 100, a run list
  // truncated at PAGE_SIZE=50 could report "nothing left running" while
  // repetitions 51-100 are still queued/running). `paginate` here means
  // this default handler is itself correct if a test's own fixture ever
  // grows past 50 rows, not only the bespoke ones screen tests install.
  http.get('/api/pipeline-runs/', ({ request }) => {
    const user = currentUser();
    if (!user) return HttpResponse.json(NOT_AUTHENTICATED_BODY, { status: 403 });
    return HttpResponse.json(paginate(pipelineRuns, request));
  }),
];

export const server = setupServer(...handlers);
