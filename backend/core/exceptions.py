"""The project's DRF exception handler: a machine-readable `code` on every
non-validation error body (task 10.6).

DRF renders an `APIException` as `{"detail": <message>}` and nothing else.
The exception's own code (`not_authenticated`, `permission_denied`,
`not_found`, ...) is carried on that `detail` — an `ErrorDetail`, a `str`
subclass — but never reaches the body. So the frontend recognised an expired
session by matching DRF's ENGLISH `detail`, which breaks the moment that
wording changes or is translated. The 503s (`missing_api_key`,
`broker_unavailable`) and the 409s (`run_in_flight`, ...) already carried a
`code`; this extends the same contract to every other error DRF raises.

Two rules keep it from changing any response that already works:

- a body that already carries a `code` is left exactly as it is — the 409s'
  `ConflictCodeMixin` writes the same `exc.detail.code` this would, and the
  503s are built by hand and never reach an exception handler at all;
- a `ValidationError` body is never touched. Its keys are FIELD names
  (`{"phase2_prompt_a": [...]}`), so a top-level `code` would read as one
  more field error to anything rendering them — even for the views that
  raise it as `{"detail": ...}`, which is the same shape to a reader.
"""

from rest_framework.exceptions import ValidationError
from rest_framework.views import exception_handler as drf_exception_handler


def exception_handler(exc, context):
    """DRF's own handler, plus the `detail`'s own `code` where the body lacks one."""
    response = drf_exception_handler(exc, context)
    if response is None or isinstance(exc, ValidationError):
        return response
    data = response.data
    if isinstance(data, dict) and "code" not in data:
        # Read off the body's `detail` rather than `exc`: DRF's handler
        # converts Django's `Http404`/`PermissionDenied` into its own
        # exceptions internally, so only the rendered `detail` is certain to
        # be an `ErrorDetail` carrying a code.
        code = getattr(data.get("detail"), "code", None)
        if code:
            data["code"] = code
    return response
