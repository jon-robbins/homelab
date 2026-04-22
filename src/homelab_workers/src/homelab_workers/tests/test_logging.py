from __future__ import annotations

import logging
import uuid

from homelab_workers.shared.logging import setup_logging


def test_setup_logging_returns_logger_with_level() -> None:
    logger_name = f"test.logging.{uuid.uuid4().hex}"

    logger = setup_logging(logger_name, level=logging.INFO)

    assert logger.name == logger_name
    assert logger.level == logging.INFO
    assert any(isinstance(handler, logging.StreamHandler) for handler in logger.handlers)


def test_setup_logging_does_not_duplicate_stream_handler() -> None:
    logger_name = f"test.logging.{uuid.uuid4().hex}"

    first = setup_logging(logger_name, level=logging.INFO)
    second = setup_logging(logger_name, level=logging.DEBUG)

    assert first is second
    stream_handlers = [h for h in second.handlers if isinstance(h, logging.StreamHandler)]
    assert len(stream_handlers) == 1
    assert second.level == logging.DEBUG
