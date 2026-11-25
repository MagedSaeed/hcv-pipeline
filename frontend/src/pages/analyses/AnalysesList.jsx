import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useAnalyses } from '../../api/analyses';
import { useAuth } from '../../contexts/auth-context';
import ErrorBanner from '../../components/ErrorBanner';
import AnalysisMenu from '../../components/analyses/AnalysisMenu';
import { STEPS } from '../../components/analyses/steps';
import { primaryButton } from '../../components/analyses/styles';

function ScopeButton({ active, onClick, children }) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={`rounded-md px-3 py-1.5 text-sm ${
        active ? 'bg-white font-medium text-gray-900 shadow-sm' : 'text-gray-600 hover:text-gray-900'
      }`}
    >
      {children}
    </button>
  );
}

function StepDots({ steps }) {
  return (
    <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs">
      {STEPS.map(({ key, title }) => (
        <li key={key} className={steps[key] ? 'text-brand-700' : 'text-gray-400'}>
          <span aria-hidden="true">{steps[key] ? '✓' : '○'}</span> {title}
          <span className="sr-only">{steps[key] ? ' (done)' : ' (not done)'}</span>
        </li>
      ))}
    </ul>
  );
}

export default function AnalysesList() {
  const { isStaff } = useAuth();
  const [scope, setScope] = useState(isStaff ? 'mine' : 'all');
  const analyses = useAnalyses(scope);
  const rows = analyses.data?.results;

  return (
    <div className="mx-auto max-w-3xl space-y-8">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-gray-900">Your analyses</h1>
          <p className="mt-1 text-sm text-gray-600">
            Describe a system, and see how people and technology could fail together.
          </p>
        </div>
        {isStaff && (
          <Link to="/analyses/new" className={primaryButton}>
            + New analysis
          </Link>
        )}
      </div>

      <div className="inline-flex gap-1 rounded-lg bg-gray-100 p-1">
        <ScopeButton active={scope === 'mine'} onClick={() => setScope('mine')}>
          Mine
        </ScopeButton>
        <ScopeButton active={scope === 'all'} onClick={() => setScope('all')}>
          Everyone&apos;s
        </ScopeButton>
      </div>

      {analyses.isError && <ErrorBanner error={analyses.error} />}
      {analyses.isPending && <p className="text-sm text-gray-500">Loading…</p>}

      {rows?.length === 0 && (
        <div className="rounded-xl border border-dashed border-gray-300 px-6 py-14 text-center">
          <p className="font-medium text-gray-900">No analyses yet</p>
          <p className="mt-1 text-sm text-gray-600">
            {isStaff
              ? 'Start with a description of a system you know. Your work is saved as you go.'
              : 'Analyses will appear here once someone creates one.'}
          </p>
        </div>
      )}

      {rows?.length > 0 && (
        <ul className="divide-y divide-gray-100 rounded-xl border border-gray-200 bg-white">
          {rows.map((analysis) => (
            <li key={analysis.id} className="flex items-start gap-3 px-5 py-4">
              <div className="min-w-0 flex-1">
                <Link
                  to={`/analyses/${analysis.id}`}
                  dir="auto"
                  className="block break-words font-medium text-gray-900 hover:text-brand-700"
                >
                  {analysis.name || 'Untitled system'}
                </Link>
                <StepDots steps={analysis.steps} />
                <p className="mt-1 text-xs text-gray-500">
                  {scope === 'all' && analysis.owner_email && `${analysis.owner_email} · `}
                  Updated {new Date(analysis.updated_at).toLocaleDateString()}
                </p>
              </div>
              {isStaff && <AnalysisMenu analysis={analysis} />}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
