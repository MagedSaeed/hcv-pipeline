import { useModels } from '../../api/queries';
import { formatTemperature, mayIgnoreTemperature, promptingText, taxonomyName, TEMPERATURE_HINT } from './setup';

function Setup({ title, setup }) {
  const models = useModels().data?.results;
  return (
    <div className="space-y-0.5">
      <p className="font-medium text-gray-900">
        {title}: {setup.is_custom ? 'Custom' : 'Recommended'}
      </p>
      <p className="break-words">
        {setup.model} · temperature {formatTemperature(setup.temperature)}
      </p>
      {mayIgnoreTemperature(models, setup.model) && <p className="text-gray-500">{TEMPERATURE_HINT}</p>}
      <p>
        {setup.prompt} · {promptingText(setup.examples.length)}
        {setup.count !== null && ` · ${setup.count} scenarios asked for`}
      </p>
      {setup.examples.length > 0 && <p className="break-words text-gray-500">Examples: {setup.examples.join(', ')}</p>}
    </div>
  );
}

function Extraction({ setup }) {
  return (
    <div className="space-y-0.5">
      <p className="font-medium text-gray-900">Extraction: {setup.is_custom ? 'Custom' : 'Recommended'}</p>
      <p className="break-words">
        {setup.model} · temperature {formatTemperature(setup.temperature)}
      </p>
      <p>{setup.prompt}</p>
      <p>
        {setup.approved} values approved ({setup.edited} edited, {setup.added} added), {setup.rejected} rejected
      </p>
    </div>
  );
}

export default function ResearchDetails({ report }) {
  const { taxonomy, extraction, generation, labelling, calls_cost: cost } = report.details;
  return (
    <div className="space-y-4 text-xs text-gray-700">
      <p>Taxonomy: {taxonomyName(taxonomy)}</p>
      {extraction && <Extraction setup={extraction} />}
      {generation && <Setup title="Generation" setup={generation} />}
      {labelling.map((setup, index) => (
        <Setup key={index} title="Labelling" setup={setup} />
      ))}
      {typeof cost === 'number' && <p>Cost of this analysis so far: ${cost.toFixed(4)}</p>}
      <ul className="space-y-2">
        {report.findings.map((finding) => (
          <li key={finding.hcv_id} className="break-words">
            <span className="font-medium text-gray-900">{finding.hcv_id}</span>
            {finding.mappings.map((mapping) => (
              <span key={mapping.condition_class} className="block">
                {mapping.condition_class}
                {mapping.rule && ` (rule ${mapping.rule})`}
              </span>
            ))}
          </li>
        ))}
      </ul>
    </div>
  );
}
