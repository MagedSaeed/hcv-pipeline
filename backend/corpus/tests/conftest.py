import pytest


@pytest.fixture(autouse=True)
def _isolated_media_root(settings, tmp_path):
    """Uploads in this app's tests write to a throwaway directory instead of
    the real MEDIA_ROOT, so runs stay isolated and leave nothing behind.

    Scoped to `corpus/tests` rather than the project-wide conftest because
    `corpus` is currently the only app that touches file storage; promote
    this to `backend/conftest.py` if another app starts uploading files.
    """
    settings.MEDIA_ROOT = tmp_path
