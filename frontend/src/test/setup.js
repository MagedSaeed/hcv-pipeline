import '@testing-library/jest-dom/vitest';
import { afterAll, afterEach, beforeAll } from 'vitest';
import { cleanup } from '@testing-library/react';
import { resetAuthState, resetFixtures, server } from './server';

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));

afterEach(() => {
  cleanup();
  server.resetHandlers();
  resetAuthState();
  resetFixtures();
  // Clears any csrftoken cookie a test left behind, so the "fetch csrf
  // exactly once" behaviour is exercised fresh in every test rather than
  // riding on a cookie a previous test happened to set.
  document.cookie = 'csrftoken=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;';
});

afterAll(() => server.close());
