"""
🚀 Rocketdoo - Odoo Development Framework
"""

from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("rocketdoo")
except PackageNotFoundError:
    # Fallback for local development (pip install -e .)
    __version__ = "dev"