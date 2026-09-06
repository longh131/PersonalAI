"""Bing-first web search parser (no live network)."""

from __future__ import annotations

from tools.websearch import format_search_rows, parse_bing_html


SAMPLE = """
<html><body>
<ol id="b_results">
<li class="b_algo">
  <h2><a href="https://www.gov.cn/notice">国务院通知</a></h2>
  <p>2026年春节放假安排</p>
</li>
<li class="b_algo">
  <h2><a href="https://example.com/school">中小学寒假</a></h2>
  <p>各地时间不同</p>
</li>
<li class="b_ad">
  <h2><a href="https://ads.example">广告</a></h2>
</li>
</ol>
</body></html>
"""


def test_parse_bing_html_skips_ads() -> None:
    rows = parse_bing_html(SAMPLE, max_results=5)
    assert len(rows) == 2
    assert rows[0]["href"] == "https://www.gov.cn/notice"
    assert "国务院" in rows[0]["title"]
    assert "春节" in rows[0]["body"]
    assert rows[1]["href"] == "https://example.com/school"


def test_format_search_rows() -> None:
    text = format_search_rows(
        [{"title": "A", "href": "https://a.example", "body": "摘要"}]
    )
    assert "https://a.example" in text
    assert "摘要" in text
    assert format_search_rows([]) == "没有搜索结果。"
