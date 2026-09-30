"""
Shared base class for tests that write files.

Django's TestCase rolls back the database between tests but NOT file
storage, so anything that saves an uploaded invoice would leave real files
in MEDIA_ROOT. This base class points MEDIA_ROOT at a temporary folder for
the whole test class and deletes it afterwards.
"""

import shutil
import tempfile

from django.test import TestCase, override_settings


class MediaIsolatedTestCase(TestCase):
    """A TestCase whose file writes go to a throw-away MEDIA_ROOT."""

    @classmethod
    def setUpClass(cls):
        """Create the temp folder and redirect MEDIA_ROOT to it before any test runs."""
        super().setUpClass()
        cls._media_root = tempfile.mkdtemp(prefix="invoice_data_test_media_")
        cls._media_override = override_settings(MEDIA_ROOT=cls._media_root)
        cls._media_override.enable()

    @classmethod
    def tearDownClass(cls):
        """Restore MEDIA_ROOT and delete the temp folder."""
        cls._media_override.disable()
        shutil.rmtree(cls._media_root, ignore_errors=True)
        super().tearDownClass()
