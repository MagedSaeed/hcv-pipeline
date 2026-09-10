// Turns a DRF error response into one human-readable string.
//
// DRF's shapes vary by where the error was raised:
//   - a whole-serializer ValidationError (e.g. bad login credentials) comes
//     back as {"non_field_errors": ["..."]}
//   - a field-level ValidationError (e.g. wrong current password) comes
//     back as {"<field>": ["..."]}
//   - a permission/auth failure comes back as {"detail": "..."}
// Task 1 has no per-field form to route these onto yet (that lands in Task
// 6's experiment builder); until then, surface the first message found.
export function extractErrorMessage(error, fallback = 'Something went wrong. Please try again.') {
  const data = error?.response?.data;
  if (!data) return error?.message || fallback;
  if (typeof data === 'string') return data;
  if (typeof data.detail === 'string') return data.detail;
  if (Array.isArray(data.non_field_errors) && data.non_field_errors.length > 0) {
    return data.non_field_errors[0];
  }
  const firstFieldError = Object.values(data).find(
    (value) => Array.isArray(value) && value.length > 0 && typeof value[0] === 'string'
  );
  if (firstFieldError) return firstFieldError[0];
  return fallback;
}
