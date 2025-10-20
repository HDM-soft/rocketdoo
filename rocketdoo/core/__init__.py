"""
🚀 Rocketdoo - Odoo Development Framework
"""

from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("rocketdoo")
except PackageNotFoundError:
    # Fallback solo para desarrollo local (pip install -e .)
    __version__ = "dev"