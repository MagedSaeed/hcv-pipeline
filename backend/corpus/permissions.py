from rest_framework.permissions import SAFE_METHODS, BasePermission


class ReadOnlyIfGenerated(BasePermission):
    """Generated rows are experimental records, never edited or cherry-picked.

    Removing generated material is done by deleting the run that produced it,
    which cascades. Deleting one row at a time would be selective reporting.
    """

    message = (
        "Generated material is immutable. Change the prompt or inputs and "
        "generate again, or delete the run that produced it."
    )

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        return obj.is_editable
