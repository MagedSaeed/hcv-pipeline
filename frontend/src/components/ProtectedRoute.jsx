import { Navigate, useLocation } from 'react-router-dom';
import { useAuth } from '../contexts/auth-context';

// Guards a route on two things: is anyone signed in, and if so, have they
// finished the forced first-login password change.
//
// The one rule that matters here: never decide anything while `isLoading`
// is true. On a hard page refresh the React Query cache starts empty, so
// `isLoading` is briefly true even for a user whose session cookie is
// perfectly valid. Redirecting to Login during that window - instead of
// waiting for the `/auth/me/` check to settle - logs a still-authenticated
// user out on every single refresh. That bug is easy to introduce and easy
// to miss, because it only shows up on a real reload, never on client-side
// navigation where the query is already cached.
export default function ProtectedRoute({ children }) {
  const { user, isLoading } = useAuth();
  const location = useLocation();

  if (isLoading) {
    return null;
  }

  if (!user) {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }

  if (user.must_change_password && location.pathname !== '/change-password') {
    return <Navigate to="/change-password" replace />;
  }

  return children;
}
