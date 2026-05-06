from orchestrator.tools.search import fetch_url, web_search
from orchestrator.tools.domains import domain_check, domain_register
from orchestrator.tools.memory import search_memory
from orchestrator.tools.operator import ask_operator

TOOL_REGISTRY = {
    "web_search": web_search,
    "fetch_url": fetch_url,
    "domain_check": domain_check,
    "domain_register": domain_register,
    "search_memory": search_memory,
    "ask_operator": ask_operator,
}
