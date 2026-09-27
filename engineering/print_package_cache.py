"""Content-keyed prepared-plate cache, preserving the historical packager API."""
import sys
import zlib
from pathlib import Path
from engineering import artifact_cache as C, print_package as P

PROJECT_CACHE = C.default_cache()

def project_bytes(parts, settings, title):
    names = [p["name"] for p in parts]
    if len(names) != len(set(names)) or not names:
        raise ValueError("PART: duplicate/empty list")
    P.check_bed(parts, settings)
    inputs = dict(parts=parts, settings=settings, title=title, python=sys.version,
                  zlib=zlib.ZLIB_RUNTIME_VERSION,
                  producer=C.digest(Path(__file__).read_bytes()),
                  packager=C.digest(Path(P.__file__).read_bytes()))
    return PROJECT_CACHE.get("prepared-print-plate", inputs,
                             lambda: P.project_bytes(parts, settings, title))
