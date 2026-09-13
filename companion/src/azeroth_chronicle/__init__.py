"""Azeroth Chronicle companion.

Imports the addon's raw event journal into a local SQLite index and
answers questions from it. The journal is canonical; this package builds
only derived views that can be deleted and rebuilt at any time.
"""

__version__ = "0.0.1"
