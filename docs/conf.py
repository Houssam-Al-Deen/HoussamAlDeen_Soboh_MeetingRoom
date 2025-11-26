# Configuration file for the Sphinx documentation builder.
import os
import sys

# Ensure project root is importable
sys.path.insert(0, os.path.abspath('..'))

# Signal code to avoid side effects during autodoc imports
os.environ['DOCS_BUILD'] = '1'

# -- Project information -----------------------------------------------------
project = 'Smart Meeting Room Microservices'
copyright = '2025, Houssam Al-Deen Soboh'
author = 'Houssam Al-Deen Soboh'
release = '1.0.0'

# -- General configuration ---------------------------------------------------
extensions = [
    'sphinx.ext.autodoc',
    'sphinx.ext.napoleon',
    'sphinx.ext.viewcode',
]

autodoc_default_options = {
    'members': True,
    'undoc-members': True,
    'show-inheritance': False,
}

napoleon_google_docstring = False
napoleon_numpy_docstring = True

templates_path = ['_templates']
exclude_patterns = ['_build', 'Thumbs.db', '.DS_Store']

# -- Options for HTML output -------------------------------------------------
html_theme = 'sphinx_rtd_theme'
html_static_path = ['_static']
