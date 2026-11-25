import { useModels } from '../../api/queries';
import Badge from './Badge';
import { formatTemperature, isRecommended, modelName, promptingText } from './setup';
import { linkButton } from './styles';

// `prompting` replaces the examples count for steps that have no examples.
export default function SetupSummary({ settings, defaults, onChange, prompting }) {
  const models = useModels().data?.results;
  const recommended = defaults ? isRecommended(settings, defaults) : null;

  return (
    <div data-testid="setup-summary" className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-gray-600">
      {recommended === true && <Badge>Recommended</Badge>}
      {recommended === false && <Badge tone="custom">Custom</Badge>}
      <span>
        {modelName(models, settings.model)} · {formatTemperature(settings.temperature)} ·{' '}
        {prompting ?? promptingText(settings.examples.length)}
      </span>
      {onChange && (
        <button type="button" onClick={onChange} className={`${linkButton} ml-auto`}>
          Change
        </button>
      )}
    </div>
  );
}
