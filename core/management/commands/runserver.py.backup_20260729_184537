import os
import shutil
import socket
import subprocess
import time

try:
    from django.contrib.staticfiles.management.commands.runserver import (
        Command as StaticFilesRunserverCommand,
    )
except ImportError:  # pragma: no cover - staticfiles is installed in this project.
    from django.core.management.commands.runserver import (
        Command as StaticFilesRunserverCommand,
    )


class Command(StaticFilesRunserverCommand):
    help = (
        "Starts the development server after making sure the local Docker "
        "database container is running."
    )

    def handle(self, *args, **options):
        self._ensure_docker_database()
        super().handle(*args, **options)

    def _ensure_docker_database(self):
        if os.getenv("TMS_DB_DOCKER_AUTOSTART", "1").lower() in {
            "0",
            "false",
            "no",
            "off",
        }:
            return

        container_name = os.getenv("TMS_DB_DOCKER_CONTAINER", "tms_db")
        db_host = os.getenv("TMS_DB_HOST", "127.0.0.1")
        db_port = int(os.getenv("TMS_DB_PORT", "6604"))
        wait_seconds = int(os.getenv("TMS_DB_DOCKER_WAIT_SECONDS", "45"))

        if shutil.which("docker") is None:
            self.stderr.write(
                self.style.WARNING(
                    "Docker was not found in PATH; skipping database container check."
                )
            )
            return

        state = self._docker_inspect(container_name, "{{.State.Status}}")
        if state is None:
            self.stderr.write(
                self.style.WARNING(
                    f"Docker container '{container_name}' was not found; "
                    "skipping database container start."
                )
            )
            return

        health = self._docker_inspect(container_name, "{{if .State.Health}}{{.State.Health.Status}}{{end}}")

        if state != "running":
            self.stdout.write(f"Starting Docker database container '{container_name}'...")
            if not self._run_docker(["start", container_name]):
                return
        elif health == "unhealthy":
            self.stdout.write(
                f"Restarting unhealthy Docker database container '{container_name}'..."
            )
            if not self._run_docker(["restart", container_name]):
                return
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Docker database container '{container_name}' is already running."
                )
            )

        if self._wait_for_port(db_host, db_port, wait_seconds):
            self.stdout.write(
                self.style.SUCCESS(
                    f"Database is reachable at {db_host}:{db_port}."
                )
            )
        else:
            self.stderr.write(
                self.style.WARNING(
                    f"Database container started, but {db_host}:{db_port} "
                    f"did not accept connections within {wait_seconds} seconds."
                )
            )

    def _docker_inspect(self, container_name, template):
        result = subprocess.run(
            ["docker", "inspect", "-f", template, container_name],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip()

    def _run_docker(self, docker_args):
        result = subprocess.run(
            ["docker", *docker_args],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return True

        message = result.stderr.strip() or result.stdout.strip()
        self.stderr.write(
            self.style.ERROR(f"Docker command failed: docker {' '.join(docker_args)}")
        )
        if message:
            self.stderr.write(message)
        return False

    def _wait_for_port(self, host, port, wait_seconds):
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            try:
                with socket.create_connection((host, port), timeout=1):
                    return True
            except OSError:
                time.sleep(1)
        return False
