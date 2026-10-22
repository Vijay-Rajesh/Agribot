import os
import sys
import types
import unittest
from unittest.mock import patch

from container import _whatsapp_wsgi_command, main


class ContainerServiceTests(unittest.TestCase):
    def test_defaults_to_chainlit_and_uses_platform_port(self):
        with (
            patch.dict(os.environ, {"PORT": "8123"}, clear=True),
            patch("container.os.execv") as execv,
        ):
            main()

        command = execv.call_args.args[1]
        self.assertEqual(command[-2:], ["--port", "8123"])
        self.assertIn("src/gis_agent/main.py", command)

    def test_starts_whatsapp_service(self):
        whatsapp = types.ModuleType("whatsapp")
        whatsapp.run = unittest.mock.Mock()
        with (
            patch.dict(os.environ, {"APP_SERVICE": "whatsapp"}, clear=True),
            patch.dict(sys.modules, {"whatsapp": whatsapp}),
        ):
            main()

        whatsapp.run.assert_called_once_with()

    def test_whatsapp_uses_production_wsgi_server_and_platform_port(self):
        command = _whatsapp_wsgi_command("5234")

        self.assertEqual(command[1:4], ["-m", "gunicorn", "--bind"])
        self.assertIn("0.0.0.0:5234", command)
        self.assertIn("gis_agent.whatsapp:app", command)

    def test_rejects_unknown_service(self):
        with patch.dict(os.environ, {"APP_SERVICE": "unknown"}, clear=True):
            with self.assertRaisesRegex(SystemExit, "APP_SERVICE must be"):
                main()


if __name__ == "__main__":
    unittest.main()
