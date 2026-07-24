# -*- coding: utf-8 -*-
"""
RPA package initialization.

Provides core title engine functionality including:
- Title generation and optimization
- Keyword management
- SEO rule validation
- Text analysis
- Score calculation
"""

from rpa.title_generator import TitleGenerator
from rpa.title_optimizer import TitleOptimizer
from rpa.keyword_manager import KeywordManager
from rpa.seo_rules import SEORules
from rpa.text_analyzer import TextAnalyzer
from rpa.score_calculator import ScoreCalculator

__all__ = [
    "TitleGenerator",
    "TitleOptimizer",
    "KeywordManager",
    "SEORules",
    "TextAnalyzer",
    "ScoreCalculator",
]
