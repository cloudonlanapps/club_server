"""CLI entry point for the club server."""

import argparse

import uvicorn


def main() -> None:
    """Run the club server."""
    parser = argparse.ArgumentParser(description="Club Server")
    _ = parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    _ = parser.add_argument("--port", type=int, default=8101, help="Port to bind to")
    _ = parser.add_argument("--reload", action="store_true", help="Enable auto-reload")
    args = parser.parse_args()

    print(f"Starting Club Server on {args.host}:{args.port}")
    uvicorn.run(
        "club_server.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
