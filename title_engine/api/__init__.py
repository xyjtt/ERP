# -*- coding: utf-8 -*-
"""
API package initialization.

Provides Flask API endpoints for the title engine.
"""

from api.title_api import create_app

__all__ = ["create_app"]
