import { useCallback, useRef, useState } from 'react';
import { Link, Navigate, useLocation, useNavigate, useParams } from 'react-router-dom';
import { useAnalysis } from '../../api/analyses';
import { useAuth } from '../../contexts/auth-context';
import ErrorBanner from '../../components/ErrorBanner';
import AnalysisMenu from '../../components/analyses/AnalysisMenu';
import Stepper from '../../components/analyses/Stepper';
import { STEPS } from '../../components/analyses/steps';
import ExtractStep from './ExtractStep';
import GenerateStep from './GenerateStep';
import LabelStep from './LabelStep';
import ReportStep from './ReportStep';
import SystemStep from './SystemStep';

const SCREENS = {
  system: SystemStep,
  extract: ExtractStep,
  generate: GenerateStep,
  label: LabelStep,
  report: ReportStep,
};

function reachableSteps(analysis) {
  const hasScenarios = Boolean(analysis?.scenarios.length);
  const checked = analysis?.extraction.status === 'current' && analysis.extraction.pending === 0;
  return {
    system: true,
    extract: Boolean(analysis),
    generate: Boolean(analysis) && (analysis.locked || checked),
    label: hasScenarios,
    report: hasScenarios,
  };
}

function Workspace({ id, step }) {
  const navigate = useNavigate();
  const { isStaff } = useAuth();
  const query = useAnalysis(id);
  // A step with unsaved input registers a save, which must succeed before leaving it.
  const beforeLeave = useRef(null);

  const registerBeforeLeave = useCallback((handler) => {
    beforeLeave.current = handler;
  }, []);

  // Forward slides in from the right, back from the left; the first step shown doesn't move.
  // -1 while a saved analysis is still loading, so arriving doesn't count as a move.
  const index = id && !query.data ? -1 : STEPS.findIndex(({ key }) => key === (query.data ? step : 'system'));
  const [shown, setShown] = useState({ index, motion: '' });
  if (shown.index !== index) {
    const direction = index > shown.index ? 'animate-slide-from-right' : 'animate-slide-from-left';
    setShown({ index, motion: index < 0 || shown.index < 0 ? '' : `${direction} motion-reduce:animate-none` });
  }

  if (id && query.isError && !query.data) return <ErrorBanner error={query.error} />;
  if (id && !query.data) return <p className="text-sm text-gray-500">Loading…</p>;

  const analysis = id ? query.data : null;
  if (analysis && !STEPS.some(({ key }) => key === step)) {
    return <Navigate to={`/analyses/${id}/${analysis.resume_step}`} replace />;
  }
  if (!analysis && !isStaff) return <Navigate to="/analyses" replace />;

  const current = analysis ? step : 'system';

  async function goTo(next) {
    // A new analysis gets its id from this save.
    const savedId = beforeLeave.current ? await beforeLeave.current() : id;
    if (savedId) navigate(`/analyses/${savedId}/${next}`);
  }

  const Screen = SCREENS[current];

  return (
    <div className="mx-auto max-w-3xl space-y-8">
      <div className="space-y-3 print:hidden">
        <Link to="/analyses" className="text-sm text-gray-500 hover:text-gray-900">
          ← Your analyses
        </Link>
        <div className="flex items-start justify-between gap-3">
          <h1 dir="auto" className="min-w-0 break-words text-lg font-semibold text-gray-900">
            {analysis ? analysis.name || 'Untitled system' : 'New analysis'}
          </h1>
          {analysis && isStaff && <AnalysisMenu analysis={analysis} onDeleted={() => navigate('/analyses')} />}
        </div>
      </div>
      <Stepper
        steps={analysis?.steps}
        current={current}
        locked={analysis?.locked}
        reachable={reachableSteps(analysis)}
        onSelect={goTo}
      />
      <div key={current} data-testid="step-panel" className={shown.motion}>
        <Screen analysis={analysis} goTo={goTo} registerBeforeLeave={registerBeforeLeave} />
      </div>
    </div>
  );
}

export default function AnalysisPage() {
  const { id, step } = useParams();
  const location = useLocation();
  const analysisId = id === 'new' ? null : id;
  // A new analysis keeps its screen, and any keystrokes, when its first save gives it an id.
  return <Workspace key={location.state?.session ?? id} id={analysisId} step={step} />;
}
