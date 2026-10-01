"""maigret-web engine: username / person search across thousands of sites."""

__title__ = 'maigret-web'
__package__ = 'maigret'

from .__version__ import __version__
from .checking import maigret as search
from .sites import MaigretEngine, MaigretSite, MaigretDatabase
