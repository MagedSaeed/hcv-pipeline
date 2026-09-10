import { describe, expect, it } from 'vitest';
import { http, HttpResponse } from 'msw';
import { QueryClient } from '@tanstack/react-query';
import { authState, server } from '../../test/server';
import { NOT_AUTHENTICATED_BODY, PERMISSION_DENIED_BODY, requireAuth, requireCsrf, requireStaff } from '../../test/mockApi';
import { apiClient, setQueryClient } from '../client';

describe('apiClient CSRF handling', () => {
  it('fetches the csrf cookie once and reuses it across multiple unsafe requests', async () => {
    authState.currentUserEmail = 'researcher@example.com';
    let csrfCalls = 0;
    const tokensSeen = [];
    server.use(
      http.get('/api/auth/csrf/', () => {
        csrfCalls += 1;
        document.cookie = 'csrftoken=cached-token; path=/';
        return HttpResponse.json({ detail: 'CSRF cookie set.' });
      }),
      http.post('/api/auth/change-password/', ({ request }) => {
        tokensSeen.push(request.headers.get('X-CSRFToken'));
        return HttpResponse.json({ current_password: ['Current password is incorrect.'] }, { status: 400 });
      })
    );

    await apiClient.post('/auth/change-password/', { current_password: 'x', new_password: 'y' }).catch(() => {});
    await apiClient.post('/auth/change-password/', { current_password: 'x', new_password: 'y' }).catch(() => {});

    expect(csrfCalls).toBe(1);
    expect(tokensSeen).toEqual(['cached-token', 'cached-token']);
  });

  it('does not send X-CSRFToken on safe GET requests', async () => {
    let sawHeader;
    server.use(
      http.get('/api/auth/me/', ({ request }) => {
        sawHeader = request.headers.get('X-CSRFToken');
        return HttpResponse.json({ detail: 'Authentication credentials were not provided.' }, { status: 403 });
      })
    );

    await apiClient.get('/auth/me/').catch(() => {});

    expect(sawHeader).toBeNull();
  });
});

// Small helper shared by every test below: wires up a fresh QueryClient with
// one cached entry and an `auth:unauthorized` listener, runs `request`, and
// returns what happened - so each test states only what's DIFFERENT about
// its scenario (the mocked response), not the boilerplate around observing
// the effect.
async function observeUnauthorizedEvent(request) {
  const queryClient = new QueryClient();
  queryClient.setQueryData(['some', 'cached', 'thing'], { ok: true });
  setQueryClient(queryClient);

  const events = [];
  const handler = () => events.push('unauthorized');
  window.addEventListener('auth:unauthorized', handler);

  try {
    await expect(request()).rejects.toBeTruthy();
  } finally {
    window.removeEventListener('auth:unauthorized', handler);
  }

  return { events, cached: queryClient.getQueryData(['some', 'cached', 'thing']) };
}

describe('apiClient auth-failure handling (task 10.6: branches on `code`, not English `detail`)', () => {
  // Task 10.6: `code: "not_authenticated"` - a session that never existed or
  // has expired - must clear the cache and fire the redirect-to-Login
  // event. The `detail` string here is deliberately NOT the real backend's
  // English wording (contrast the old `NOT_AUTHENTICATED_DETAIL` constant
  // this replaced) - proof the client now reads `code`, never prose.
  it('not_authenticated: clears the query cache and dispatches auth:unauthorized, without retrying, driven by `code` alone', async () => {
    let attempts = 0;
    server.use(
      http.get('/api/settings/', () => {
        attempts += 1;
        return HttpResponse.json(
          { detail: 'Some other wording entirely - not the old hard-coded sentence.', code: 'not_authenticated' },
          { status: 403 }
        );
      })
    );

    const { events, cached } = await observeUnauthorizedEvent(() => apiClient.get('/settings/'));

    expect(attempts).toBe(1);
    expect(cached).toBeUndefined();
    expect(events).toEqual(['unauthorized']);
  });

  it('does not treat the auth self-check endpoints as a global unauthorized event', async () => {
    const { events, cached } = await observeUnauthorizedEvent(() => apiClient.get('/auth/me/'));

    expect(events).toEqual([]);
    expect(cached).toEqual({ ok: true });
  });

  // Task 10.6's own acceptance bar: a `permission_denied` 403 - a logged-in
  // user hitting a control they don't have rights to (e.g. a non-staff user
  // on a staff-only endpoint) - must NEVER be treated as a session ending.
  // Logging someone out because they clicked something staff-only would be
  // a real regression, not a safety net.
  it('permission_denied: never logs the user out, and leaves the cache untouched', async () => {
    server.use(
      http.get('/api/settings/', () =>
        HttpResponse.json(
          { detail: 'You do not have permission to perform this action.', code: 'permission_denied' },
          { status: 403 }
        )
      )
    );

    const { events, cached } = await observeUnauthorizedEvent(() => apiClient.get('/settings/'));

    expect(events).toEqual([]);
    expect(cached).toEqual({ ok: true });
  });

  // The other half of task 10.6's acceptance bar: a CSRF failure under DRF's
  // SessionAuthentication is ALSO `permission_denied` (core/exceptions.py -
  // Django's CSRF middleware raises PermissionDenied under the hood), and
  // must be just as recoverable as any other permission_denied - a stale or
  // missing token is fixed by `ensureCsrfCookie` on the very next unsafe
  // request, not by logging the user out mid-session.
  it('permission_denied from a CSRF failure never logs the user out either', async () => {
    server.use(
      http.get('/api/settings/', () =>
        HttpResponse.json(
          { detail: 'CSRF Failed: CSRF token missing or incorrect.', code: 'permission_denied' },
          { status: 403 }
        )
      )
    );

    const { events, cached } = await observeUnauthorizedEvent(() => apiClient.get('/settings/'));

    expect(events).toEqual([]);
    expect(cached).toEqual({ ok: true });
  });

  // A body with NO `code` at all (e.g. an older/unshaped error, or a 403
  // this project's exception handler didn't touch) must default to "not a
  // session ending" too - the safe direction to fail in is staying logged
  // in and showing the error, never a surprise logout off an ambiguous body.
  it('a 403 with no `code` at all is not treated as a session ending', async () => {
    server.use(
      http.get('/api/settings/', () => HttpResponse.json({ detail: 'Some unshaped 403 body.' }, { status: 403 }))
    );

    const { events, cached } = await observeUnauthorizedEvent(() => apiClient.get('/settings/'));

    expect(events).toEqual([]);
    expect(cached).toEqual({ ok: true });
  });
});

// Task 10.6's own instruction: "make the mocks tell the truth about this" -
// every test above proves the CLIENT branches correctly on a `code` it is
// handed, but none of them exercise the shared mock guards
// (test/mockApi.js) that every OTHER test file's 403s actually go through.
// A mock that stopped attaching `code` would still pass every test above
// (they build their own response bodies by hand) - these two close that
// gap: the first asserts the guard helpers' own bodies directly, the
// second drives a real request through `requireAuth` end to end, the same
// way `server.js`'s default `/api/auth/me/` handler and most screen tests'
// own write guards actually do.
describe('the shared mock guards carry the same `code`s the real handler does (task 10.6)', () => {
  it('requireAuth/requireStaff/requireCsrf bodies carry the real handler\'s own codes', async () => {
    const authBody = await requireAuth(null).json();
    expect(authBody).toEqual(NOT_AUTHENTICATED_BODY);
    expect(authBody.code).toBe('not_authenticated');

    const staffBody = await requireStaff({ is_staff: false }).json();
    expect(staffBody).toEqual(PERMISSION_DENIED_BODY);
    expect(staffBody.code).toBe('permission_denied');

    const csrfRequest = new Request('http://localhost/api/settings/', { method: 'PATCH' });
    const csrfBody = await requireCsrf(csrfRequest).json();
    expect(csrfBody.code).toBe('permission_denied');

    // The two guards that pass (nothing to refuse) return null, not a body.
    expect(requireAuth({ id: 1 })).toBeNull();
    expect(requireStaff({ is_staff: true })).toBeNull();
    const safeRequest = new Request('http://localhost/api/settings/', {
      method: 'PATCH',
      headers: { 'X-CSRFToken': 'token' },
    });
    expect(requireCsrf(safeRequest)).toBeNull();
  });

  it('a 403 built through requireAuth (as server.js\'s own /auth/me/ default handler builds it) still ends the session end to end', async () => {
    server.use(http.get('/api/settings/', () => requireAuth(null)));

    const { events, cached } = await observeUnauthorizedEvent(() => apiClient.get('/settings/'));

    expect(events).toEqual(['unauthorized']);
    expect(cached).toBeUndefined();
  });
});
