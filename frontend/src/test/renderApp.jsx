import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render } from '@testing-library/react';
import App from '../App';
import { AuthProvider } from '../contexts/AuthContext';
import { setQueryClient } from '../api/client';

// Shared render helper for anything exercising routing/auth end-to-end.
// Every call gets its own QueryClient (no state leaking between tests) and
// registers it with client.js, exactly as main.jsx does for the real app.
export function renderApp({ initialEntries = ['/dashboard'] } = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  setQueryClient(queryClient);

  const utils = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={initialEntries}>
        <AuthProvider>
          <App />
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>
  );

  return { ...utils, queryClient };
}
