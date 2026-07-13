import datetime
import os
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, os.path.abspath("../packages/synology-apm-sdk/src"))

project = "APM Python SDK"
copyright = f"{datetime.date.today().year}, Synology Inc."
author = "Synology Inc."
with open(Path(__file__).parent.parent / "packages/synology-apm-sdk/pyproject.toml", "rb") as f:
    _pkg_version = tomllib.load(f)["project"]["version"]

version = ".".join(_pkg_version.split(".")[:2])
release = _pkg_version

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.intersphinx",
]

autodoc_default_options = {
    "members": True,
    "undoc-members": False,
    "show-inheritance": True,
    "member-order": "bysource",
    "inherited-members": True,
}
autodoc_typehints = "description"
autodoc_typehints_format = "short"
autodoc_typehints_description_target = "documented"

napoleon_google_docstring = True
napoleon_numpy_docstring = False
# Use :ivar: for the Attributes section; prevents duplicate entries with
# autodoc's dataclass field discovery.
napoleon_use_ivar = True

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
}

html_theme = "furo"
html_title = "APM Python SDK"
html_show_sphinx = False
html_show_sourcelink = False
html_copy_source = False

exclude_patterns = [
    "_build",
    "api/synology_apm.rst",                # namespace package root, nothing to document
    "api/synology_apm.sdk.rst",            # re-exports from __init__.py duplicate submodule descriptions
    "api/synology_apm.sdk.models.rst",     # empty __init__
    "api/synology_apm.sdk.collections.rst",  # empty __init__
]
