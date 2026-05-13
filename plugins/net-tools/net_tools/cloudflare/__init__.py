"""Cloudflare DNS submodule.

Exposes 6 tools registered by ``net_tools/server.py``:

  cloudflare_dns_list_records   (read)
  cloudflare_dns_get_record     (read, lookup-by-name)
  cloudflare_dns_create_record  (mutate, gated)
  cloudflare_dns_update_record  (mutate, gated)
  cloudflare_dns_delete_record  (mutate, gated)
  cloudflare_dns_purge_cache    (mutate, gated, P3 — optional)
"""
from .client import CloudflareClient
from .tools import (
    cloudflare_dns_create_record,
    cloudflare_dns_delete_record,
    cloudflare_dns_get_record,
    cloudflare_dns_list_records,
    cloudflare_dns_update_record,
)

__all__ = [
    "CloudflareClient",
    "cloudflare_dns_create_record",
    "cloudflare_dns_delete_record",
    "cloudflare_dns_get_record",
    "cloudflare_dns_list_records",
    "cloudflare_dns_update_record",
]
