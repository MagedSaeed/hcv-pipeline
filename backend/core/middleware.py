from django.http import JsonResponse

# What an analyst may reach: Analyses and what its pages need. Everything else is refused,
# so an endpoint added later stays closed to analysts until it is listed here.
ANALYST_PATHS = ("/api/analyses/", "/api/auth/", "/api/models/", "/api/health/")
ANALYST_READS = ("/api/taxonomies/",)


class AnalystScope:
    """Keeps analyst accounts to Analyses: the Experiment Lab and the admin are refused."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        analyst = user is not None and user.is_authenticated and getattr(user, "is_analyst", False)
        if analyst and not self._allowed(request):
            return JsonResponse({"detail": "Analyst accounts can only use Analyses."}, status=403)
        return self.get_response(request)

    @staticmethod
    def _allowed(request):
        path = request.path
        if not path.startswith(("/api/", "/admin/")):
            return True
        if path.startswith(ANALYST_PATHS):
            return True
        return request.method == "GET" and path in ANALYST_READS
