import { describe, expect, it } from 'vitest';
import { http, HttpResponse } from 'msw';
import { QueryClient } from '@tanstack/react-query';
import { authState, server } from '../../test/server';
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

describe('apiClient auth-failure handling', () => {
  it('clears the query cache and dispatches auth:unauthorized on a genuine auth failure, without retrying', async () => {
    let attempts = 0;
    server.use(
      http.get('/api/settings/', () => {
        attempts += 1;
        return HttpResponse.json({ detail: 'Authentication credentials were not provided.' }, { status: 403 });
      })
    );

    const queryClient = new QueryClient();
    queryClient.setQueryData(['some', 'cached', 'thing'], { ok: true });
    setQueryClient(queryClient);

    const events = [];
    const handler = () => events.push('unauthorized');
    window.addEventListener('auth:unauthorized', handler);

    try {
      await expect(apiClient.get('/settings/')).rejects.toBeTruthy();
    } finally {
      window.removeEventListener('auth:unauthorized', handler);
    }

    expect(attempts).toBe(1);
    expect(queryClient.getQueryData(['some', 'cached', 'thing'])).toBeUndefined();
    expect(events).toEqual(['unauthorized']);
  });

  it('does not treat the auth self-check endpoints as a global unauthorized event', async () => {
    const queryClient = new QueryClient();
    queryClient.setQueryData(['some', 'cached', 'thing'], { ok: true });
    setQueryClient(queryClient);

    const events = [];
    const handler = () => events.push('unauthorized');
    window.addEventListener('auth:unauthorized', handler);

    try {
      await expect(apiClient.get('/auth/me/')).rejects.toBeTruthy();
    } finally {
      window.removeEventListener('auth:unauthorized', handler);
    }

    expect(events).toEqual([]);
    expect(queryClient.getQueryData(['some', 'cached', 'thing'])).toEqual({ ok: true });
  });

  it('leaves a non-auth 403 (real permission denial) alone', async () => {
    // A logged-in but non-staff user hitting /api/settings/ is a genuine
    // permission error, not a "you got logged out" signal, and must not be
    // treated as one - the message differs from the auth-failure detail.
    server.use(
      http.get('/api/settings/', () =>
        HttpResponse.json({ detail: 'You do not have permission to perform this action.' }, { status: 403 })
      )
    );

    const queryClient = new QueryClient();
    queryClient.setQueryData(['some', 'cached', 'thing'], { ok: true });
    setQueryClient(queryClient);

    const events = [];
    const handler = () => events.push('unauthorized');
    window.addEventListener('auth:unauthorized', handler);

    try {
      await expect(apiClient.get('/settings/')).rejects.toBeTruthy();
    } finally {
      window.removeEventListener('auth:unauthorized', handler);
    }

    expect(events).toEqual([]);
    expect(queryClient.getQueryData(['some', 'cached', 'thing'])).toEqual({ ok: true });
  });
});
