from rest_framework.permissions import SAFE_METHODS, BasePermission


class StaffWrites(BasePermission):
    """Anyone signed in may read; only staff may write.

    Shared by every viewset that opens reads to any authenticated user but
    reserves creation/mutation for staff. Subclass and override `message`
    for endpoint-specific wording — the check itself never varies, and
    duplicating it per app was already how two copies of the same rule
    drifted apart.
    """

    message = "Only staff may perform this action."

    def has_permission(self, request, view):
        return request.method in SAFE_METHODS or bool(request.user.is_staff)
