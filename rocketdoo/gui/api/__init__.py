from fastapi import APIRouter
from .project import router as project_router
from .docker_ops import router as docker_router
from .modules import router as modules_router
from .mail import router as mail_router
from .traefik_ops import router as traefik_router
from .instances import router as instances_router

router = APIRouter()
router.include_router(project_router, prefix="/project", tags=["project"])
router.include_router(docker_router, prefix="/docker", tags=["docker"])
router.include_router(modules_router, prefix="/modules", tags=["modules"])
router.include_router(mail_router, prefix="/mail", tags=["mail"])
router.include_router(traefik_router, prefix="/traefik", tags=["traefik"])
router.include_router(instances_router, prefix="/instances", tags=["instances"])
