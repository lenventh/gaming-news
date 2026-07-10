"""validator 模块单元测试 — 日期提取 + LLM 结果解析"""
import json
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import pytest

from pipeline.validator import _extract_date_from_page


# ============================================================
# 页面日期提取测试
# ============================================================

HTML_WITH_ARTICLE_TIME = """
<html>
<head>
<meta property="article:published_time" content="2026-07-08T10:30:00+08:00">
</head>
<body></body>
</html>
"""

HTML_WITH_OG_TIME = """
<html>
<head>
<meta property="og:article:published_time" content="2026-07-07T15:00:00Z">
</head>
<body></body>
</html>
"""

HTML_WITH_JSON_LD = """
<html>
<head>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "Article",
  "datePublished": "2026-07-06",
  "headline": "Test Article"
}
</script>
</head>
<body></body>
</html>
"""

HTML_WITH_JSON_LD_LIST = """
<html>
<head>
<script type="application/ld+json">
[{
  "@type": "Article",
  "dateModified": "2026-07-05T12:00:00Z"
}]
</script>
</head>
<body></body>
</html>
"""

HTML_WITH_TIME_TAG = """
<html>
<body>
<time datetime="2026-07-04">July 4, 2026</time>
</body>
</html>
"""

HTML_NO_DATE = """
<html>
<head><title>No Date</title></head>
<body><p>Nothing here</p></body>
</html>
"""


class TestExtractDateFromPage:
    """_extract_date_from_page 日期提取测试"""

    def test_article_published_time(self):
        """提取 article:published_time meta 标签"""
        with patch("pipeline.validator.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.text = HTML_WITH_ARTICLE_TIME
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp

            result = _extract_date_from_page("https://example.com/article")
            assert result == "2026-07-08T10:30:00+08:00"

    def test_og_article_time(self):
        """提取 og:article:published_time（无 article:published_time 时）"""
        with patch("pipeline.validator.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.text = HTML_WITH_OG_TIME
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp

            result = _extract_date_from_page("https://example.com/article")
            assert result == "2026-07-07T15:00:00Z"

    def test_json_ld_date(self):
        """从 schema.org JSON-LD 提取 datePublished"""
        with patch("pipeline.validator.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.text = HTML_WITH_JSON_LD
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp

            result = _extract_date_from_page("https://example.com/article")
            assert result == "2026-07-06"

    def test_json_ld_list_date(self):
        """从 JSON-LD 数组提取日期"""
        with patch("pipeline.validator.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.text = HTML_WITH_JSON_LD_LIST
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp

            result = _extract_date_from_page("https://example.com/article")
            assert result == "2026-07-05T12:00:00Z"

    def test_time_tag(self):
        """从 <time datetime> 标签提取"""
        with patch("pipeline.validator.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.text = HTML_WITH_TIME_TAG
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp

            result = _extract_date_from_page("https://example.com/article")
            assert result == "2026-07-04"

    def test_no_date_returns_none(self):
        """无日期标签时返回 None"""
        with patch("pipeline.validator.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.text = HTML_NO_DATE
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp

            result = _extract_date_from_page("https://example.com/article")
            assert result is None

    def test_timeout_returns_none(self):
        """请求超时时返回 None"""
        with patch("pipeline.validator.requests.get") as mock_get:
            import requests
            mock_get.side_effect = requests.Timeout()

            result = _extract_date_from_page("https://example.com/slow")
            assert result is None

    def test_article_time_priority_over_og(self):
        """article:published_time 优先级高于 og:article:published_time"""
        html = """
        <html><head>
        <meta property="article:published_time" content="2026-07-09">
        <meta property="og:article:published_time" content="2026-07-01">
        </head><body></body></html>
        """
        with patch("pipeline.validator.requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.text = html
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp

            result = _extract_date_from_page("https://example.com/article")
            assert result == "2026-07-09"


# ============================================================
# LLM 结果解析测试
# ============================================================

class TestLLMResultParsing:
    """validate() 中 LLM 返回结果的解析逻辑"""

    def _make_item(self, idx, title="Test", sub_type="general", cat="steam_deck"):
        return {
            "title": title, "url": f"https://example.com/{idx}",
            "published_at": "2026-07-08T00:00:00+00:00",
            "summary": "Test summary",
            "raw_data": {},
            "sub_type": sub_type,
            "category": cat,
            "source_name": "TestSource",
            "merged_sources": ["TestSource"],
            "material_links": [],
        }

    def test_verified_entries_kept(self):
        """verified 条目被保留"""
        from pipeline.validator import validate

        selected = {
            "steam_deck": [
                self._make_item(0, "Verified News", "leak"),
            ]
        }

        with patch("pipeline.validator.OPENAI_API_KEY", "sk-test"), \
             patch("pipeline.validator.OpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock()]
            mock_resp.choices[0].message.content = json.dumps({
                "0": {"date_confidence": "verified", "sub_type_ok": True,
                      "corrected_sub_type": None, "reason": "Contains recent date"}
            })
            mock_client.chat.completions.create.return_value = mock_resp
            mock_openai.return_value = mock_client

            result = validate(selected)
            assert len(result["steam_deck"]) == 1

    def test_rejected_entries_removed(self):
        """rejected 条目被从结果中移除"""
        from pipeline.validator import validate

        selected = {
            "steam_deck": [
                self._make_item(0, "Old News", "leak"),
            ]
        }

        with patch("pipeline.validator.OPENAI_API_KEY", "sk-test"), \
             patch("pipeline.validator.OpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock()]
            mock_resp.choices[0].message.content = json.dumps({
                "0": {"date_confidence": "rejected", "sub_type_ok": True,
                      "corrected_sub_type": None, "reason": "Mentions March event"}
            })
            mock_client.chat.completions.create.return_value = mock_resp
            mock_openai.return_value = mock_client

            result = validate(selected)
            assert len(result["steam_deck"]) == 0

    def test_sub_type_correction(self):
        """错误的 sub_type 被 LLM 修正"""
        from pipeline.validator import validate

        selected = {
            "steam_deck": [
                self._make_item(0, "System Update", "leak"),
            ]
        }

        with patch("pipeline.validator.OPENAI_API_KEY", "sk-test"), \
             patch("pipeline.validator.OpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock()]
            mock_resp.choices[0].message.content = json.dumps({
                "0": {"date_confidence": "verified", "sub_type_ok": False,
                      "corrected_sub_type": "system", "reason": "This is a system update"}
            })
            mock_client.chat.completions.create.return_value = mock_resp
            mock_openai.return_value = mock_client

            result = validate(selected)
            assert result["steam_deck"][0]["sub_type"] == "system"

    def test_llm_json_with_code_fences(self):
        """LLM 返回带 markdown 代码块的 JSON"""
        from pipeline.validator import validate

        selected = {
            "steam_deck": [
                self._make_item(0, "News", "general"),
            ]
        }

        with patch("pipeline.validator.OPENAI_API_KEY", "sk-test"), \
             patch("pipeline.validator.OpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock()]
            # 模拟 LLM 返回带代码块的 JSON
            mock_resp.choices[0].message.content = '```json\n' + json.dumps({
                "0": {"date_confidence": "verified", "sub_type_ok": True,
                      "corrected_sub_type": None, "reason": "OK"}
            }) + '\n```'
            mock_client.chat.completions.create.return_value = mock_resp
            mock_openai.return_value = mock_client

            result = validate(selected)
            assert len(result["steam_deck"]) == 1

    def test_missing_llm_key_skipped(self):
        """LLM 返回中缺少某些条目的 key 时不崩溃"""
        from pipeline.validator import validate

        selected = {
            "steam_deck": [
                self._make_item(0, "Item 0", "general"),
                self._make_item(1, "Item 1", "leak"),
            ]
        }

        with patch("pipeline.validator.OPENAI_API_KEY", "sk-test"), \
             patch("pipeline.validator.OpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock()]
            # LLM 只返回了 index 0，漏掉了 index 1
            mock_resp.choices[0].message.content = json.dumps({
                "0": {"date_confidence": "verified", "sub_type_ok": True,
                      "corrected_sub_type": None, "reason": "OK"}
            })
            mock_client.chat.completions.create.return_value = mock_resp
            mock_openai.return_value = mock_client

            result = validate(selected)
            # 两个条目都应该保留（未验证的默认保留）
            assert len(result["steam_deck"]) == 2

    def test_llm_failure_keeps_all(self):
        """LLM 调用失败时保留所有条目"""
        from pipeline.validator import validate

        selected = {
            "steam_deck": [
                self._make_item(0, "News", "leak"),
                self._make_item(1, "News 2", "release"),
            ]
        }

        with patch("pipeline.validator.OPENAI_API_KEY", "sk-test"), \
             patch("pipeline.validator.OpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_client.chat.completions.create.side_effect = Exception("API Error")
            mock_openai.return_value = mock_client

            result = validate(selected)
            assert len(result["steam_deck"]) == 2
            assert result["steam_deck"][0]["sub_type"] == "leak"

    def test_invalid_corrected_sub_type_ignored(self):
        """非法 corrected_sub_type 不被应用"""
        from pipeline.validator import validate

        selected = {
            "steam_deck": [
                self._make_item(0, "News", "general"),
            ]
        }

        with patch("pipeline.validator.OPENAI_API_KEY", "sk-test"), \
             patch("pipeline.validator.OpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock()]
            mock_resp.choices[0].message.content = json.dumps({
                "0": {"date_confidence": "verified", "sub_type_ok": False,
                      "corrected_sub_type": "invalid_type",
                      "reason": "This is a test"}
            })
            mock_client.chat.completions.create.return_value = mock_resp
            mock_openai.return_value = mock_client

            result = validate(selected)
            # 非法类型不应被应用
            assert result["steam_deck"][0]["sub_type"] == "general"

    def test_empty_selected_returns_unchanged(self):
        """空输入直接返回"""
        from pipeline.validator import validate

        result = validate({})
        assert result == {}

    def test_no_llm_config_returns_unchanged(self):
        """无 LLM 配置时直接返回"""
        from pipeline.validator import validate

        selected = {
            "steam_deck": [self._make_item(0, "News", "leak")]
        }

        with patch("pipeline.validator.OPENAI_API_KEY", "sk-xxx"):
            result = validate(selected)
            assert len(result["steam_deck"]) == 1


# ============================================================
# 日期解析函数测试
# ============================================================

class TestDateParsing:
    """测试各采集器中的日期解析工具函数"""

    def test_tieba_hours_ago(self):
        """贴吧 'X小时前' 解析"""
        from collectors.tieba_browser_collector import _parse_tieba_date
        from datetime import datetime, timedelta

        result = _parse_tieba_date("3小时前")
        expected = datetime.now() - timedelta(hours=3)
        assert result is not None
        assert abs((result - expected).total_seconds()) < 60

    def test_tieba_minutes_ago(self):
        """贴吧 'X分钟前' 解析"""
        from collectors.tieba_browser_collector import _parse_tieba_date
        from datetime import datetime, timedelta

        result = _parse_tieba_date("30分钟前")
        expected = datetime.now() - timedelta(minutes=30)
        assert result is not None
        assert abs((result - expected).total_seconds()) < 60

    def test_tieba_yesterday(self):
        """贴吧 '昨天' 解析"""
        from collectors.tieba_browser_collector import _parse_tieba_date
        from datetime import datetime, timedelta

        result = _parse_tieba_date("昨天")
        expected = datetime.now() - timedelta(days=1)
        assert result is not None
        assert result.day == expected.day

    def test_tieba_yesterday_with_time(self):
        """贴吧 '昨天 HH:MM' 解析"""
        from collectors.tieba_browser_collector import _parse_tieba_date

        result = _parse_tieba_date("昨天 14:30")
        assert result is not None
        assert result.hour == 14
        assert result.minute == 30

    def test_tieba_mm_dd(self):
        """贴吧 'MM-DD' 格式"""
        from collectors.tieba_browser_collector import _parse_tieba_date

        result = _parse_tieba_date("07-09")
        assert result is not None
        assert result.month == 7
        assert result.day == 9

    def test_tieba_mm_dd_with_time(self):
        """贴吧 'MM-DD HH:MM' 格式"""
        from collectors.tieba_browser_collector import _parse_tieba_date

        result = _parse_tieba_date("07-09 18:30")
        assert result is not None
        assert result.hour == 18
        assert result.minute == 30

    def test_tieba_full_date(self):
        """贴吧 'YYYY-MM-DD' 格式"""
        from collectors.tieba_browser_collector import _parse_tieba_date

        result = _parse_tieba_date("2026-07-01")
        assert result is not None
        assert result.year == 2026
        assert result.month == 7
        assert result.day == 1

    def test_tieba_with_reply_prefix(self):
        """贴吧 '回复于X小时前' 格式"""
        from collectors.tieba_browser_collector import _parse_tieba_date

        result = _parse_tieba_date("回复于5小时前")
        assert result is not None

    def test_tieba_invalid_date(self):
        """非法日期返回 None"""
        from collectors.tieba_browser_collector import _parse_tieba_date

        assert _parse_tieba_date("") is None
        assert _parse_tieba_date("刚刚") is None  # 不支持此格式

    def test_zhihu_date_parsing(self):
        """知乎日期解析"""
        from collectors.chinese_browser_collector import _extract_zhihu_date

        result = _extract_zhihu_date("发布于 2026-07-08")
        assert result is not None
        assert result.month == 7
        assert result.day == 8

    def test_zhihu_yesterday(self):
        """知乎 '昨天' 解析"""
        from collectors.chinese_browser_collector import _extract_zhihu_date
        from datetime import datetime, timedelta

        result = _extract_zhihu_date("昨天")
        assert result is not None
        expected = datetime.now(tz=timezone.utc) - timedelta(days=1)
        assert result.day == expected.day

    def test_smzdm_date_parsing(self):
        """什么值得买日期解析"""
        from collectors.chinese_browser_collector import _extract_smzdm_date

        result = _extract_smzdm_date("2026-07-08 14:30")
        assert result is not None
        assert result.hour == 14
        assert result.minute == 30
