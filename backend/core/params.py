from rest_framework.exceptions import ValidationError


def integer_param(params, name):
    """Read a query parameter that filters on an integer column, or raise a 400.

    An uncoerced value reaches the ORM and raises ValueError/TypeError, which
    DRF does not map to a response: a typo in a query string would be a 500
    rather than a 400.

    Shared by every app with an integer-keyed list filter (`corpus`,
    `pipeline`) so the coercion — and its error shape — cannot drift apart
    the way two independent copies of this already had.
    """
    raw = params.get(name)
    if raw in (None, ""):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError) as error:
        raise ValidationError({name: f"Must be an integer, got {raw!r}."}) from error
