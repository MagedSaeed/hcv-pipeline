import { useEffect, useState } from 'react';
import { useAnalysisReport } from '../../api/analyses';
import ErrorBanner from '../../components/ErrorBanner';
import { Finding, ThreatLegend } from '../../components/analyses/Findings';
import LabelList from '../../components/analyses/LabelList';
import ManualTag from '../../components/analyses/ManualTag';
import ResearchDetails from '../../components/analyses/ResearchDetails';
import MasterDetail from '../../components/analyses/MasterDetail';
import ScenarioBrowser from '../../components/analyses/ScenarioBrowser';
import Tab from '../../components/analyses/Tab';
import useWideScreen from '../../components/analyses/useWideScreen';
import { secondaryButton, stepTitle } from '../../components/analyses/styles';

const DISCLAIMER =
  'These are potential vulnerabilities found in generated or analyst-written scenarios. ' +
  'Check them against the real system. A vulnerability that is not listed may still be present.';

function reportTitle(report) {
  const count = report.findings.length;
  return `${report.name || 'Untitled system'}: ${count} potential ${count === 1 ? 'vulnerability' : 'vulnerabilities'}`;
}

function incompleteText({ unlabelled, failed, stale }) {
  const total = unlabelled + failed + stale;
  if (!total) return '';
  const parts = [
    unlabelled && `${unlabelled} not checked yet`,
    failed && `${failed} failed`,
    stale && `${stale} out of date`,
  ].filter(Boolean);
  const subject = total === 1 ? '1 scenario is' : `${total} scenarios are`;
  return `${subject} not in this report: ${parts.join(', ')}.`;
}

function SetAside({ scenarios }) {
  if (scenarios.length === 0) return null;
  return (
    <section aria-label="Set aside" className="text-sm text-gray-600">
      <h3 className="font-medium text-gray-700">Set aside, not in this report</h3>
      <ul className="mt-1 list-disc ps-5">
        {scenarios.map((scenario) => (
          <li key={scenario.id}>
            <span dir="auto" className="break-words">
              {scenario.title}
            </span>{' '}
            {scenario.is_manual && <ManualTag />}
          </li>
        ))}
      </ul>
    </section>
  );
}

// A plain link: a click downloads, and "Open in new tab" shows the PDF.
function PdfLink({ analysisId, children }) {
  return (
    <a href={`/api/analyses/${analysisId}/report/pdf/`} download className={`${secondaryButton} shrink-0`}>
      {children}
    </a>
  );
}

function plural(count, word) {
  return `${count} ${count === 1 ? word : `${word}s`}`;
}

function Findings({ report, onShow }) {
  const wide = useWideScreen();
  const [selected, setSelected] = useState(null);

  if (report.findings.length === 0) {
    return <p className="py-6 text-sm text-gray-600">No vulnerabilities in the scenarios checked so far.</p>;
  }
  if (!wide) {
    return (
      <div className="space-y-4">
        <ThreatLegend />
        {report.findings.map((finding) => (
          <Finding key={finding.hcv_id} finding={finding} onShow={onShow} />
        ))}
      </div>
    );
  }
  const shown = report.findings.find((finding) => finding.hcv_id === selected) ?? report.findings[0];
  return (
    <div className="space-y-3">
      <ThreatLegend />
      <MasterDetail
        listLabel="Vulnerabilities"
        detailLabel="Selected vulnerability"
        items={report.findings.map((finding) => ({ key: finding.hcv_id, finding }))}
        selectedKey={shown.hcv_id}
        onSelect={setSelected}
        itemLabel={({ finding }) => finding.name}
        renderCompact={({ finding }) => (
          <span className="font-mono text-xs text-gray-700">{finding.hcv_id.replace(/^HCV-/, '')}</span>
        )}
        renderItem={({ finding }) => (
          <span className="block">
            <span className="block text-sm font-medium text-gray-900">{finding.name}</span>
            <span className="block text-xs text-gray-500">
              {plural(finding.scenario_count, 'scenario')}
              {finding.threats.length > 0 && ` · ${plural(finding.threats.length, 'threat')}`}
            </span>
          </span>
        )}
      >
        <Finding finding={shown} onShow={onShow} bare />
      </MasterDetail>
    </div>
  );
}

// The panel slides in from the side of the tab that was clicked.
const TAB_MOTION = {
  findings: 'animate-slide-from-left motion-reduce:animate-none',
  scenarios: 'animate-slide-from-right motion-reduce:animate-none',
};

function ReportScreen({ analysisId, report, numberOf }) {
  const [tab, setTab] = useState('findings');
  const [switched, setSwitched] = useState(false);
  const [focused, setFocused] = useState(null);
  const incomplete = incompleteText(report.incomplete);

  useEffect(() => {
    if (tab === 'scenarios' && focused) document.getElementById(`scenario-${focused}`)?.scrollIntoView?.();
  }, [tab, focused]);

  function openTab(next) {
    setSwitched(true);
    setTab(next);
  }

  function showScenario(id) {
    setFocused(id);
    openTab('scenarios');
  }

  return (
    <div data-testid="report-screen" className="space-y-6">
      {/* The title wraps beside the button, so the button stays at the right. */}
      <div className="flex items-start justify-between gap-4">
        <h2 dir="auto" className={`${stepTitle} min-w-0 flex-1 break-words`}>
          {reportTitle(report)}
        </h2>
        <PdfLink analysisId={analysisId}>Download PDF</PdfLink>
      </div>
      <p className="text-sm leading-relaxed text-gray-600">{DISCLAIMER}</p>
      {incomplete && (
        <p role="status" className="rounded-md bg-amber-50 p-3 text-sm text-amber-900">
          {incomplete}
        </p>
      )}

      <div role="tablist" aria-label="Report view" className="flex gap-6 border-b border-gray-200">
        <Tab selected={tab === 'findings'} onSelect={() => openTab('findings')}>
          Vulnerabilities
        </Tab>
        <Tab
          selected={tab === 'scenarios'}
          onSelect={() => {
            setFocused(null);
            openTab('scenarios');
          }}
        >
          Scenarios
        </Tab>
      </div>

      <div key={tab} role="tabpanel" className={switched ? TAB_MOTION[tab] : ''}>
        {tab === 'findings' ? (
          <Findings report={report} onShow={showScenario} />
        ) : (
        <ScenarioBrowser
          scenarios={report.scenarios}
          selectedId={focused}
          onSelect={setFocused}
          renderBelow={(scenario) => (scenario.labels.length > 0 ? <LabelList labels={scenario.labels} scenario={scenario} /> : null)}
            numberOf={numberOf}
            showLabels
          />
        )}
      </div>

      <SetAside scenarios={report.set_aside} />
      <div className="flex justify-end border-t border-gray-200 pt-5">
        <PdfLink analysisId={analysisId}>Download as PDF</PdfLink>
      </div>
      <details className="rounded-xl border border-gray-200 bg-white p-4">
        <summary className="cursor-pointer text-sm text-gray-600">Research details</summary>
        <div className="mt-3">
          <ResearchDetails report={report} />
        </div>
      </details>
    </div>
  );
}

export default function ReportStep({ analysis }) {
  const report = useAnalysisReport(analysis.id, analysis.updated_at);
  const order = analysis.scenarios.map((scenario) => scenario.id);

  if (report.data) {
    return (
      <ReportScreen
        analysisId={analysis.id}
        report={report.data}
        numberOf={(scenario) => order.indexOf(scenario.id) + 1}
      />
    );
  }
  if (report.isError) return <ErrorBanner error={report.error} />;
  return <p className="text-sm text-gray-500">Loading…</p>;
}
