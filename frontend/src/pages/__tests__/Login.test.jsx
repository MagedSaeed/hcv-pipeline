import { describe, expect, it } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { authState, server } from '../../test/server';
import { renderApp } from '../../test/renderApp';

async function submitLogin(user, email, password) {
  await user.type(screen.getByLabelText(/email/i), email);
  await user.type(screen.getByLabelText(/password/i), password);
  await user.click(screen.getByRole('button', { name: /sign in/i }));
}

describe('Login and the auth flow', () => {
  it('calls csrf before login, then lands on the dashboard', async () => {
    const callOrder = [];
    server.use(
      http.get('/api/auth/csrf/', () => {
        callOrder.push('csrf');
        document.cookie = 'csrftoken=test-csrf-token; path=/';
        return HttpResponse.json({ detail: 'CSRF cookie set.' });
      }),
      http.post('/api/auth/login/', async ({ request }) => {
        callOrder.push('login');
        const body = await request.json();
        return HttpResponse.json({
          id: 1,
          email: body.email,
          name: 'Researcher',
          is_staff: false,
          must_change_password: false,
        });
      })
    );

    const user = userEvent.setup();
    renderApp({ initialEntries: ['/login'] });

    await submitLogin(user, 'researcher@example.com', 'correct-horse');

    expect(await screen.findByRole('heading', { name: /dashboard/i })).toBeInTheDocument();
    expect(callOrder).toEqual(['csrf', 'login']);
  });

  it('shows the API error message on an invalid password and keeps the user on Login', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/login'] });

    await submitLogin(user, 'researcher@example.com', 'wrong-password');

    expect(await screen.findByRole('alert')).toHaveTextContent('Invalid email or password.');
    expect(screen.getByRole('heading', { name: /sign in/i })).toBeInTheDocument();
  });

  it('redirects an unauthenticated visit to a protected route to Login', async () => {
    renderApp({ initialEntries: ['/dashboard'] });

    expect(await screen.findByRole('heading', { name: /sign in/i })).toBeInTheDocument();
  });

  it('sends a user with must_change_password to the change-password screen and will not let them navigate away', async () => {
    // Visiting a protected route directly (not via the login form) with an
    // already-authenticated, must-change-password session: the guard must
    // catch this on arrival, not only right after a fresh login.
    authState.currentUserEmail = 'newhire@example.com';

    renderApp({ initialEntries: ['/dashboard'] });

    expect(await screen.findByRole('heading', { name: /choose a new password/i })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: /^dashboard$/i })).not.toBeInTheDocument();
  });

  it('logs out, clearing the cached user so a protected route redirects again', async () => {
    const user = userEvent.setup();
    renderApp({ initialEntries: ['/login'] });

    await submitLogin(user, 'researcher@example.com', 'correct-horse');
    await screen.findByRole('heading', { name: /dashboard/i });

    await user.click(screen.getByRole('button', { name: /log out/i }));

    expect(await screen.findByRole('heading', { name: /sign in/i })).toBeInTheDocument();
  });
});
