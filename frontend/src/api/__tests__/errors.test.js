import { describe, expect, it } from 'vitest';
import { extractErrorMessage } from '../errors';

// extractErrorMessage is the one place every DRF error shape this API can
// produce gets turned into a string (see the module's own comment). Each
// case here is a real shape a real view in this backend actually returns,
// not a hypothetical.
describe('extractErrorMessage', () => {
  it('falls back to the network error message when there is no response at all', () => {
    expect(extractErrorMessage({ message: 'Network Error' })).toBe('Network Error');
  });

  it('falls back to the given default when there is nothing usable at all', () => {
    expect(extractErrorMessage({}, 'default text')).toBe('default text');
  });

  it('reads a permission/auth failure shape: {"detail": "..."}', () => {
    const error = { response: { data: { detail: 'Authentication credentials were not provided.' } } };
    expect(extractErrorMessage(error)).toBe('Authentication credentials were not provided.');
  });

  it('reads a whole-serializer validation error: {"non_field_errors": [...]}', () => {
    const error = { response: { data: { non_field_errors: ['Invalid email or password.'] } } };
    expect(extractErrorMessage(error)).toBe('Invalid email or password.');
  });

  it('reads a field-level validation error: {"<field>": [...]}', () => {
    const error = { response: { data: { current_password: ['Current password is incorrect.'] } } };
    expect(extractErrorMessage(error)).toBe('Current password is incorrect.');
  });

  // corpus.views.ResourceFileViewSet.perform_destroy raises
  // ValidationError(f"'{name}' is still referenced by: ...") - a PLAIN
  // STRING, not a dict. DRF's ValidationError still coerces that into a
  // list internally, and the exception handler returns that list as the
  // ENTIRE response body (verified directly against
  // rest_framework.views.exception_handler's source: `isinstance(exc.detail,
  // (list, dict))` short-circuits straight to `data = exc.detail`, so the
  // body is `["'oldsmar-scenarios' is still referenced by: ..."]`, never
  // `{"detail": "..."}`). Before the fix this landed on the generic
  // fallback instead of the server's real refusal message.
  it('reads a bare top-level list of strings - a plain-string ValidationError, not a dict', () => {
    const error = {
      response: {
        data: ["'oldsmar-scenarios' is still referenced by: oldsmar. Remove or repoint those first."],
      },
    };
    expect(extractErrorMessage(error)).toBe(
      "'oldsmar-scenarios' is still referenced by: oldsmar. Remove or repoint those first."
    );
  });

  it('falls back to the default for an empty list, rather than crashing', () => {
    const error = { response: { data: [] } };
    expect(extractErrorMessage(error, 'default text')).toBe('default text');
  });
});
