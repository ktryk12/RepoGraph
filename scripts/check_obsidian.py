"""Script to check connectivity to Obsidian Local REST API."""

import json
import logging
from repograph.connectors.obsidian.client import ObsidianClient
from repograph.connectors.obsidian.exceptions import ObsidianConnectorError

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
LOGGER = logging.getLogger(__name__)

def main():
    import os
    for env_file in [".env", ".env.example"]:
        if os.path.exists(env_file):
            with open(env_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        if k not in os.environ:
                            os.environ[k] = v

    LOGGER.info("Initializing Obsidian Client...")
    
    client = ObsidianClient()
    
    if not client.configured:
        LOGGER.error("Client is NOT configured. Please check OBSIDIAN_REST_API_URI and OBSIDIAN_API_KEY in your environment.")
        return

    LOGGER.info(f"Targeting Obsidian API at: {client.uri}")
    
    try:
        health = client.healthcheck()
        LOGGER.info(f"Healthcheck successful: {json.dumps(health)}")
        
        # Simple test query
        test_query = "architecture"
        LOGGER.info(f"Performing test query for: '{test_query}'")
        search_results = client.search_simple(test_query)
        LOGGER.info(f"Found {len(search_results)} notes for query '{test_query}'.")
        
        print("\nAll integration checks passed! Obsidian connection is fully functional.")
    except ObsidianConnectorError as exc:
        LOGGER.error(f"Integration Check Failed: {exc}")
    except Exception as exc:
        LOGGER.error(f"Unexpected error: {exc}")

if __name__ == "__main__":
    main()
