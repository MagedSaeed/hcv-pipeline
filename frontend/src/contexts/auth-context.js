import { createContext, useContext } from 'react';

// Split out of AuthContext.jsx so that file can export only the
// AuthProvider component - mixing a component export with plain
// value/hook exports in one file defeats React Fast Refresh (oxlint's
// react/only-export-components rule flags exactly this).
export const AUTH_QUERY_KEY = ['auth', 'me'];

export const AuthContext = createContext(undefined);

export function useAuth() {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}
