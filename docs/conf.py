"""Sphinx configuration for the Contacts API documentation."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

project = "Contacts API"
copyright = "2026, Contacts API contributors"
author = "Contacts API contributors"
release = "1.0"

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
]

autodoc_member_order = "bysource"
autodoc_typehints = "description"
napoleon_google_docstring = True
napoleon_numpy_docstring = True
exclude_patterns = ["_build"]
html_theme = "alabaster"
