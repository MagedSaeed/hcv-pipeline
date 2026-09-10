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
import ExperimentDetail from './pages/ExperimentDetail';
import RunDetail from './pages/RunDetail';
import Labels from './pages/Labels';
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
          {/* task 10.3: an experiment is never stranded - it always has a
              route back (`/experiments/:id`, linked from the Dashboard and
              landed on straight after create), and an edit route that
              reuses the SAME builder form via PATCH. Order matters here:
              react-router matches `/experiments/:id/edit` against the more
              specific route below before falling through to the `:id` one,
              so "edit" is never swallowed as if it were an experiment id. */}
          <Route path="/experiments/:id/edit" element={<ExperimentBuilder />} />
          <Route path="/experiments/:id" element={<ExperimentDetail />} />
          <Route path="/pipeline-runs/:id" element={<RunDetail />} />
          <Route path="/labels" element={<Labels />} />
          <Route path="/settings" element={<Settings />} />
        </Route>
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Routes>
    </>
  );
}
