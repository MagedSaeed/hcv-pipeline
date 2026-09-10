import { HttpResponse } from 'msw';

// ---------------------------------------------------------------------
// Pagination. Every list endpoint in the real API returns DRF's paginated
// envelope - {count, next, previous, results}, PAGE_SIZE=50 - never a bare
// array (spec §9: "a frontend data layer needs to unwrap `results` and
// follow `next`/`previous`"). Before task 10.1, every mock list handler in
// this suite built that envelope by hand with a local `paginated()` helper
// that always set `next`/`previous` to null - a single-page response is a
// faithful shape for a small fixture, but it meant only ONE test in the
// whole suite (Labels.test.jsx's own bespoke `paginateLabels`) ever
// exercised a client actually following `next`. That is exactly how 14
// call sites that read only page one of a real list passed every test -
// the mock was more permissive than the API it stands in for (see
// api/queries.js's own comment on `fetchList`/`fetchAllPages`, and this
// project's own earlier lesson: a fake client more permissive than the real
// one hid a completely broken call path behind 760 passing tests).
//
// `paginate` below is the ONE place a test builds a REAL, multi-page
// envelope from a fixture larger than PAGE_SIZE; `paginated` is its
// single-page counterpart for a fixture that is deliberately, permanently
// small. Every list handler in this suite should build its response
// through one of these two, not invent a third shape.
export const PAGE_SIZE = 50;

// A single-page DRF envelope - `next`/`previous` both null, matching a real
// API response for a fixture that never has a second page. NOT a shortcut
// around pagination: use `paginate` instead for any fixture meant to prove
// a client follows `next` to exhaustion.
export function paginated(results) {
  return { count: results.length, next: null, previous: null, results };
}

function requestUrl(requestOrUrl) {
  return requestOrUrl instanceof URL ? requestOrUrl : new URL(requestOrUrl.url);
}

function withPageParam(url, page) {
  const next = new URL(url);
  if (page == null) next.searchParams.delete('page');
  else next.searchParams.set('page', String(page));
  return next.toString();
}

// DRF's own `PageNumberPagination`, faithfully: PAGE_SIZE-row pages,
// `count` the FULL row count (never just the current page's), and
// `next`/`previous` built from the REQUEST's own URL - `replace_query_param`/
// `remove_query_param`, matching DRF's actual pagination.py - so every other
// query param a caller sent (a filter, another page-unrelated param) is
// preserved onto the next/previous page's URL exactly as the real API's
// would be. `api/queries.js`'s `fetchAllPages` only ever reads `page` back
// off these (see its own `nextPageNumber`), but building the full URL here
// anyway means a test asserting the URL *shape* itself, not only that
// pagination eventually completes, has something real to check against.
//
// `rows` is the FULL, already-filtered row set for this request - the
// mock's equivalent of a queryset after every `?filter=` has been applied -
// callers slice nothing themselves; this is the one place slicing happens.
export function paginate(rows, requestOrUrl, { pageSize = PAGE_SIZE } = {}) {
  const url = requestUrl(requestOrUrl);
  const requestedPage = Number(url.searchParams.get('page') || '1');
  const page = Number.isFinite(requestedPage) && requestedPage > 0 ? requestedPage : 1;
  const start = (page - 1) * pageSize;
  const results = rows.slice(start, start + pageSize);
  const hasNext = start + pageSize < rows.length;
  const hasPrevious = page > 1;
  return {
    count: rows.length,
    next: hasNext ? withPageParam(url, page + 1) : null,
    previous: hasPrevious ? withPageParam(url, page === 2 ? null : page - 1) : null,
    results,
  };
}

// ---------------------------------------------------------------------
// CSRF and staff-write enforcement. The real API rejects an unsafe request
// on an authenticated session with no `X-CSRFToken` (403, body containing
// "CSRF" - `core/tests/test_auth_api.py::
// test_an_unsafe_request_on_a_real_session_needs_a_csrf_token`) and returns
// 403 for a non-staff write wherever a view is staff-only (spec §9: e.g.
// prompts create/update/preview, experiment/pipeline-run/phase-run writes).
// Screen-level mocks historically enforced neither, so the UI's own CSRF
// header attachment (api/client.js's request interceptor) and staff gating
// were never actually exercised by a test that could fail if either broke.
// PLAUSIBLE per task 10.1's brief, wired in here while every list/write
// handler in the suite was already being touched.
const UNSAFE_METHODS = new Set(['post', 'put', 'patch', 'delete']);

// Task 10.6: `backend/core/exceptions.py` (the project's DRF exception
// handler) now puts a machine-readable `code` on every non-validation error
// body - `not_authenticated`/`permission_denied` for the two 403s this file
// mocks, `not_found` for a 404. Before this, these three bodies carried
// only `detail`, so nothing in this suite could actually catch a client
// that branched on DRF's English wording instead of `code` - a mock more
// permissive than the real API, the exact shape of bug this project has
// been bitten by before (see `paginate`'s own comment above). `code` is
// added here, once, so every caller of these three helpers - and every
// screen test that installs its own 403 via one of them - gets a body that
// actually matches what `api/client.js`'s `isSessionExpired` (task 10.6)
// branches on.
export const NOT_AUTHENTICATED_BODY = {
  detail: 'Authentication credentials were not provided.',
  code: 'not_authenticated',
};
export const PERMISSION_DENIED_BODY = {
  detail: 'You do not have permission to perform this action.',
  code: 'permission_denied',
};
// A CSRF failure is refused by Django's CSRF middleware, ahead of DRF's own
// permission checks, but it is still a `PermissionDenied` under the hood
// (`django.middleware.csrf` raises exactly that) - so the project exception
// handler gives it the SAME `permission_denied` code as any other
// permission refusal, not a bespoke one of its own. That is exactly the
// case `isSessionExpired` must never treat as a session ending: a stale or
// missing CSRF token is recovered by `ensureCsrfCookie` on the very next
// unsafe request, not by logging the user out.
const CSRF_FAILED_BODY = {
  detail: 'CSRF Failed: CSRF token missing or incorrect.',
  code: 'permission_denied',
};

// A 403 Response if `request` is an unsafe method with no `X-CSRFToken`
// header, else null - a handler calls this first and returns its result
// when non-null. The real client (api/client.js's request interceptor)
// always attaches this header on every unsafe request once the CSRF cookie
// exists (fetched from `/api/auth/csrf/`, mocked below in this same file's
// caller), so a genuine UI flow driven through `apiClient` never trips
// this; only a test that bypasses the real request path would.
export function requireCsrf(request) {
  if (!UNSAFE_METHODS.has(request.method.toLowerCase())) return null;
  if (request.headers.get('X-CSRFToken')) return null;
  return HttpResponse.json(CSRF_FAILED_BODY, { status: 403 });
}

// A 403 (IsAdminUser's own body, PERMISSION_DENIED_BODY, `code:
// "permission_denied"`) if `user` is missing or not staff, else null.
export function requireStaff(user) {
  if (user?.is_staff) return null;
  return HttpResponse.json(PERMISSION_DENIED_BODY, { status: 403 });
}

// A 403 (NOT_AUTHENTICATED_BODY - the real API's body for an unauthenticated
// request, per DRF coercing 401->403 under SessionAuthentication, spec §9;
// `code: "not_authenticated"`) if `user` is missing, else null.
export function requireAuth(user) {
  if (user) return null;
  return HttpResponse.json(NOT_AUTHENTICATED_BODY, { status: 403 });
}
