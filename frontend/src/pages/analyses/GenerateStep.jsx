import { useState } from 'react';
import {
  activeRunOf,
  useAddScenario,
  useAnalysisOptions,
  useDeleteScenario,
  useGenerate,
  useUpdateScenario,
} from '../../api/analyses';
import { useAuth } from '../../contexts/auth-context';
import ConfirmDialog from '../../components/ConfirmDialog';
import ErrorBanner from '../../components/ErrorBanner';
import ActionMenu from '../../components/analyses/ActionMenu';
import RunStatus from '../../components/analyses/RunStatus';
import ScenarioBrowser from '../../components/analyses/ScenarioBrowser';
import ScenarioForm from '../../components/analyses/ScenarioForm';
import SettingsPanel from '../../components/analyses/SettingsPanel';
import SetupSummary from '../../components/analyses/SetupSummary';
import { primaryButton, secondaryButton, stepTitle } from '../../components/analyses/styles';
import useSettingsSaver from '../../components/analyses/useSettingsSaver';

function isCount(text) {
  return text.trim() !== '' && Number.isInteger(Number(text)) && Number(text) >= 1 && Number(text) <= 20;
}

function Scenarios({ analysis, editable }) {
  const add = useAddScenario(analysis.id);
  const update = useUpdateScenario(analysis.id);
  const remove = useDeleteScenario(analysis.id);
  const [form, setForm] = useState(null);
  const [selectedId, setSelectedId] = useState(null);

  const included = analysis.scenarios.filter((scenario) => !scenario.set_aside);
  const aside = analysis.scenarios.length - included.length;

  function menuFor(scenario) {
    if (!editable) return [];
    const toggle = {
      label: scenario.set_aside ? 'Restore' : 'Set aside',
      onSelect: () => update.mutate({ scenarioId: scenario.id, set_aside: !scenario.set_aside }),
    };
    if (!scenario.is_manual) return [toggle];
    return [
      {
        label: 'Edit',
        onSelect: () => {
          update.reset();
          setForm({ scenario });
        },
      },
      toggle,
      { label: 'Delete', danger: true, onSelect: () => remove.mutate(scenario.id) },
    ];
  }

  const headerItems =
    editable && analysis.locked
      ? [
          {
            label: 'Add your own scenario',
            onSelect: () => {
              add.reset();
              setForm({});
            },
          },
        ]
      : [];
  const error = (!form && update.error) || remove.error;

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h3 className="font-medium text-gray-900">Scenarios</h3>
          <p className="text-sm text-gray-500">
            {included.length} included{aside > 0 && `, ${aside} set aside`}
          </p>
        </div>
        <ActionMenu label="Scenario options" items={headerItems} />
      </div>
      {error && <ErrorBanner error={error} />}
      <ScenarioBrowser
        scenarios={analysis.scenarios}
        selectedId={selectedId}
        onSelect={setSelectedId}
        menuFor={menuFor}
      />
      {form && (
        <ScenarioForm
          scenario={form.scenario}
          mutation={form.scenario ? update : add}
          onClose={(created) => {
            setForm(null);
            if (created?.id) setSelectedId(created.id);
          }}
        />
      )}
    </div>
  );
}

// "[ Generate | 8 | scenarios ]": the count sits inside the action it changes.
// The count beside the button that runs the generator; the count is hidden when the prompt takes none.
function GenerateControl({
  label,
  groupLabel,
  primary = false,
  takesCount,
  count,
  disabled,
  onCount,
  onValidity,
  onClick,
}) {
  const [typed, setTyped] = useState(null);
  const invalid = typed !== null && !isCount(typed);
  const shown = typed !== null && (invalid || Number(typed) === count) ? typed : String(count);

  function handleChange(event) {
    const text = event.target.value;
    setTyped(text);
    onValidity(isCount(text));
    if (isCount(text) && Number(text) !== count) onCount(Number(text));
  }

  return (
    <div className="space-y-1">
      <div role="group" aria-label={groupLabel} className="flex flex-wrap items-center justify-center gap-3">
        {takesCount && (
          <label className="flex items-center gap-2 text-sm text-gray-700">
            Number of scenarios
            <input
              type="number"
              min="1"
              max="20"
              step="1"
              value={shown}
              onChange={handleChange}
              aria-invalid={invalid}
              className="w-16 rounded-md border border-gray-300 bg-white px-2 py-1.5 text-center text-sm focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500 aria-[invalid=true]:border-red-500"
            />
          </label>
        )}
        <button
          type="button"
          disabled={disabled || invalid}
          onClick={onClick}
          className={primary ? primaryButton : secondaryButton}
        >
          {label}
        </button>
      </div>
      {invalid && <p className="text-xs text-red-700">Choose a whole number from 1 to 20.</p>}
    </div>
  );
}

export default function GenerateStep({ analysis, goTo }) {
  const { isStaff } = useAuth();
  const options = useAnalysisOptions(analysis.taxonomy.id);
  const saver = useSettingsSaver(analysis, 'generation');
  const generate = useGenerate(analysis.id);
  const [panelOpen, setPanelOpen] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [countValid, setCountValid] = useState(true);

  const { settings } = saver;
  const step = options.data?.generation;
  const takesCount = Boolean(step?.prompts.find((prompt) => prompt.ref === settings.prompt)?.takes_count);
  const run = analysis.latest_runs.generate;
  const running = Boolean(activeRunOf(analysis));
  const hasGenerated = analysis.scenarios.some((scenario) => !scenario.is_manual);
  const blocked = running || generate.isPending || (takesCount && !countValid);
  const editable = isStaff && !running;

  async function start() {
    setConfirming(false);
    if (await saver.flush()) generate.mutate();
  }

  return (
    <section className="space-y-6">
      <div className="space-y-2">
        <h2 className={stepTitle}>Generate scenarios</h2>
        <SetupSummary
          settings={settings}
          defaults={step?.defaults}
          onChange={editable ? () => setPanelOpen(true) : undefined}
        />
      </div>

      {options.isError && <ErrorBanner error={options.error} />}
      {saver.error && !panelOpen && <ErrorBanner error={saver.error} />}
      <RunStatus analysisId={analysis.id} run={run} working="Writing scenarios" typical="This can take a while" />
      {generate.isError && <ErrorBanner error={generate.error} />}

      {/* Shown once the prompt list says whether it takes a count, so the button doesn't change under the cursor. */}
      {editable && step && !hasGenerated && (
        <div className="rounded-xl border border-dashed border-gray-300 px-6 py-8 text-center">
          <p className="mx-auto max-w-md text-sm text-gray-600">
            The model writes scenarios from the values you checked and your description.
          </p>
          <div className="mt-4">
            <GenerateControl
              label="Generate scenarios"
              groupLabel="Generate scenarios"
              primary
              takesCount={takesCount}
              count={settings.count}
              disabled={blocked}
              onCount={(count) => saver.save({ count })}
              onValidity={setCountValid}
              onClick={start}
            />
          </div>
          <p className="mt-2 text-xs text-gray-500">The description will be locked.</p>
        </div>
      )}

      {confirming && editable && (
        <ConfirmDialog
          title="Replace the generated scenarios?"
          message="When the new ones arrive they replace the generated scenarios and their labels. Scenarios added manually stay."
          confirmLabel="Replace scenarios"
          onConfirm={start}
          onCancel={() => setConfirming(false)}
        />
      )}

      {analysis.scenarios.length > 0 && <Scenarios analysis={analysis} editable={editable} />}

      {hasGenerated && (
        <div className="flex flex-wrap items-center justify-end gap-3 border-t border-gray-200 pt-5">
          {editable && step && (
            <GenerateControl
              label="Generate again"
              groupLabel="Generate scenarios again"
              takesCount={takesCount}
              count={settings.count}
              disabled={blocked}
              onCount={(count) => saver.save({ count })}
              onValidity={setCountValid}
              onClick={() => setConfirming(true)}
            />
          )}
          <button type="button" disabled={running} onClick={() => goTo('label')} className={primaryButton}>
            Continue to labelling →
          </button>
        </div>
      )}

      {panelOpen && editable && (
        <SettingsPanel
          kind="generation"
          analysis={analysis}
          step={step}
          saver={saver}
          onClose={() => setPanelOpen(false)}
        />
      )}
    </section>
  );
}
