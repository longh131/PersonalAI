"""Hugging Face Hub access helpers.

Model files live on huggingface.co. In some networks that host times out
(WinError 10060) and the Hub client retries for minutes, blocking startup.
Normal boot stays offline and only reads the local cache. Use
`--download-models` once (with a working Hub connection) to fill the cache.
"""

from __future__ import annotations

import os


def configure_huggingface_hub(*, offline: bool = True) -> None:
    """Configure Hub access before `huggingface_hub` is imported.

    Args:
        offline: When True (default boot), never contact the Hub. When False,
            allow downloads with longer timeouts for a one-time cache fill.
    """
    if offline:
        os.environ["HF_HUB_ETAG_TIMEOUT"] = "2"
        os.environ["HF_HUB_DOWNLOAD_TIMEOUT"] = "5"
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        return
    os.environ.pop("HF_HUB_OFFLINE", None)
    os.environ.pop("TRANSFORMERS_OFFLINE", None)
    os.environ["HF_HUB_ETAG_TIMEOUT"] = "30"
    os.environ["HF_HUB_DOWNLOAD_TIMEOUT"] = "120"
