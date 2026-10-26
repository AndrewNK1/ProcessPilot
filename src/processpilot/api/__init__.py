"""FastAPI backend: the pipeline as HTTP endpoints, with jobs and a SQLite index.

``main.create_app`` builds the app, ``service.Workspace`` does the work,
``db.Database`` is the index and decision log, ``jobs.JobRunner`` runs slow
steps in the background. Start it with ``python -m processpilot.api``.
"""
