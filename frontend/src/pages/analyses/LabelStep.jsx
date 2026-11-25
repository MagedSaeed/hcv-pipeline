import { useState } from 'react';
import { activeRunOf, useAnalysisOptions, useLabelScenarios } from '../../api/analyses';
import { useAuth } from '../../contexts/auth-context';
import ErrorBanner from '../../components/ErrorBanner';
import LabelList from '../../components/analyses/LabelList';
import RunStatus from '../../components/analyses/RunStatus';
import ScenarioBrowser from '../../components/analyses/ScenarioBrowser';
import SettingsPanel from '../../components/analyses/SettingsPanel';
import SetupSummary from '../../components/analyses/SetupSummary';
import { primaryButton, secondaryButton, stepTitle } from '../../components/analyses/styles';
import useSettingsSaver from '../../components/analyses/useSettingsSaver';

const STATUS_NOTES = {
  current: 'Labelled',
  stale: 'Out of date',
  failed: 'Failed',
  unlabelled: 'Not labelled yet',
};

function statusNote(scenario) {
  if (scenario.set_aside) return '';
  if (scenario.label_status === 'current') return scenario.labels.length ? '' : 'No vulnerability found';
  return STATUS_NOTES[scenario.label_status];
}

function ScenarioLabels({ scenario }) {
  const { label_status: status, labels } = scenario;
  if (scenario.set_aside) return <p className="text-sm text-gray-500">Set aside, not labelled</p>;
  return (
    <div className="space-y-3">
      {status === 'unlabelled' && <p className="text-sm text-gray-500">Not labelled yet</p>}
      {status === 'stale' && <p className="text-sm text-amber-800">Out of date. Label again to update.</p>}
      {status === 'failed' && <p className="break-words text-sm text-red-700">{scenario.error}</p>}
      {status === 'current' && labels.length === 0 && (
        <p className="text-sm text-gray-600">No vulnerability found in this scenario</p>
      )}
      {labels.length > 0 && (
        <div className={status === 'current' ? '' : 'opacity-60'}>
          <LabelList labels={labels} />
        </div>
      )}
    </div>
  );
}

export default function LabelStep({ analysis, goTo }) {
  const { isStaff } = useAuth();
  const options = useAnalysisOptions(analysis.taxonomy.id);
  const saver = useSettingsSaver(analysis, 'labelling');
  const label = useLabelScenarios(analysis.id);
  const [panelOpen, setPanelOpen] = useState(false);
  const [selectedId, setSelectedId] = useState(null);

  const step = options.data?.labelling;
  const included = analysis.scenarios.filter((scenario) => !scenario.set_aside);
  const waiting = included.filter((scenario) => scenario.label_status !== 'current').length;
  const hasFailed = included.some((scenario) => scenario.label_status === 'failed');
  const hasResults = included.some((scenario) => scenario.label_status === 'current');
  const run = analysis.latest_runs.label;
  const running = Boolean(activeRunOf(analysis));
  const editable = isStaff && !running;
  const blocked = label.isPending || !analysis.locked;
  const position = run ? Math.min(run.done + run.failed + 1, run.total) : 0;

  async function start(body) {
    if (await saver.flush()) label.mutate(body);
  }

  let mainLabel = `Label ${waiting} ${waiting === 1 ? 'scenario' : 'scenarios'}`;
  if (!waiting) mainLabel = included.length ? 'All scenarios labelled' : 'No scenarios to label';

  return (
    <section className="space-y-6">
      <div className="space-y-2">
        <h2 className={stepTitle}>Label scenarios</h2>
        <SetupSummary
          settings={saver.settings}
          defaults={step?.defaults}
          onChange={editable ? () => setPanelOpen(true) : undefined}
        />
      </div>

      {options.isError && <ErrorBanner error={options.error} />}
      {saver.error && !panelOpen && <ErrorBanner error={saver.error} />}
      <RunStatus
        analysisId={analysis.id}
        run={run}
        working={`Examining scenario ${position} of ${run?.total}`}
        typical="About 10 seconds a scenario"
      />
      {label.isError && <ErrorBanner error={label.error} />}

      {editable && (
        <div className="flex flex-wrap gap-3">
          <button
            type="button"
            disabled={blocked || waiting === 0}
            onClick={() => start({})}
            className={waiting ? primaryButton : secondaryButton}
          >
            {mainLabel}
          </button>
          {hasFailed && (
            <button type="button" disabled={blocked} onClick={() => start({ only_failed: true })} className={secondaryButton}>
              Retry failed
            </button>
          )}
        </div>
      )}

      <ScenarioBrowser
        scenarios={analysis.scenarios}
        selectedId={selectedId}
        onSelect={setSelectedId}
        renderBelow={(scenario) => <ScenarioLabels scenario={scenario} />}
        itemNote={statusNote}
        showLabels
      />

      {hasResults && (
        <div className="flex justify-end border-t border-gray-200 pt-5">
          <button type="button" onClick={() => goTo('report')} className={waiting ? secondaryButton : primaryButton}>
            Continue to report →
          </button>
        </div>
      )}

      {panelOpen && editable && (
        <SettingsPanel
          kind="labelling"
          analysis={analysis}
          step={step}
          saver={saver}
          onClose={() => setPanelOpen(false)}
        />
      )}
    </section>
  );
}
