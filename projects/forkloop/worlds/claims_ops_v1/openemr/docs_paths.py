"""Where OpenEMR patient documents live in the VM (contract §8).

    documents are written to   /var/www/openemr/sites/default/documents/<pid>/<name>
    documents.url is           file:///var/www/openemr/sites/default/documents/<pid>/<name>

OpenEMR's ``Document`` class resolves ``file://`` URLs relative to the site's
documents directory using ``documents.path_depth`` (default 1 = ``<pid>/<name>``),
so both forms must agree byte-for-byte with what the seeding SQL writes.
"""

from __future__ import annotations

import posixpath
import re

__all__ = [
    "OPENEMR_ROOT",
    "SITE",
    "DOCUMENTS_ROOT",
    "document_dir",
    "document_fs_path",
    "document_url",
    "validate_document_name",
]

OPENEMR_ROOT = "/var/www/openemr"
SITE = "default"
DOCUMENTS_ROOT = f"{OPENEMR_ROOT}/sites/{SITE}/documents"

# Conservative: letters, digits, dot, dash, underscore, space; must not start
# with a dot.  Keeps the name safe in a shell path, a SQL literal and a URL.
_NAME_RE = re.compile(r"^[A-Za-z0-9_\-][A-Za-z0-9_\- .]{0,199}$")


def validate_document_name(name: str) -> str:
    """Return ``name`` if it is a safe single path component, else raise."""
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise ValueError(f"unsafe document name {name!r}")
    if "/" in name or name in (".", "..") or ".." in name.split("."):
        raise ValueError(f"unsafe document name {name!r}")
    return name


def document_dir(pid: int) -> str:
    """Per-patient directory: ``/var/www/openemr/sites/default/documents/<pid>``."""
    return posixpath.join(DOCUMENTS_ROOT, str(int(pid)))


def document_fs_path(pid: int, name: str) -> str:
    """Absolute VM path where the file bytes are written (``SeedFile.path``)."""
    return posixpath.join(document_dir(pid), validate_document_name(name))


def document_url(pid: int, name: str) -> str:
    """Value for ``documents.url``: ``file://`` + :func:`document_fs_path`."""
    return "file://" + document_fs_path(pid, name)
