import os
from pathlib import Path
import sys


def _whatsapp_wsgi_command(port: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        "gunicorn",
        "--bind",
        f"0.0.0.0:{port}",
        "--workers",
        "1",
        "--threads",
        "8",
        "--access-logfile",
        "-",
        "--error-logfile",
        "-",
        "gis_agent.whatsapp:app",
    ]


def main() -> None:
    service = os.getenv("APP_SERVICE", "chainlit").strip().lower() or "chainlit"

    if service == "chainlit":
        command = [
            sys.executable,
            "-m",
            "chainlit",
            "run",
            "src/gis_agent/main.py",
            "--host",
            "0.0.0.0",
            "--port",
            os.getenv("PORT", "8000"),
        ]
        os.execv(sys.executable, command)
        return

    if service == "whatsapp":
        package_directory = str(Path(__file__).resolve().parent)
        if package_directory not in sys.path:
            sys.path.insert(0, package_directory)

        port = os.getenv("PORT", "5000")
        if os.name == "nt":
            from whatsapp import run

            run()
            return

        command = _whatsapp_wsgi_command(port)
        os.execv(sys.executable, command)
        return

    raise SystemExit("APP_SERVICE must be either 'chainlit' or 'whatsapp'.")


if __name__ == "__main__":
    main()
