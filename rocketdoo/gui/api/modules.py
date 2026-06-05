from pathlib import Path
from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("")
async def list_modules(path: str = "addons", include_all: bool = False):
    """Scan and return Odoo modules in the addons directory."""
    try:
        from rocketdoo.core.module_scanner import ModuleScanner
        scanner = ModuleScanner(base_path=Path(path))
        modules = scanner.scan()

        result = []
        for m in modules:
            if not include_all and not m.is_installable:
                continue
            result.append({
                "name": m.name,
                "path": str(m.relative_path),
                "version": m.version or "",
                "installable": m.is_installable,
                "depends": m.depends or [],
                "has_invalid_name": m.has_invalid_name,
            })
        return {"modules": result, "total": len(result)}
    except Exception as e:
        return JSONResponse({"modules": [], "total": 0, "error": str(e)}, status_code=200)
