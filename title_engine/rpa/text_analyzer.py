# -*- coding: utf-8 -*-
"""
Text analyzer module for Chinese text processing.

Provides Chinese word segmentation, root extraction, and duplicate detection.
"""

import re
import logging
from typing import List, Set, Dict, Tuple, Optional, Union
from collections import Counter

logger = logging.getLogger(__name__)

# Try to import jieba for Chinese segmentation
try:
    import jieba
    import jieba.posseg as pseg
    JIEBA_AVAILABLE = True
except ImportError:
    JIEBA_AVAILABLE = False
    logger.warning("jieba not available, using basic segmentation")


class TextAnalyzer:
    """Chinese text analyzer with segmentation and analysis capabilities."""

    # Common Chinese particles and stop words
    STOP_WORDS: Set[str] = {
        "的", "了", "在", "是", "我", "有", "和", "就", "不", "人",
        "都", "一", "一个", "上", "也", "很", "到", "说", "要", "去",
        "你", "会", "着", "没有", "看", "好", "自己", "这", "他", "她",
        "它", "们", "那", "里", "为", "什么", "怎么", "如何", "可以",
        "能", "想", "做", "用", "来", "把", "让", "给", "从", "向",
        "被", "对", "与", "及", "等", "而", "但", "或", "之", "其",
        "此", "该", "又", "已", "还", "则", "所", "如", "若", "虽",
        "当", "使", "因", "由", "于", "以", "至", "直到", "只要",
        "如果", "虽然", "因此", "所以", "但是", "然而", "不过",
    }

    # Common Chinese suffixes
    SUFFIXES: Set[str] = {
        "的", "地", "得", "了", "着", "过", "吗", "呢", "吧",
        "啊", "呀", "哦", "哈", "呵", "嗯", "嘛", "啦", "噢",
    }

    def __init__(self):
        """Initialize text analyzer."""
        self._init_jieba()
        logger.info("TextAnalyzer initialized")

    def _init_jieba(self) -> None:
        """Initialize jieba tokenizer if available."""
        if not JIEBA_AVAILABLE:
            return

        # Add custom dictionary words for e-commerce
        custom_words = [
            "厂家直销", "源头工厂", "一件代发", "包邮",
            "爆款", "热销", "新品", "热卖", "促销",
            "限时", "特价", "清仓", "断码", "码数齐全",
            "质量保证", "假一赔十", "七天无理由",
            "实木", "布艺", "皮艺", "铁艺", "竹艺",
            "真皮", "仿皮", "PU", "PVC", "环保",
        ]

        for word in custom_words:
            jieba.add_word(word)

    def segment(self, text: str, use_pos: bool = False) -> List[str]:
        """
        Segment Chinese text into words.

        Args:
            text: Input text to segment
            use_pos: Whether to use part-of-speech tagging (currently ignored for type consistency)

        Returns:
            List of segmented words
        """
        if not text or not text.strip():
            return []

        # Clean text
        text = self._clean_text(text)

        if JIEBA_AVAILABLE:
            return list(jieba.cut(text))
        else:
            return self._basic_segment(text)

    def _clean_text(self, text: str) -> str:
        """
        Clean input text.

        Args:
            text: Input text

        Returns:
            Cleaned text
        """
        # Remove extra whitespace
        text = re.sub(r'\s+', ' ', text).strip()

        # Remove special characters but keep Chinese, alphanumeric, and common punctuation
        text = re.sub(r'[^\u4e00-\u9fa5a-zA-Z0-9\s\-\+\×÷=]', '', text)

        return text

    def _basic_segment(self, text: str) -> List[str]:
        """
        Basic character-level segmentation when jieba is not available.

        Args:
            text: Input text

        Returns:
            List of characters/words
        """
        words = []
        current_word = []

        for char in text:
            if '\u4e00' <= char <= '\u9fa5':
                # Chinese character
                if current_word:
                    words.append(''.join(current_word))
                    current_word = []
                words.append(char)
            elif char.isalnum():
                # Alphanumeric character
                current_word.append(char)
            else:
                if current_word:
                    words.append(''.join(current_word))
                    current_word = []

        if current_word:
            words.append(''.join(current_word))

        return words

    def extract_roots(self, words: List[str]) -> Set[str]:
        """
        Extract word roots from segmented words.

        Args:
            words: List of segmented words

        Returns:
            Set of unique word roots
        """
        roots: Set[str] = set()

        for word in words:
            # Skip stop words and particles
            if word in self.STOP_WORDS or word in self.SUFFIXES:
                continue

            # Skip very short words (single characters that are not meaningful)
            if len(word) == 1 and not self._is_meaningful_char(word):
                continue

            # Add the word itself as a root
            roots.add(word)

            # For longer words, also extract substrings
            if len(word) > 2:
                # Extract 2-character substrings
                for i in range(len(word) - 1):
                    substr = word[i:i+2]
                    if substr not in self.STOP_WORDS:
                        roots.add(substr)

        return roots

    def _is_meaningful_char(self, char: str) -> bool:
        """
        Check if a single character is meaningful.

        Args:
            char: Single character

        Returns:
            True if meaningful, False otherwise
        """
        # Common meaningful single characters
        meaningful_chars = {
            "大", "小", "长", "短", "高", "低", "新", "旧", "好", "坏",
            "多", "少", "快", "慢", "早", "晚", "上", "下", "左", "右",
            "前", "后", "里", "外", "红", "蓝", "绿", "黄", "白", "黑",
            "金", "银", "铜", "铁", "木", "水", "火", "土", "风", "云",
        }
        return char in meaningful_chars

    def detect_duplicates(self, words: List[str]) -> Dict[str, int]:
        """
        Detect duplicate words in a list.

        Args:
            words: List of words

        Returns:
            Dictionary mapping words to their occurrence counts
        """
        # Filter out stop words and particles
        filtered_words = [
            w for w in words
            if w not in self.STOP_WORDS and w not in self.SUFFIXES
        ]

        word_counts = Counter(filtered_words)

        # Return only duplicates
        return {word: count for word, count in word_counts.items() if count > 1}

    def get_word_frequency(self, words: List[str]) -> Dict[str, int]:
        """
        Get word frequency distribution.

        Args:
            words: List of words

        Returns:
            Dictionary mapping words to their frequencies
        """
        return dict(Counter(words))

    def calculate_keyword_density(self, text: str, keyword: str) -> float:
        """
        Calculate keyword density in text.

        Args:
            text: Input text
            keyword: Keyword to measure

        Returns:
            Keyword density as a float between 0 and 1
        """
        if not text or not keyword:
            return 0.0

        text_length = len(text)
        keyword_count = text.count(keyword)

        if text_length == 0:
            return 0.0

        return keyword_count / text_length

    def find_keyword_positions(self, text: str, keyword: str) -> List[int]:
        """
        Find all positions of keyword in text.

        Args:
            text: Input text
            keyword: Keyword to find

        Returns:
            List of start positions
        """
        positions = []
        start = 0

        while True:
            pos = text.find(keyword, start)
            if pos == -1:
                break
            positions.append(pos)
            start = pos + 1

        return positions

    def is_stop_word(self, word: str) -> bool:
        """
        Check if a word is a stop word.

        Args:
            word: Word to check

        Returns:
            True if stop word, False otherwise
        """
        return word in self.STOP_WORDS or word in self.SUFFIXES

    def get_text_statistics(self, text: str) -> Dict[str, int]:
        """
        Get text statistics.

        Args:
            text: Input text

        Returns:
            Dictionary with text statistics
        """
        if not text:
            return {
                "total_length": 0,
                "chinese_length": 0,
                "english_length": 0,
                "digit_length": 0,
                "word_count": 0,
            }

        chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fa5')
        english_chars = sum(1 for c in text if c.isalpha() and not ('\u4e00' <= c <= '\u9fa5'))
        digit_chars = sum(1 for c in text if c.isdigit())

        words = self.segment(text)

        return {
            "total_length": len(text),
            "chinese_length": chinese_chars,
            "english_length": english_chars,
            "digit_length": digit_chars,
            "word_count": len(words),
        }
