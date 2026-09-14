import { screen } from '@testing-library/react';

// Drives a SearchableSelect (components/SearchableSelect.jsx): finds its
// trigger by `role="combobox"` + accessible `name` (its <label>'s text),
// opens it, and clicks the option for `value` (its underlying option
// value, e.g. a numeric id as a string) - never by the option's visible
// label text, so a call site stays agnostic to exact label wording, the
// same way `pickModel` (ExperimentBuilder.test.jsx/PromptEditor.test.jsx)
// already drives ModelPicker via its own `data-testid`. Scoping to the
// combobox role (rather than `getByLabelText`, which also matches the
// field's own "Clear <label>" button once a value is set) keeps this
// unambiguous even after a value has been picked once already.
export async function pickOption(user, name, value) {
  const field = await screen.findByRole('combobox', { name });
  await user.click(field);
  const option = await screen.findByTestId(`${field.id}-option-${value}`);
  await user.click(option);
  return field;
}
