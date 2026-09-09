"""The OpenRouter model catalogue, cached so a dropdown does not hammer the API."""

import json

from django.core.cache import cache
from openai import OpenAI
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from core.keys import MissingAPIKey, resolve_openrouter_key
from llm.client import OPENROUTER_BASE_URL

MODEL_CACHE_KEY = "openrouter:models"
MODEL_CACHE_SECONDS = 3600


def _fetch_from_openrouter(api_key):
    """Read the catalogue through the SDK's raw-response accessor.

    Raw rather than parsed because OpenRouter adds fields the OpenAI model type
    does not declare — pricing and context_length among them — and those are
    exactly what a model picker needs to show.
    """
    client = OpenAI(api_key=api_key, base_url=OPENROUTER_BASE_URL)
    return json.loads(client.models.with_raw_response.list().text)


def fetch_models(fetcher=None):
    """Return the catalogue, cached for an hour.

    A failed fetch is allowed to propagate and is never cached — a transient
    outage must not blank the model list for the next hour. An EMPTY result is
    treated the same way: a degraded reply carrying no models and no error would
    otherwise be cached, masking the provider's recovery for the full hour, for
    the same reason a failure is not cached.

    A payload that is not a JSON object at all (a bare list, a string, an error
    page decoded as something else) degrades to no rows rather than raising an
    AttributeError out of a view — the same treatment the call client gives a
    malformed body.
    """
    cached = cache.get(MODEL_CACHE_KEY)
    if cached is not None:
        return cached

    payload = (fetcher or _fetch_from_openrouter)(resolve_openrouter_key())
    rows = payload.get("data", []) if isinstance(payload, dict) else []
    if rows:
        cache.set(MODEL_CACHE_KEY, rows, MODEL_CACHE_SECONDS)
    return rows


class AvailableModelsView(APIView):
    def get(self, request):
        try:
            rows = fetch_models()
        except MissingAPIKey as error:
            return Response({"detail": str(error)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except Exception as error:  # noqa: BLE001 — an upstream failure is a 502, not a 500
            # A timeout, a DNS failure, a 500 from OpenRouter: all of them are
            # the upstream being unavailable, which is a 502 naming the cause,
            # not an unhandled crash that shows the caller a bare 500.
            return Response(
                {"detail": f"Could not reach the model catalogue: {error}"},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        return Response({"count": len(rows), "results": rows})
