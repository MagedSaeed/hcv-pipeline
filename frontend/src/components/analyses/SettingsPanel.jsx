import { useState } from 'react';
import { useUpdateAnalysis } from '../../api/analyses';
import { extractFieldErrors } from '../../api/errors';
import { useModels, useTaxonomies } from '../../api/queries';
import ErrorBanner from '../ErrorBanner';
import ModelPicker from '../ModelPicker';
import SidePanel from '../SidePanel';
import Badge from './Badge';
import NumberSetting from './NumberSetting';
import { mayIgnoreTemperature, taxonomyName, TEMPERATURE_HINT } from './setup';
import { linkButton, secondaryButton, textInput } from './styles';

const TITLES = {
  extraction: 'Extraction settings',
  generation: 'Generation settings',
  labelling: 'Identification settings',
};

function isTemperature(text) {
  return text.trim() !== '' && Number(text) >= 0 && Number(text) <= 2;
}

function Choice({ type, name, checked, disabled, onChange, title, recommended, note, error }) {
  return (
    <label className={`flex items-start gap-3 rounded-md px-2 py-2 ${disabled ? '' : 'cursor-pointer hover:bg-gray-50'}`}>
      <input type={type} name={name} checked={checked} disabled={disabled} onChange={onChange} className="mt-1" />
      <span className="min-w-0 flex-1 text-sm">
        <span className={`mr-2 break-words ${disabled ? 'text-gray-400' : 'text-gray-900'}`}>{title}</span>
        {recommended && <Badge>Recommended</Badge>}
        {note && <span className="block text-xs text-amber-800">{note}</span>}
        {error && <span className="block break-words text-xs text-red-700">{error}</span>}
      </span>
    </label>
  );
}

function PromptChoices({ step, settings, save }) {
  const missing = !step.prompts.some((prompt) => prompt.ref === settings.prompt);
  return (
    <fieldset>
      <legend className="text-sm font-medium text-gray-700">Prompt</legend>
      {missing && <p className="text-xs text-red-700">The saved prompt is no longer available. Choose another.</p>}
      {step.prompts.map((prompt) => (
        <Choice
          key={prompt.ref}
          type="radio"
          name="analysis-prompt"
          checked={prompt.ref === settings.prompt}
          disabled={Boolean(prompt.error)}
          onChange={() => save({ prompt: prompt.ref })}
          title={`${prompt.name} v${prompt.version}`}
          recommended={prompt.recommended}
          note={prompt.note}
          error={prompt.error}
        />
      ))}
    </fieldset>
  );
}

function ExampleChoices({ step, settings, save }) {
  const [choosing, setChoosing] = useState(settings.examples.length > 0);
  const listing = choosing || settings.examples.length > 0;
  const recommendsNone = step.defaults.examples.length === 0;

  function chooseFromList() {
    setChoosing(true);
    if (!recommendsNone) save({ examples: step.defaults.examples });
  }

  function chooseNone() {
    setChoosing(false);
    save({ examples: [] });
  }

  function toggle(ref) {
    const chosen = new Set(settings.examples);
    if (chosen.has(ref)) chosen.delete(ref);
    else chosen.add(ref);
    setChoosing(true);
    save({ examples: step.examples.map((example) => example.ref).filter((item) => chosen.has(item)) });
  }

  return (
    <fieldset>
      <legend className="text-sm font-medium text-gray-700">Worked examples</legend>
      <Choice
        type="radio"
        name="analysis-examples"
        checked={!listing}
        onChange={chooseNone}
        title="None, zero-shot"
        recommended={recommendsNone}
      />
      <Choice
        type="radio"
        name="analysis-examples"
        checked={listing}
        onChange={chooseFromList}
        title="Choose from the list"
        recommended={!recommendsNone}
      />
      {listing && (
        <div className="ml-6">
          {step.examples.length === 0 && <p className="px-2 text-xs text-gray-500">There are no examples to choose.</p>}
          {step.examples.map((example) => (
            <Choice
              key={example.ref}
              type="checkbox"
              checked={settings.examples.includes(example.ref)}
              // A selected example can always be removed.
              disabled={Boolean(example.error) && !settings.examples.includes(example.ref)}
              onChange={() => toggle(example.ref)}
              title={example.name}
              recommended={example.recommended}
              error={example.error}
            />
          ))}
        </div>
      )}
    </fieldset>
  );
}

function TaxonomySetting({ analysis }) {
  const taxonomies = useTaxonomies({ enabled: !analysis.locked });
  const update = useUpdateAnalysis();

  if (analysis.locked) {
    return (
      <div className="text-sm">
        <p className="font-medium text-gray-700">Taxonomy</p>
        <p className="mt-1 text-gray-900">{taxonomyName(analysis.taxonomy)}</p>
        <p className="text-xs text-gray-500">Fixed once generation starts.</p>
      </div>
    );
  }

  const examplesDoNotFit = Boolean(extractFieldErrors(update.error).labelling_settings);

  return (
    <div className="space-y-2">
      <label className="block text-sm font-medium text-gray-700">
        Taxonomy
        <select
          value={analysis.taxonomy.id}
          disabled={update.isPending}
          onChange={(event) => update.mutate({ id: analysis.id, taxonomy: Number(event.target.value) })}
          className={textInput}
        >
          {(taxonomies.data?.results ?? [analysis.taxonomy]).map((taxonomy) => (
            <option key={taxonomy.id} value={taxonomy.id}>
              {taxonomyName(taxonomy)}
            </option>
          ))}
        </select>
      </label>
      {update.isError && <ErrorBanner error={update.error} />}
      {examplesDoNotFit && (
        <button
          type="button"
          className={secondaryButton}
          onClick={() =>
            update.mutate({ ...update.variables, labelling_settings: { ...analysis.labelling_settings, examples: [] } })
          }
        >
          Switch identification to zero-shot and use it
        </button>
      )}
    </div>
  );
}

export default function SettingsPanel({ kind, analysis, step, saver, onClose }) {
  const models = useModels();
  const { settings, save, error } = saver;

  return (
    <SidePanel title={TITLES[kind]} open onClose={onClose}>
      <div className="h-full space-y-6 overflow-y-auto p-4">
        <ModelPicker
          id={`${kind}-model`}
          label="Model"
          value={settings.model}
          onChange={(model) => save({ model })}
          modelsQuery={models}
        />
        <NumberSetting
          id={`${kind}-temperature`}
          label="Temperature"
          value={settings.temperature}
          min="0"
          max="2"
          step="0.1"
          isValid={isTemperature}
          invalidText="Choose a number from 0 to 2."
          onSave={(temperature) => save({ temperature })}
        >
          {mayIgnoreTemperature(models.data?.results, settings.model) && (
            <p className="mt-1 text-xs text-gray-500">{TEMPERATURE_HINT}</p>
          )}
        </NumberSetting>
        {kind !== 'extraction' && step && <PromptChoices step={step} settings={settings} save={save} />}
        {kind !== 'extraction' && step && <ExampleChoices step={step} settings={settings} save={save} />}
        {kind === 'extraction' && step && (
          <div className="text-sm">
            <p className="font-medium text-gray-700">Prompt</p>
            <p className="mt-1 flex items-center gap-2 text-gray-900">
              {step.prompt} <Badge>Recommended</Badge>
            </p>
          </div>
        )}
        {kind === 'generation' && <TaxonomySetting analysis={analysis} />}
        {error && <ErrorBanner error={error} />}
        {step && (
          <button type="button" onClick={() => save({ ...step.defaults })} className={linkButton}>
            Reset to recommended
          </button>
        )}
      </div>
    </SidePanel>
  );
}
