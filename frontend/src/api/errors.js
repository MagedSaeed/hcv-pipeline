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
// The keys a DRF error body can carry that are NOT about one specific
// field - `detail` (a permission/auth failure, or a plain-string
// ValidationError DRF coerced into `{"detail": ...}`), `non_field_errors`
// (a whole-serializer ValidationError, e.g. Experiment.clean() raising
// against no field in particular), and `code` (the machine-readable
// 409/503 marker this API attaches - see pipeline/views.py's
// ConflictCodeMixin/_service_unavailable). Everything else in the body is
// a field name from the serializer that was actually being validated.
// Declared before `extractErrorMessage` below (not just `extractFieldErrors`
// further down) so that function can also skip these when scanning for a
// field-level error to show, rather than ever reading `code`'s own short
// machine token (e.g. "missing_api_key") as if it were human-readable
// error text.
const NON_FIELD_KEYS = new Set(['detail', 'non_field_errors', 'code']);

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
  // A field-level ValidationError raised DIRECTLY (not through a
  // serializer) with a bare string value - e.g. `prompts/views.py::
  // PromptPreviewMixin._resolve_template` raising
  // `ValidationError({"user_prompt": "Previewing a template with no id
  // requires user_prompt."})`, or `pipeline/views.py::_export_format`
  // raising `ValidationError({"format": "Use format=json or format=csv."})`.
  // DRF's `ValidationError.__init__` only coerces a LIST value's own
  // entries into `ErrorDetail`s; a bare string value is coerced into a
  // single `ErrorDetail` (a str subclass), never wrapped in a list - so the
  // response body is `{"user_prompt": "..."}`, not `{"user_prompt":
  // ["..."]}`. The array-shaped check below alone missed this entirely and
  // fell through to the generic fallback, hiding the server's real
  // refusal - `extractFieldErrors` (below) already handled this shape
  // correctly via `joinMessages`; this is the same fix for the single
  // human-readable string this function returns.
  for (const [key, value] of Object.entries(data)) {
    if (NON_FIELD_KEYS.has(key)) continue;
    if (Array.isArray(value) && value.length > 0 && typeof value[0] === 'string') return value[0];
    if (typeof value === 'string' && value) return value;
  }
  return fallback;
}

function joinMessages(value) {
  if (Array.isArray(value)) {
    return value.map((item) => (typeof item === 'string' ? item : JSON.stringify(item))).join(' ');
  }
  if (typeof value === 'string') return value;
  return JSON.stringify(value);
}

// Turns a DRF ValidationError body into a {fieldName: message} map, so a
// form can place the backend's own wording next to the field it actually
// refused - Experiment.clean() (backend/pipeline/models.py) raises exactly
// this shape (`{"seed_systems": [...], "generate_systems": [...], ...}`,
// via Django's `ValidationError.message_dict`), and ExperimentSerializer
// re-raises it unchanged (`pipeline/serializers.py`). A value that is a
// list of strings (the common case) is joined into one string; a value
// that is itself a list of objects (e.g. `variants`' own list-of-strings
// errors) still joins cleanly since every entry here is already a string.
//
// Deliberately ignores `detail`/`non_field_errors`/`code` - those are what
// `hasNonFieldError` below is for, so a caller renders EITHER the specific
// field errors this returns OR a fallback banner for what's left, never
// both saying the same thing twice.
export function extractFieldErrors(error) {
  const data = error?.response?.data;
  if (!data || typeof data !== 'object' || Array.isArray(data)) return {};
  const fieldErrors = {};
  for (const [key, value] of Object.entries(data)) {
    if (NON_FIELD_KEYS.has(key)) continue;
    fieldErrors[key] = joinMessages(value);
  }
  return fieldErrors;
}

// Whether anything is left for a generic ErrorBanner to say once every
// field-specific error above has already been placed next to its own
// field - a network failure or an unshaped body (no `response.data` at
// all) always has something to show; a shaped body only does when it
// carries `detail` or `non_field_errors`. This is what keeps a page from
// showing the SAME message twice - once next to the field, once in a
// banner that duplicates it - when the backend response was entirely
// field-shaped errors.
export function hasNonFieldError(error) {
  const data = error?.response?.data;
  if (!data) return true;
  if (typeof data === 'string') return true;
  if (Array.isArray(data)) return true;
  return (
    typeof data.detail === 'string' ||
    (Array.isArray(data.non_field_errors) && data.non_field_errors.length > 0)
  );
}
