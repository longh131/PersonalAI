"""Local-network web search. Prefer Bing; ddgs auto hits blocked overseas engines."""

from __future__ import annotations

from typing import Any

import httpx
from loguru import logger
from lxml import html

DEFAULT_TIMEOUT = 8.0
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def parse_bing_html(markup: str, max_results: int = 5) -> list[dict[str, str]]:
    """Extract organic Bing results from an HTML page."""
    tree = html.fromstring(markup)
    rows: list[dict[str, str]] = []
    for item in tree.xpath("//li[contains(@class,'b_algo')]"):
        title = "".join(item.xpath(".//h2//a//text()")).strip()
        hrefs = item.xpath(".//h2//a/@href")
        href = str(hrefs[0]).strip() if hrefs else ""
        body = "".join(item.xpath(".//p//text()")).strip()
        if not title and not href:
            continue
        rows.append({"title": title, "href": href, "body": body})
        if len(rows) >= max_results:
            break
    return rows


def search_bing(
    query: str,
    *,
    max_results: int = 5,
    timeout: float = DEFAULT_TIMEOUT,
    proxy: str | None = None,
) -> list[dict[str, str]]:
    """Search Bing HTML. Works on this machine without a proxy."""
    with httpx.Client(
        headers=_HEADERS,
        follow_redirects=True,
        timeout=timeout,
        proxy=proxy or None,
    ) as client:
        response = client.get(
            "https://www.bing.com/search",
            params={"q": query, "setlang": "zh-CN", "cc": "CN"},
        )
        response.raise_for_status()
    return parse_bing_html(response.text, max_results=max_results)


def search_ddgs(
    query: str,
    *,
    max_results: int = 5,
    timeout: float = 5.0,
    proxy: str | None = None,
    backend: str = "auto",
) -> list[dict[str, str]]:
    """Optional overseas metasearch. Only useful when a proxy can reach those hosts."""
    from ddgs import DDGS

    with DDGS(proxy=proxy or None, timeout=max(1, int(timeout))) as client:
        raw = list(client.text(query, max_results=max_results, backend=backend, region="cn-zh"))
    rows: list[dict[str, str]] = []
    for item in raw:
        rows.append(
            {
                "title": str(item.get("title") or ""),
                "href": str(item.get("href") or item.get("url") or ""),
                "body": str(item.get("body") or item.get("snippet") or ""),
            }
        )
    return rows


def search_web(
    query: str,
    *,
    max_results: int = 5,
    timeout: float = DEFAULT_TIMEOUT,
    proxy: str | None = None,
    backend: str = "bing",
) -> list[dict[str, str]]:
    """Run the search strategy. `bing` first; `auto` also tries ddgs if Bing is empty."""
    wanted = (backend or "bing").strip().lower()
    errors: list[str] = []
    if wanted in {"bing", "auto", ""}:
        try:
            rows = search_bing(query, max_results=max_results, timeout=timeout, proxy=proxy)
            if rows:
                return rows
            errors.append("bing: empty")
        except Exception as exc:  # noqa: BLE001
            logger.warning("bing search failed: {}", exc)
            errors.append(f"bing: {exc}")
    if wanted in {"auto", "ddgs"} or proxy:
        try:
            rows = search_ddgs(query, max_results=max_results, timeout=min(timeout, 5), proxy=proxy)
            if rows:
                return rows
            errors.append("ddgs: empty")
        except Exception as exc:  # noqa: BLE001
            logger.warning("ddgs search failed: {}", exc)
            errors.append(f"ddgs: {exc}")
    detail = "；".join(errors) if errors else "没有搜索结果。"
    raise RuntimeError(detail)


def format_search_rows(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "没有搜索结果。"
    chunks: list[str] = []
    for index, row in enumerate(rows, start=1):
        title = row.get("title") or ""
        href = row.get("href") or row.get("url") or ""
        body = row.get("body") or row.get("snippet") or ""
        chunks.append(f"{index}. {title}\n{href}\n{body}")
    return "\n\n".join(chunks)
