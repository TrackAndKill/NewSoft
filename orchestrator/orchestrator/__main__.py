import logging

import uvicorn

from orchestrator.config import settings


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    uvicorn.run(
        "orchestrator.api:app",
        host="127.0.0.1",
        port=settings.orchestrator_port,
        reload=False,
    )


if __name__ == "__main__":
    main()
