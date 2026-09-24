import shutil
import tempfile

from django.test import TestCase, override_settings


class MediaIsolatedTestCase(TestCase):
    """
    TestCase that points MEDIA_ROOT at a throwaway folder for the whole
    class and deletes it afterwards. Django only rolls back the database
    between tests, not file storage - without this, every test that saves
    a FileField would leave files behind in the real media folder.
    """

    @classmethod
    def setUpClass(cls):
        cls._media_root = tempfile.mkdtemp(prefix="test_media_")
        cls._media_override = override_settings(MEDIA_ROOT=cls._media_root)
        cls._media_override.enable()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls._media_override.disable()
        shutil.rmtree(cls._media_root, ignore_errors=True)
