"""jevreel: zero-embedding video intelligence with contact sheets and Jev typed decisions."""

from importlib.metadata import PackageNotFoundError, version

from jevreel.config import Settings
from jevreel.reel import Reel

try:
    __version__ = version("jevreel")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.0.0"

__all__ = ["Reel", "Settings", "__version__"]
