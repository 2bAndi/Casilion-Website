"""The job API: the only door between the application zone and the AI enclave.

The enclave opens every connection (pull). Nothing here calls the enclave.

    POST /internal/jobs/claim           -> 200 job payload | 204 nothing to do
    POST /internal/jobs/<id>/heartbeat  -> extends the lease
    POST /internal/jobs/<id>/result     -> stores finding, draft and guard report
    POST /internal/jobs/<id>/fail       -> gives the job up; a person takes over
"""
import hmac
import json
from functools import wraps

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import path
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import services
from .models import AIJob


def worker_only(view):
    @wraps(view)
    @csrf_exempt
    @require_POST
    def wrapped(request, *args, **kwargs):
        expected = settings.CASSILION["AI_WORKER_TOKEN"]
        scheme, _, token = (request.headers.get("Authorization") or "").partition(" ")
        if not expected or scheme.lower() != "bearer" or not hmac.compare_digest(token.strip(), expected):
            return JsonResponse({"error": "unauthorized"}, status=401)
        try:
            request.json = json.loads(request.body or b"{}")
        except ValueError:
            return JsonResponse({"error": "body is not JSON"}, status=400)
        if not isinstance(request.json, dict):
            return JsonResponse({"error": "body is not an object"}, status=400)
        return view(request, *args, **kwargs)

    return wrapped


@worker_only
def claim(request):
    worker_id = str(request.json.get("worker_id") or "worker")
    services.worker_seen(worker_id)
    job = services.claim_job(worker_id)
    if job is None:
        return HttpResponse(status=204)
    return JsonResponse(services.build_payload(job))


@worker_only
def heartbeat(request, job_id):
    job = get_object_or_404(AIJob, pk=job_id, status=AIJob.Status.CLAIMED)
    services.heartbeat(job)
    return JsonResponse({"ok": True})


@worker_only
def result(request, job_id):
    job = get_object_or_404(AIJob.objects.select_related("ticket"), pk=job_id)
    try:
        services.submit_result(job, request.json)
    except services.NotAllowed as exc:
        return JsonResponse({"error": str(exc)}, status=409)
    return JsonResponse({"ok": True, "ticket_status": job.ticket.status})


@worker_only
def fail(request, job_id):
    job = get_object_or_404(AIJob.objects.select_related("ticket"), pk=job_id)
    services.fail_job(job, str(request.json.get("reason") or ""))
    return JsonResponse({"ok": True})


app_name = "jobs"

urlpatterns = [
    path("claim", claim, name="claim"),
    path("<int:job_id>/heartbeat", heartbeat, name="heartbeat"),
    path("<int:job_id>/result", result, name="result"),
    path("<int:job_id>/fail", fail, name="fail"),
]
