import { useState } from 'react';
import ErrorBanner from '../ErrorBanner';
import Modal from '../Modal';
import { primaryButton, textInput } from './styles';

const OPTIONAL_FIELDS = [
  ['actor', 'Who is involved'],
  ['trigger', 'Occasion'],
  ['standing_state', 'What was already true'],
  ['asset', 'What it reaches'],
  ['threat', 'Threat'],
  ['outcome', 'Outcome'],
];

export default function ScenarioForm({ scenario, mutation, onClose }) {
  const [values, setValues] = useState(() => ({
    narrative: scenario?.narrative ?? '',
    ...Object.fromEntries(OPTIONAL_FIELDS.map(([key]) => [key, scenario?.[key] ?? ''])),
  }));

  function handleSubmit(event) {
    event.preventDefault();
    const body = scenario ? { scenarioId: scenario.id, ...values } : values;
    mutation.mutate(body, { onSuccess: (saved) => onClose(saved) });
  }

  function field(key) {
    return {
      dir: 'auto',
      value: values[key],
      onChange: (event) => setValues({ ...values, [key]: event.target.value }),
      className: textInput,
    };
  }

  return (
    <Modal title={scenario ? 'Edit your scenario' : 'Add your own scenario'} onClose={onClose}>
      <form onSubmit={handleSubmit} className="space-y-4">
        <label className="block text-sm font-medium text-gray-700">
          What happens?
          <textarea rows={5} autoFocus maxLength={20000} {...field('narrative')} />
        </label>
        <details>
          <summary className="cursor-pointer text-sm text-gray-600">More details, optional</summary>
          <div className="mt-3 space-y-3">
            {OPTIONAL_FIELDS.map(([key, title]) => (
              <label key={key} className="block text-sm text-gray-700">
                {title}
                <textarea rows={2} maxLength={10000} {...field(key)} />
              </label>
            ))}
          </div>
        </details>
        {mutation.isError && <ErrorBanner error={mutation.error} />}
        <button type="submit" disabled={!values.narrative.trim() || mutation.isPending} className={primaryButton}>
          {scenario ? 'Save scenario' : 'Add scenario'}
        </button>
      </form>
    </Modal>
  );
}
