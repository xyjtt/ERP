# -*- coding: utf-8 -*-
"""
Flask API for title engine.

Provides RESTful endpoints for title generation, optimization, and keyword management.
"""

import logging
from typing import Dict, Any, List
from flask import Flask, request, jsonify
from functools import wraps

logger = logging.getLogger(__name__)


def create_app(config: Dict[str, Any] = None) -> Flask:
    """
    Create Flask application.

    Args:
        config: Optional configuration dictionary

    Returns:
        Configured Flask app
    """
    app = Flask(__name__)

    # Apply configuration
    if config:
        app.config.update(config)

    # Initialize components
    from rpa.title_generator import TitleGenerator, ProductInfo
    from rpa.title_optimizer import TitleOptimizer
    from rpa.keyword_manager import KeywordManager
    from rpa.keyword_sources import KeywordSourceService
    from rpa.product_fit import ProductContext
    from rpa.score_calculator import ScoreCalculator
    from database.db_manager import DatabaseManager

    generator = TitleGenerator()
    optimizer = TitleOptimizer()
    db_manager = DatabaseManager()
    keyword_manager = KeywordManager(db_manager)
    keyword_source_service = KeywordSourceService()
    title_score_metadata = ScoreCalculator().get_score_metadata()

    def handle_errors(f):
        """Decorator for handling API errors."""
        @wraps(f)
        def decorated_function(*args, **kwargs):
            try:
                return f(*args, **kwargs)
            except Exception as e:
                logger.error("API error: %s", e)
                return jsonify({
                    "success": False,
                    "error": str(e),
                }), 500
        return decorated_function

    @app.route("/api/title/generate", methods=["POST"])
    @handle_errors
    def generate_title():
        """
        Generate SEO-optimized titles.

        Request body:
            category: Product category
            core_keywords: List of core keywords
            attributes: Product attributes (optional)
            selling_points: Selling points (optional)
            brand: Brand name (optional)
            num_titles: Number of titles to generate (optional, default 5)

        Returns:
            JSON with generated titles
        """
        data = request.get_json()

        if not data:
            return jsonify({
                "success": False,
                "error": "请求体不能为空",
            }), 400

        # Validate required fields
        if "category" not in data:
            return jsonify({
                "success": False,
                "error": "缺少必要参数: category",
            }), 400

        raw_core_keywords = data.get("core_keywords")
        if isinstance(raw_core_keywords, str):
            core_keywords = [
                value.strip()
                for value in raw_core_keywords.replace("，", ",").split(",")
                if value.strip()
            ]
        elif isinstance(raw_core_keywords, list):
            core_keywords = [str(value).strip() for value in raw_core_keywords if str(value).strip()]
        else:
            core_keywords = []
        if not core_keywords:
            return jsonify({
                "success": False,
                "error": "缺少必要参数: core_keywords",
            }), 400

        source_keywords = []
        source_summaries = []
        product_fit_metadata = None
        keyword_candidates = []
        if bool(data.get("use_source_keywords")):
            context = ProductContext(
                category=str(data["category"]).strip(),
                product_name=str(data.get("product_name") or "").strip(),
                core_keywords=core_keywords,
                attributes=data.get("attributes", {}) if isinstance(data.get("attributes"), dict) else {},
            )
            candidates, summaries, product_fit_metadata = keyword_source_service.preview(
                context,
                limit_per_source=max(50, min(int(data.get("source_limit") or 500), 2000)),
            )
            ranked = keyword_manager.rank_keywords(candidates, limit=50)
            source_keywords = [keyword.to_dict() for keyword, _score in ranked]
            minimum_product_fit = max(
                0.0,
                min(float(data.get("source_keyword_min_fit") or 0.2), 1.0),
            )
            complete_words = [
                keyword.word
                for keyword, _score in ranked
                if keyword.score_status == "complete"
                and keyword.source_role == "keyword_candidate"
                and float(keyword.product_fit or 0.0) >= minimum_product_fit
            ]
            independent_words = [
                keyword.word
                for keyword, _score in ranked
                if keyword.source_role == "independent_candidate"
                and keyword.freshness_status == "fresh"
            ]
            keyword_candidates = list(dict.fromkeys(complete_words + independent_words))[
                : max(3, min(int(data.get("source_keyword_limit") or 20), 30))
            ]
            source_summaries = [summary.to_dict() for summary in summaries]

        product_info = ProductInfo(
            category=data["category"],
            core_keywords=core_keywords,
            attributes=data.get("attributes", {}),
            selling_points=data.get("selling_points", []),
            brand=data.get("brand"),
            model=data.get("model"),
            product_name=data.get("product_name"),
            keyword_candidates=keyword_candidates,
        )
        num_titles = max(1, min(int(data.get("num_titles") or 5), 30))

        # Generate titles
        results = generator.generate(product_info, num_titles=num_titles)

        # Convert to response format
        titles = []
        for result in results:
            titles.append({
                "title": result.title,
                "score": result.score,
                "score_type": title_score_metadata["score_type"],
                "score_version": title_score_metadata["score_version"],
                "core_word_used": result.core_word_used,
                "word_count": result.word_count,
                "breakdown": result.breakdown,
            })

        return jsonify({
            "success": True,
            "data": {
                "titles": titles,
                "count": len(titles),
                "requested_count": num_titles,
                "generation_status": "complete" if len(titles) == num_titles else "partial",
                "score_metadata": title_score_metadata,
                "source_keywords": source_keywords,
                "selected_source_keywords": keyword_candidates,
                "source_keyword_min_fit": minimum_product_fit if bool(data.get("use_source_keywords")) else None,
                "source_summaries": source_summaries,
                "product_fit_metadata": product_fit_metadata,
            },
        })

    @app.route("/api/title/optimize", methods=["POST"])
    @handle_errors
    def optimize_title():
        """
        Optimize an existing title.

        Request body:
            title: Title to optimize
            category: Product category (optional)
            core_keywords: Core keywords (optional)

        Returns:
            JSON with optimization results
        """
        data = request.get_json()

        if not data:
            return jsonify({
                "success": False,
                "error": "请求体不能为空",
            }), 400

        if "title" not in data:
            return jsonify({
                "success": False,
                "error": "缺少必要参数: title",
            }), 400

        title = data["title"]
        category = data.get("category")
        core_keywords = data.get("core_keywords")

        # Optimize title
        result = optimizer.optimize(title, category, core_keywords)

        # Convert suggestions to response format
        suggestions = []
        for suggestion in result.suggestions:
            suggestions.append({
                "type": suggestion.type,
                "priority": suggestion.priority,
                "description": suggestion.description,
                "current_value": str(suggestion.current_value),
                "suggested_value": str(suggestion.suggested_value),
                "impact_score": suggestion.impact_score,
            })

        return jsonify({
            "success": True,
            "data": {
                "original_title": result.original_title,
                "optimized_title": result.optimized_title,
                "original_score": result.original_score,
                "optimized_score": result.optimized_score,
                "suggestions": suggestions,
                "improvements": result.improvements,
                "score_metadata": title_score_metadata,
            },
        })

    @app.route("/api/title/diagnose", methods=["POST"])
    @handle_errors
    def diagnose_title():
        """
        Diagnose an existing title without changing online data.

        Request body:
            title: Title to diagnose
            category: Product category (optional)

        Returns:
            JSON with title analysis, score breakdown, and detected issues
        """
        data = request.get_json()

        if not data:
            return jsonify({
                "success": False,
                "error": "请求体不能为空",
            }), 400

        if "title" not in data:
            return jsonify({
                "success": False,
                "error": "缺少必要参数: title",
            }), 400

        analysis = optimizer.analyze(
            data["title"],
            data.get("category"),
        )

        return jsonify({
            "success": True,
            "data": analysis,
        })

    @app.route("/api/title/keywords", methods=["GET"])
    @handle_errors
    def get_keywords():
        """
        Query keywords.

        Query parameters:
            category: Category filter (optional)
            query: Search query (optional)
            limit: Maximum results (optional, default 50)

        Returns:
            JSON with keyword list
        """
        category = request.args.get("category")
        query = request.args.get("query")
        limit = max(1, min(request.args.get("limit", 50, type=int) or 50, 200))

        if query:
            keywords = keyword_manager.search_keywords(query, category, limit)
        elif category:
            keywords = keyword_manager.get_keywords(category, limit)
        else:
            # Return error if no filter specified
            return jsonify({
                "success": False,
                "error": "请提供 category 或 query 参数",
            }), 400

        ranked_keywords = keyword_manager.rank_keywords(keywords, limit=limit)
        keyword_list = [keyword.to_dict() for keyword, _score in ranked_keywords]

        return jsonify({
            "success": True,
            "data": {
                "keywords": keyword_list,
                "count": len(keyword_list),
                "score_metadata": keyword_manager.get_score_metadata(),
            },
        })

    @app.route("/api/title/batch", methods=["POST"])
    @handle_errors
    def batch_generate():
        """
        Batch generate titles for multiple products.

        Request body:
            products: List of product info objects

        Returns:
            JSON with batch results
        """
        data = request.get_json()

        if not data or "products" not in data:
            return jsonify({
                "success": False,
                "error": "请求体不能为空且必须包含 products 数组",
            }), 400

        products = data["products"]

        if not isinstance(products, list):
            return jsonify({
                "success": False,
                "error": "products 必须是数组",
            }), 400

        results = []

        for product_data in products:
            # Validate each product
            if "category" not in product_data or "core_keywords" not in product_data:
                results.append({
                    "success": False,
                    "error": "缺少必要参数: category 或 core_keywords",
                    "product": product_data,
                })
                continue

            # Create ProductInfo
            product_info = ProductInfo(
                category=product_data["category"],
                core_keywords=product_data["core_keywords"],
                attributes=product_data.get("attributes", {}),
                selling_points=product_data.get("selling_points", []),
                brand=product_data.get("brand"),
                model=product_data.get("model"),
            )

            num_titles = product_data.get("num_titles", 3)

            # Generate titles
            titles = generator.generate(product_info, num_titles=num_titles)

            title_results = []
            for title in titles:
                title_results.append({
                    "title": title.title,
                    "score": title.score,
                    "score_type": title_score_metadata["score_type"],
                    "score_version": title_score_metadata["score_version"],
                    "core_word_used": title.core_word_used,
                    "word_count": title.word_count,
                    "breakdown": title.breakdown,
                })

            results.append({
                "success": True,
                "titles": title_results,
                "count": len(title_results),
            })

        return jsonify({
            "success": True,
            "data": {
                "results": results,
                "total": len(results),
                "score_metadata": title_score_metadata,
            },
        })

    @app.route("/api/title/keywords/preview", methods=["POST"])
    @handle_errors
    def preview_source_keywords():
        """Preview live crawler keyword candidates without writing snapshots."""
        data = request.get_json() or {}
        category = str(data.get("category") or "").strip()
        if not category:
            return jsonify({
                "success": False,
                "error": "缺少必要参数: category",
            }), 400

        raw_core_keywords = data.get("core_keywords", [])
        if isinstance(raw_core_keywords, str):
            core_keywords = [
                value.strip()
                for value in raw_core_keywords.replace("，", ",").split(",")
                if value.strip()
            ]
        elif isinstance(raw_core_keywords, list):
            core_keywords = [str(value).strip() for value in raw_core_keywords if str(value).strip()]
        else:
            core_keywords = []
        attributes = data.get("attributes")
        if not isinstance(attributes, dict):
            attributes = {}
        limit = max(1, min(int(data.get("limit") or 50), 200))
        source_limit = max(limit, min(int(data.get("source_limit") or 500), 2000))
        context = ProductContext(
            category=category,
            product_name=str(data.get("product_name") or "").strip(),
            core_keywords=core_keywords,
            attributes=attributes,
        )
        candidates, summaries, product_fit_metadata = keyword_source_service.preview(
            context,
            limit_per_source=source_limit,
        )
        query = str(data.get("query") or "").strip().lower()
        if query:
            candidates = [
                candidate
                for candidate in candidates
                if query in candidate.word.lower()
            ]
        ranked = keyword_manager.rank_keywords(candidates, limit=limit)
        keyword_list = [keyword.to_dict() for keyword, _score in ranked]
        return jsonify({
            "success": True,
            "data": {
                "keywords": keyword_list,
                "count": len(keyword_list),
                "score_metadata": keyword_manager.get_score_metadata(),
                "product_fit_metadata": product_fit_metadata,
                "source_summaries": [summary.to_dict() for summary in summaries],
                "read_only": True,
            },
        })

    @app.route("/api/title/history", methods=["GET"])
    @handle_errors
    def get_history():
        """
        Get title generation history.

        Query parameters:
            category: Category filter (optional)
            limit: Maximum results (optional, default 50)

        Returns:
            JSON with history records
        """
        category = request.args.get("category")
        limit = request.args.get("limit", 50, type=int)

        # Get history from database
        history = db_manager.get_title_history(category, limit)

        return jsonify({
            "success": True,
            "data": {
                "history": history,
                "count": len(history),
            },
        })

    @app.route("/api/title/analyze", methods=["POST"])
    @handle_errors
    def analyze_title():
        """
        Analyze a title.

        Request body:
            title: Title to analyze
            category: Product category (optional)

        Returns:
            JSON with analysis results
        """
        data = request.get_json()

        if not data or "title" not in data:
            return jsonify({
                "success": False,
                "error": "请求体不能为空且必须包含 title 参数",
            }), 400

        title = data["title"]
        category = data.get("category")

        # Analyze title
        analysis = optimizer.analyze(title, category)

        return jsonify({
            "success": True,
            "data": analysis,
        })

    @app.route("/api/health", methods=["GET"])
    @handle_errors
    def health_check():
        """
        Health check endpoint.

        Returns:
            JSON with health status
        """
        return jsonify({
            "success": True,
            "data": {
                "status": "healthy",
                "service": "title_engine",
                "scores": {
                    "keyword": keyword_manager.get_score_metadata(),
                    "title_structure": title_score_metadata,
                },
                "keyword_sources": keyword_source_service.metadata(),
            },
        })

    return app
