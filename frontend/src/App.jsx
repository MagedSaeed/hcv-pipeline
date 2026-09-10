import { useEffect } from 'react';
import { Navigate, Route, Routes, useNavigate } from 'react-router-dom';
import ProtectedRoute from './components/ProtectedRoute';
import Layout from './components/Layout';
import Login from './pages/Login';
import ChangePassword from './pages/ChangePassword';
import Dashboard from './pages/Dashboard';
import Resources from './pages/Resources';
import Taxonomy from './pages/Taxonomy';
import Corpus from './pages/Corpus';
import PromptEditor from './pages/PromptEditor';
import ExperimentBuilder from './pages/ExperimentBuilder';
import RunDetail from './pages/RunDetail';
import Settings from './pages/Settings';

// The axios response interceptor (src/api/client.js) has no Router context
// of its own - it is plain JS, not a component - so on a genuine
// mid-session auth failure it clears the query cache and dispatches this
// DOM event instead of navigating directly. This component, mounted inside
// the Router, is the other half: it turns that event into an actual
// SPA-internal redirect to Login.
function UnauthorizedListener() {
  const navigate = useNavigate();

  useEffect(() => {
    function handleUnauthorized() {
      navigate('/login', { replace: true });
    }
    window.addEventListener('auth:unauthorized', handleUnauthorized);
    return () => window.removeEventListener('auth:unauthorized', handleUnauthorized);
  }, [navigate]);

  return null;
}

export default function App() {
  return (
    <>
      <UnauthorizedListener />
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route
          path="/change-password"
          element={
            <ProtectedRoute>
              <ChangePassword />
            </ProtectedRoute>
          }
        />
        <Route
          element={
            <ProtectedRoute>
              <Layout />
            </ProtectedRoute>
          }
        >
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/resources" element={<Resources />} />
          <Route path="/taxonomy" element={<Taxonomy />} />
          <Route path="/corpus" element={<Corpus />} />
          <Route path="/prompts" element={<PromptEditor />} />
          <Route path="/experiments/new" element={<ExperimentBuilder />} />
          <Route path="/pipeline-runs/:id" element={<RunDetail />} />
          <Route path="/settings" element={<Settings />} />
        </Route>
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Routes>
    </>
  );
}
