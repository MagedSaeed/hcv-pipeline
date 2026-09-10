import { beforeEach, describe, expect, it } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { authState, server } from '../../test/server';
import { renderApp } from '../../test/renderApp';

beforeEach(() => {
  authState.currentUserEmail = null;
});

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
      http.patch('/api/settings/', async ({ request }) => {
        const body = await request.json();
        receivedKey = body.openrouter_key;
        return HttpResponse.json({
          openrouter_key_masked: 'sk-o...9999',
          key_source: 'settings',
          updated_at: '2026-09-05T00:00:00Z',
        });
      })
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
      http.patch('/api/settings/', () =>
        HttpResponse.json({ openrouter_key: ['This field may not be blank.'] }, { status: 400 })
      )
    );

    const user = userEvent.setup();
    renderApp({ initialEntries: ['/settings'] });

    await screen.findByText(/not configured/i);
    await user.type(screen.getByLabelText(/set a new key/i), 'x');
    await user.click(screen.getByRole('button', { name: /save key/i }));

    expect(await screen.findByRole('alert')).toHaveTextContent('This field may not be blank.');
  });
});
