"""Local entry point. This command never provisions hosting or registers a plugin."""

import argparse
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from .auth import OAuthConfig
from .server import create_http_app, create_server
from .service import ReviewService, default_registry


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio")
    parser.add_argument("--host", choices=("127.0.0.1", "0.0.0.0"), default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    # These libraries can otherwise log raw provider error details or HTTP URLs.
    for name in ("openai", "openai.agents", "httpx", "httpx2"):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    try:
        load_dotenv(Path(__file__).resolve().parent.parent / ".env")
        model = os.getenv("OPENAI_MODEL") or "gpt-4.1-mini"
        service = ReviewService(default_registry(model),
                                enable_reviews=os.getenv("H2O_MCP_ENABLE_REVIEWS") == "1")
        if args.transport == "stdio":
            create_server(service).run("stdio")
        else:
            import uvicorn
            app = create_http_app(service, OAuthConfig.from_env())
            uvicorn.run(app, host=args.host, port=args.port, log_level="warning",
                        access_log=False, proxy_headers=False)
    except (OSError, UnicodeError, ValueError):
        parser.exit(2, "Configuration error: check the documented MCP environment settings and endpoint URLs.\n")


if __name__ == "__main__":
    main()
