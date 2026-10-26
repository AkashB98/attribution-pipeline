"""No-network enforcement: with sockets blocked, generating, stitching,
attributing, reporting and validating must all still work. (``serve``
is the only intentional exception — it binds loopback on demand, and is
not used here.)"""

import os
import socket
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _Blocker:
    def __init__(self, *a, **k):
        raise RuntimeError("network access blocked in hermetic test")


class TestNoNetwork(unittest.TestCase):
    def setUp(self):
        self._real_socket = socket.socket
        socket.socket = _Blocker
        import urllib.request as urlreq
        self._real_urlopen = urlreq.urlopen
        urlreq.urlopen = lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("network access blocked in hermetic test"))

    def tearDown(self):
        socket.socket = self._real_socket
        import urllib.request as urlreq
        urlreq.urlopen = self._real_urlopen

    def test_full_pipeline_offline(self):
        from generator import generate_dataset
        from models import MODELS, attribute_all
        from report import build_report
        from stitching import build_journeys
        from validation import validate_dataset
        ds = generate_dataset(users=100, seed=42)
        self.assertEqual(validate_dataset(ds), [])
        journeys = build_journeys(ds["events"], ds["conversions"])
        for model in MODELS:
            self.assertTrue(attribute_all(journeys, model))
        self.assertEqual(build_report(journeys, ds["spend"])["totals"]["conversions"],
                         len(ds["conversions"]))

    def test_no_module_opens_sockets_at_import(self):
        for mod in ("generator", "stitching", "models", "report",
                    "validation", "cli", "demo"):
            sys.modules.pop(mod, None)
        import cli, demo, generator, models, report, stitching, validation  # noqa: F401
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
