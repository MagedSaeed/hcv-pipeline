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
  // A DRF ValidationError raised with a bare string (rather than a dict) -
  // e.g. corpus.views.ResourceFileViewSet.perform_destroy's protected-delete
  // refusal - is still coerced into a list by ValidationError.__init__, and
  // DRF's exception_handler special-cases list/dict `detail` to become the
  // ENTIRE response body verbatim (rest_framework/views.py:
  // `data = exc.detail if isinstance(exc.detail, (list, dict)) else
  // {'detail': exc.detail}`), never wrapped in {"detail": ...}. Checked
  // before the object-shape checks below: `data.detail` on a bare array is
  // simply `undefined` (not a crash), but would otherwise fall through
  // silently to the generic fallback and hide the server's real refusal
  // message - exactly the case task 3's "deleting a referenced resource"
  // acceptance criterion exercises.
  if (Array.isArray(data) && data.length > 0 && typeof data[0] === 'string') {
    return data[0];
  }
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
