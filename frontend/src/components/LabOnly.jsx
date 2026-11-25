import { Navigate, Outlet } from 'react-router-dom';
import { useAuth } from '../contexts/auth-context';

// The Experiment Lab's screens; analyst accounts are sent to Analyses instead.
export default function LabOnly() {
  const { isAnalyst } = useAuth();
  return isAnalyst ? <Navigate to="/analyses" replace /> : <Outlet />;
}
