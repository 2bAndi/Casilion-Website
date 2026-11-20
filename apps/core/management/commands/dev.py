"""One command for local development: database, demo data, web server with MCP, AI worker."""
import os
import subprocess
import sys

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand

from apps.accounts.models import User


class Command(BaseCommand):
    help = "Migrate, seed demo data on first run, then start the web server (with MCP) and the AI worker."

    def add_arguments(self, parser):
        parser.add_argument("--port", type=int, default=8000)
        parser.add_argument("--host", default="127.0.0.1")
        parser.add_argument("--no-worker", action="store_true", help="Do not start the AI worker.")
        parser.add_argument("--no-reload", action="store_true", help="Do not restart the web server on code changes.")

    def handle(self, *args, **options):
        call_command("migrate", interactive=False, verbosity=0)
        if settings.DEBUG and not User.objects.exists():
            call_command("seed_demo", tickets=True)

        env = dict(os.environ)
        env.setdefault("CASSILION_JOB_API", f"http://{options['host']}:{options['port']}")
        env.setdefault("AI_WORKER_TOKEN", settings.CASSILION["AI_WORKER_TOKEN"])
        env.setdefault("PYTHONUNBUFFERED", "1")
        env.setdefault("PYTHONUTF8", "1")  # mails and logs go to the console; keep them UTF-8 on Windows too

        web = [sys.executable, "-m", "uvicorn", "config.asgi:application", "--host", options["host"], "--port", str(options["port"])]
        if not options["no_reload"]:
            web += ["--reload", "--reload-dir", "apps", "--reload-dir", "config", "--reload-dir", "shared", "--reload-dir", "templates"]
        processes = [subprocess.Popen(web, cwd=settings.BASE_DIR, env=env)]
        if not options["no_worker"]:
            processes.append(subprocess.Popen([sys.executable, "-m", "ai_worker"], cwd=settings.BASE_DIR, env=env))

        self.stdout.write(self.style.SUCCESS(f"\nCassilion is starting on http://{options['host']}:{options['port']}  (Ctrl+C stops it)\n"))
        try:
            processes[0].wait()
        except KeyboardInterrupt:
            pass
        finally:
            for process in processes:
                if process.poll() is None:
                    process.terminate()
            for process in processes:
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
