"""Register version 1 controllers in one place."""

from fastapi import APIRouter

from ...controller import automation, capture, credentials, cv, jobs, profile, session, tracker

router = APIRouter()
for controller in (session, jobs, cv, profile, automation, tracker, capture, credentials):
    router.include_router(controller.router)
