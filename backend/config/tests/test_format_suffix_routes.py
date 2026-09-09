"""Every `@action` needs a `format=None` parameter.

`DefaultRouter.include_format_suffixes` (on by default) makes it generate a
format-SUFFIX variant of every route it registers — `.../export.json`,
`.../run.csv` — which passes `format` as a URL kwarg to the view. DRF's
built-in `list`/`create`/`retrieve`/`update`/`partial_update`/`destroy`
methods all accept `**kwargs` and swallow it harmlessly; a custom `@action`
whose signature only declares `(self, request, pk=None)` does not, and
`handler(request, **kwargs)` inside `APIView.dispatch()` then raises
`TypeError: <name>() got an unexpected keyword argument 'format'` — an
UNCAUGHT exception, surfacing as a plain 500. A plain, non-suffixed request
never exercises this at all, which is exactly how `export`, `progress`,
`run`, `calls`, `retry`, `activate`, `preview` and `hcvs` all went unnoticed
until a review round found three of them and asked for the rest to be swept.

This walks every router registered under `/api/` (not just the ones that
happened to have the bug already) and hits every format-suffix route it
generates, so an action added later without `format=None` fails here
automatically instead of needing to be remembered one action at a time.
"""

import re

import pytest
from django.contrib.auth import get_user_model

from corpus.urls import router as corpus_router
from pipeline.urls import router as pipeline_router
from prompts.urls import router as prompts_router
from taxonomy.urls import router as taxonomy_router

User = get_user_model()

# Every DefaultRouter registered under `/api/` (see `config/urls.py`). A
# router added for a new app later is invisible here until it's added to
# this tuple too — there is no way to discover "every router in the
# project" short of walking `config.urls`, which is not worth the added
# indirection for four routers.
ROUTERS = (corpus_router, pipeline_router, prompts_router, taxonomy_router)

_PK_GROUP = re.compile(r"\(\?P<pk>[^)]*\)")
_FORMAT_GROUP = re.compile(r"\(\?P<format>[^)]*\)")


def _concrete_path(route, pk, fmt):
    """Turn one router-generated regex route back into a literal, requestable path.

    `route` looks like `'^experiments/(?P<pk>[^/.]+)/export\\.(?P<format>[a-z0-9]+)/?$'`
    — substituting the two named groups and stripping the regex anchors/
    escaping turns it back into `'experiments/1/export.json'`. Works
    equally for list-level routes with no `pk` group at all (the
    substitution is a no-op when the group is absent).
    """
    raw = route.lstrip("^").rstrip("$")
    raw = _PK_GROUP.sub(str(pk), raw)
    raw = _FORMAT_GROUP.sub(fmt, raw)
    raw = raw.replace("\\.", ".").rstrip("/?")
    return f"/api/{raw}"


def _format_suffix_routes():
    """(path, http_method) for every format-suffix route DRF's routers built.

    Every registered method of every such route is included, not just GET —
    `run`, `retry`, `activate` and `preview` are all POST-only, and a sweep
    that only ever issued GET would never have caught them.
    """
    seen = []
    for router in ROUTERS:
        for pattern in router.urls:
            route = str(pattern.pattern)
            if "(?P<format>" not in route:
                # Excludes the plain (non-suffixed) sibling routes and the
                # router's own API-root format pattern, which uses a path
                # converter (`<drf_format_suffix:format>`) rather than a
                # named regex group and names no actual view.
                continue
            actions = getattr(pattern.callback, "actions", None)
            if not actions:
                continue
            for fmt in ("json", "csv"):
                path = _concrete_path(route, pk=1, fmt=fmt)
                for method in actions:
                    seen.append(pytest.param(path, method, id=f"{method.upper()}:{path}"))
    return seen


@pytest.fixture
def staff(db):
    """Staff, not a plain member: `StaffWrites` would otherwise turn every
    unsafe method into a 403 before the view body — and by the time
    `check_permissions` runs, the `format` kwarg has ALREADY been handed to
    the handler method inside `dispatch()`'s try/except, so permission
    denial would mask the very crash this test exists to catch just as
    effectively as a fixed action would. Using staff throughout keeps every
    failure mode here attributable to the format kwarg, not to who is asking.
    """
    return User.objects.create_user(email="sweep@example.com", password="pw-123456", is_staff=True)


@pytest.mark.django_db
@pytest.mark.parametrize("path, method", _format_suffix_routes())
def test_every_format_suffix_route_resolves_without_a_type_error(api_client, staff, path, method):
    """`pk=1` need not exist: the `TypeError` this guards against happens the
    instant `dispatch()` calls the handler with the extra `format` kwarg —
    before `get_object()` (or any of the view's own logic) ever runs. So
    every OTHER outcome here (200, 400, 403, 404, 405, ...) means the
    action's signature accepted the call; what it goes on to do with a
    nonexistent object is each endpoint's own test's job, not this one's.
    """
    api_client.force_authenticate(user=staff)
    response = getattr(api_client, method)(path)
    assert response.status_code != 500, (
        f"{method.upper()} {path} returned 500 — almost certainly a missing "
        f"`format=None` parameter on the action this route maps to."
    )
