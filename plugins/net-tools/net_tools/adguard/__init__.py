"""AdGuard Home submodule.

6 tools registered by ``net_tools/server.py``:

  adguard_list_rewrites           (read)
  adguard_list_filtering_rules    (read)
  adguard_query_log_search        (read)
  adguard_set_rewrites            (mutate, gated, ATOMIC bulk replace)
  adguard_add_rewrite             (mutate, gated, helper sobre set)
  adguard_remove_rewrite          (mutate, gated, helper sobre set)

Multi-instance via env vars (see ``net_tools.multi_instance``):
  ADGUARD_<INSTANCE>_HOST  (required, full URL e.g. http://10.0.1.14:3000)
  ADGUARD_<INSTANCE>_USER  (required for Basic Auth)
  ADGUARD_<INSTANCE>_PASSWORD  (required for Basic Auth)

Caller passes ``host_ref="l1"`` and the resolver finds the credentials.
"""
from .client import AdGuardClient
from .tools import (
    adguard_add_rewrite,
    adguard_list_filtering_rules,
    adguard_list_rewrites,
    adguard_query_log_search,
    adguard_remove_rewrite,
    adguard_set_rewrites,
)

__all__ = [
    "AdGuardClient",
    "adguard_add_rewrite",
    "adguard_list_filtering_rules",
    "adguard_list_rewrites",
    "adguard_query_log_search",
    "adguard_remove_rewrite",
    "adguard_set_rewrites",
]
