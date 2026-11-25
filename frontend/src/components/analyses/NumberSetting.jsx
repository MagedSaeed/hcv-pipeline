import { useState } from 'react';

// Saves each valid value as typed and keeps an invalid one on screen with its reason.
export default function NumberSetting({ id, label, value, isValid, invalidText, onSave, onValidity, children, ...input }) {
  const [typed, setTyped] = useState(null);
  const invalid = typed !== null && !isValid(typed);
  // Keep "1.0" as typed, but follow a reset or another change of the saved value.
  const shown = typed !== null && (invalid || Number(typed) === value) ? typed : String(value);

  function handleChange(event) {
    const text = event.target.value;
    const valid = isValid(text);
    setTyped(text);
    onValidity?.(valid);
    if (valid && Number(text) !== value) onSave(Number(text));
  }

  return (
    <div>
      <label htmlFor={id} className="block text-sm font-medium text-gray-700">
        {label}
      </label>
      <input
        id={id}
        type="number"
        value={shown}
        onChange={handleChange}
        aria-invalid={invalid}
        className="mt-1 w-28 rounded-md border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
        {...input}
      />
      {invalid && <p className="mt-1 text-xs text-red-700">{invalidText}</p>}
      {children}
    </div>
  );
}
