import axios from 'axios';
import { beforeEach, describe, expect, it } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { authState, currentUser, server } from '../../test/server';
import { renderApp } from '../../test/renderApp';
import { requireCsrf, requireStaff } from '../../test/mockApi';

beforeEach(() => {
  authState.currentUserEmail = null;
});

// Coordinator review (final polish, item 4): none of this file's five
// PATCH handlers enforced staff/CSRF before now - `AppSettingsView` is
// `IsAdminUser` AND, like every other unsafe method in this app, CSRF-
// checked, but every test here installed its own bare resolver with
// neither guard. A regression that stopped sending `X-CSRFToken` on a
// settings PATCH (or that let a non-staff session reach this view) would
// still have passed all 5. Matches `ExperimentBuilder.test.jsx`'s
// `guardedExperimentWrite`/`ExperimentDetail.test.jsx`'s `guardedRunWrite` -
// each test installs its OWN resolver (the response body differs per
// test), so the guard wraps each one rather than living in a separate
// layered handler an msw request would never actually reach.
function guardedSettingsWrite(resolver) {
  return async (info) => {
    const staffRefusal = requireStaff(currentUser());
    if (staffRefusal) return staffRefusal;
    const csrfRefusal = requireCsrf(info.request);
    if (csrfRefusal) return csrfRefusal;
    return resolver(info);
  };
}

describe('Settings', () => {
  it('shows the masked key and which source is in effect for a staff user', async () => {
    authState.currentUserEmail = 'admin@example.com';
    server.use(
      http.get('/api/settings/', () =>
        HttpResponse.json({
          openrouter_key_masked: 'sk-o...ab12',
          key_source: 'environment',
          updated_at: '2026-09-01T00:00:00Z',
        })
      )
    );

    renderApp({ initialEntries: ['/settings'] });

    expect(await screen.findByText('sk-o...ab12')).toBeInTheDocument();
    expect(screen.getByText(/OPENROUTER_API_KEY environment variable/i)).toBeInTheDocument();
    // Never renders anything that looks like a real, unmasked key.
    expect(screen.queryByText(/sk-or-[a-z0-9]{15,}/i)).not.toBeInTheDocument();
  });

  it('lets a staff user submit a new key, which the response never echoes back', async () => {
    authState.currentUserEmail = 'admin@example.com';
    let receivedKey = null;
    server.use(
      http.get('/api/settings/', () =>
        HttpResponse.json({ openrouter_key_masked: '', key_source: 'none', updated_at: '2026-09-01T00:00:00Z' })
      ),
      http.patch(
        '/api/settings/',
        guardedSettingsWrite(async ({ request }) => {
          const body = await request.json();
          receivedKey = body.openrouter_key;
          return HttpResponse.json({
            openrouter_key_masked: 'sk-o...9999',
            key_source: 'settings',
            updated_at: '2026-09-05T00:00:00Z',
          });
        })
      )
    );

    const user = userEvent.setup();
    renderApp({ initialEntries: ['/settings'] });

    await screen.findByText(/not configured/i);
    await user.type(screen.getByLabelText(/set a new key/i), 'sk-or-realsecretvalue9999');
    await user.click(screen.getByRole('button', { name: /save key/i }));

    expect(await screen.findByText('sk-o...9999')).toBeInTheDocument();
    expect(receivedKey).toBe('sk-or-realsecretvalue9999');
    // The real value the operator typed is never rendered on the page,
    // even after a successful save.
    expect(screen.queryByText('sk-or-realsecretvalue9999')).not.toBeInTheDocument();
  });

  it('a non-staff user cannot see the form, and it is never even requested', async () => {
    authState.currentUserEmail = 'researcher@example.com';
    let settingsRequested = false;
    server.use(
      http.get('/api/settings/', () => {
        settingsRequested = true;
        return HttpResponse.json({ detail: 'You do not have permission to perform this action.' }, { status: 403 });
      })
    );

    renderApp({ initialEntries: ['/settings'] });

    expect(await screen.findByText(/only staff/i)).toBeInTheDocument();
    expect(screen.queryByLabelText(/set a new key/i)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /save key/i })).not.toBeInTheDocument();
    // No nav link to Settings either.
    expect(screen.queryByRole('link', { name: /^settings$/i })).not.toBeInTheDocument();
    expect(settingsRequested).toBe(false);
  });

  it("shows the server's own error message when saving the key fails", async () => {
    authState.currentUserEmail = 'admin@example.com';
    server.use(
      http.get('/api/settings/', () =>
        HttpResponse.json({ openrouter_key_masked: '', key_source: 'none', updated_at: '2026-09-01T00:00:00Z' })
      ),
      http.patch(
        '/api/settings/',
        guardedSettingsWrite(() =>
          HttpResponse.json({ openrouter_key: ['This field may not be blank.'] }, { status: 400 })
        )
      )
    );

    const user = userEvent.setup();
    renderApp({ initialEntries: ['/settings'] });

    await screen.findByText(/not configured/i);
    await user.type(screen.getByLabelText(/set a new key/i), 'x');
    await user.click(screen.getByRole('button', { name: /save key/i }));

    expect(await screen.findByRole('alert')).toHaveTextContent('This field may not be blank.');
  });

  // Task 10.6 / walkthrough finding: before this, Save disabled itself on
  // an empty field, so once a key was ever saved here, the UI could never
  // fall back to `.env` again - the exact trap that failed the
  // walkthrough's first iterate attempt with a 401. The API already allows
  // this (`openrouter_key` is `allow_blank=True`); the fix is this explicit,
  // confirmed action.
  describe('clearing an admin-entered key (task 10.6)', () => {
    it('offers the clear action only while a key is set on this screen, never when the key already comes from the environment', async () => {
      authState.currentUserEmail = 'admin@example.com';
      server.use(
        http.get('/api/settings/', () =>
          HttpResponse.json({ openrouter_key_masked: 'sk-o...ab12', key_source: 'environment', updated_at: '2026-09-01T00:00:00Z' })
        )
      );

      renderApp({ initialEntries: ['/settings'] });

      await screen.findByText('sk-o...ab12');
      expect(screen.queryByRole('button', { name: /clear.*environment key/i })).not.toBeInTheDocument();
    });

    it('clears the stored key behind a confirmation, sends an empty string, and shows the resulting source - never the real key', async () => {
      let receivedBody = null;
      authState.currentUserEmail = 'admin@example.com';
      server.use(
        http.get('/api/settings/', () =>
          HttpResponse.json({ openrouter_key_masked: 'sk-o...9999', key_source: 'settings', updated_at: '2026-09-01T00:00:00Z' })
        ),
        http.patch(
          '/api/settings/',
          guardedSettingsWrite(async ({ request }) => {
            receivedBody = await request.json();
            return HttpResponse.json({
              openrouter_key_masked: 'sk-e...nv01',
              key_source: 'environment',
              updated_at: '2026-09-06T00:00:00Z',
            });
          })
        )
      );

      const user = userEvent.setup();
      renderApp({ initialEntries: ['/settings'] });

      await screen.findByText('sk-o...9999');
      const clearButton = screen.getByRole('button', { name: /clear.*environment key/i });
      await user.click(clearButton);

      // Behind confirmation - the click above must not have sent anything yet.
      expect(receivedBody).toBeNull();
      const dialog = screen.getByRole('alertdialog');
      expect(dialog).toHaveTextContent(/fall back to the openrouter_api_key environment variable/i);
      await user.click(within(dialog).getByRole('button', { name: /^confirm$/i }));

      // Anchored to the start of the whole status paragraph so this can
      // only match the "Cleared. Source in effect is now: ..." note itself,
      // never the (separately rendered) "Source in effect" dt/dd pair above
      // it, which shares the same trailing wording.
      const clearedNote = await screen.findByText(/^cleared\./i);
      expect(clearedNote).toHaveTextContent(/OPENROUTER_API_KEY environment variable/i);
      // The PATCH sent the empty string the API already accepts - never a
      // distinct backend action, and never any part of a real key.
      expect(receivedBody).toEqual({ openrouter_key: '' });
      expect(await screen.findByText('sk-e...nv01')).toBeInTheDocument();
      // The real key never appears anywhere on the page.
      expect(screen.queryByText(/sk-or-[a-z0-9]{15,}/i)).not.toBeInTheDocument();
    });

    it('cancelling the confirmation clears nothing', async () => {
      let patchCalled = false;
      authState.currentUserEmail = 'admin@example.com';
      server.use(
        http.get('/api/settings/', () =>
          HttpResponse.json({ openrouter_key_masked: 'sk-o...9999', key_source: 'settings', updated_at: '2026-09-01T00:00:00Z' })
        ),
        http.patch(
          '/api/settings/',
          guardedSettingsWrite(() => {
            patchCalled = true;
            return HttpResponse.json({ openrouter_key_masked: '', key_source: 'none', updated_at: '2026-09-06T00:00:00Z' });
          })
        )
      );

      const user = userEvent.setup();
      renderApp({ initialEntries: ['/settings'] });

      await screen.findByText('sk-o...9999');
      await user.click(screen.getByRole('button', { name: /clear.*environment key/i }));
      await user.click(screen.getByRole('button', { name: /never mind/i }));

      expect(patchCalled).toBe(false);
      expect(screen.getByText('sk-o...9999')).toBeInTheDocument();
    });

    it("shows the API's own error message if clearing fails", async () => {
      authState.currentUserEmail = 'admin@example.com';
      server.use(
        http.get('/api/settings/', () =>
          HttpResponse.json({ openrouter_key_masked: 'sk-o...9999', key_source: 'settings', updated_at: '2026-09-01T00:00:00Z' })
        ),
        http.patch(
          '/api/settings/',
          guardedSettingsWrite(() =>
            HttpResponse.json({ detail: 'Something went wrong clearing the key.' }, { status: 500 })
          )
        )
      );

      const user = userEvent.setup();
      renderApp({ initialEntries: ['/settings'] });

      await screen.findByText('sk-o...9999');
      await user.click(screen.getByRole('button', { name: /clear.*environment key/i }));
      await user.click(screen.getByRole('button', { name: /^confirm$/i }));

      expect(await screen.findByRole('alert')).toHaveTextContent(/something went wrong clearing the key/i);
    });
  });

  // Coordinator review (final polish, item 4): proves the guard just wired
  // into this file's own PATCH handlers actually refuses a write with no
  // `X-CSRFToken` header - not merely present in the file, unreachable. A
  // fresh axios instance (no interceptors, unlike `apiClient`, which always
  // attaches the header correctly and so could never exercise this) talks
  // to the SAME mock server msw intercepts regardless of which client made
  // the request.
  it('the mock refuses a PATCH to /api/settings/ with no CSRF header (guard actually wired)', async () => {
    authState.currentUserEmail = 'admin@example.com';
    server.use(
      http.patch('/api/settings/', guardedSettingsWrite(() => HttpResponse.json({ openrouter_key_masked: '', key_source: 'none', updated_at: '2026-09-01T00:00:00Z' })))
    );
    const rawClient = axios.create({ baseURL: '/api', withCredentials: true });

    await expect(rawClient.patch('/settings/', { openrouter_key: 'sk-or-shouldnotwork' })).rejects.toMatchObject({
      response: { status: 403 },
    });
  });
});
