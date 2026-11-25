// The categories of backend/taxonomy/categories.py, which validates them.
const FRAMEWORKS = [
  {
    key: 'stride',
    title: 'STRIDE',
    names: [
      'Spoofing',
      'Tampering',
      'Repudiation',
      'Information disclosure',
      'Denial of service',
      'Elevation of privilege',
    ],
  },
  {
    key: 'linddun',
    title: 'LINDDUN',
    names: [
      'Linking',
      'Identifying',
      'Non-repudiation',
      'Detecting',
      'Data disclosure',
      'Unawareness',
      'Non-compliance',
    ],
  },
];

const TEXT_FIELDS = [
  ['condition_class', 'Condition class'],
  ['element', 'Element at threat'],
  ['rule', 'Rule'],
  ['qualifier', 'Qualifier, when only this row has a threat'],
];

function emptyMapping() {
  return { condition_class: '', element: '', rule: '', stride: [], linddun: [], qualifier: '' };
}

export default function ThreatMappings({ mappings }) {
  return mappings.map((mapping) => (
    <div key={mapping.condition_class} className="mt-2 rounded-md bg-gray-50 px-3 py-2 text-xs text-gray-600">
      <p className="break-words font-medium text-gray-700">{mapping.condition_class}</p>
      {(mapping.element || mapping.rule) && (
        <p>
          At threat: {mapping.element}
          {mapping.rule && ` (rule ${mapping.rule})`}
        </p>
      )}
      {FRAMEWORKS.map(({ key, title }) => (
        <p key={key}>
          {title}: {mapping[key].length ? mapping[key].join('; ') : 'None'}
        </p>
      ))}
      {mapping.qualifier && <p>Qualifier: {mapping.qualifier}</p>}
    </div>
  ));
}

export function ThreatMappingsEditor({ mappings, onChange }) {
  function update(index, changes) {
    onChange(mappings.map((mapping, at) => (at === index ? { ...mapping, ...changes } : mapping)));
  }

  function toggle(index, framework, name) {
    const chosen = mappings[index][framework.key];
    const next = chosen.includes(name) ? chosen.filter((item) => item !== name) : [...chosen, name];
    update(index, { [framework.key]: framework.names.filter((item) => next.includes(item)) });
  }

  return (
    <div className="space-y-2">
      <p className="text-sm font-medium text-gray-700">Threat mappings</p>
      {mappings.map((mapping, index) => (
        <fieldset key={index} aria-label={`Threat mapping ${index + 1}`} className="space-y-2 rounded-md border border-gray-200 p-3">
          {TEXT_FIELDS.map(([field, title]) => (
            <label key={field} className="block text-xs text-gray-700">
              {title}
              <input
                value={mapping[field]}
                required={field === 'condition_class'}
                onChange={(event) => update(index, { [field]: event.target.value })}
                className="mt-1 block w-full rounded-md border border-gray-300 px-2 py-1 text-sm"
              />
            </label>
          ))}
          {FRAMEWORKS.map((framework) => (
            <div key={framework.key} className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-gray-700">
              <span className="font-medium">{framework.title}</span>
              {framework.names.map((name) => (
                <label key={name} className="flex items-center gap-1">
                  <input
                    type="checkbox"
                    checked={mapping[framework.key].includes(name)}
                    onChange={() => toggle(index, framework, name)}
                  />
                  {name}
                </label>
              ))}
            </div>
          ))}
          <button
            type="button"
            aria-label={`Remove threat mapping ${index + 1}`}
            onClick={() => onChange(mappings.filter((_, at) => at !== index))}
            className="text-xs font-medium text-red-700 hover:underline"
          >
            Remove
          </button>
        </fieldset>
      ))}
      <button
        type="button"
        onClick={() => onChange([...mappings, emptyMapping()])}
        className="rounded-md border border-gray-300 px-2 py-1 text-xs font-medium text-gray-700 hover:bg-gray-100"
      >
        Add threat mapping
      </button>
    </div>
  );
}
