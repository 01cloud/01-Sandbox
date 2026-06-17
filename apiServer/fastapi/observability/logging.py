import contextvars
import logging
import time

from pythonjsonlogger import jsonlogger

CORRELATION_ID_VAR = contextvars.ContextVar("correlation_id", default=None)


class CorrelationIdFormatter(jsonlogger.JsonFormatter):
    def add_fields(self, log_record, record, message_dict):
        super().add_fields(log_record, record, message_dict)
        # Add correlation ID if present in the execution context
        corr_id = CORRELATION_ID_VAR.get()
        if corr_id:
            log_record["correlation_id"] = corr_id
        else:
            # Fallback to checking extra attributes directly on record if passed explicitly
            corr_id = getattr(record, "correlation_id", None)
            if corr_id:
                log_record["correlation_id"] = corr_id

        # Normalize log fields to match modern practices
        if not log_record.get("timestamp"):
            # Use ISO 8601 formatting for timestamps
            log_record["timestamp"] = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)
            )
        log_record["level"] = record.levelname


def setup_logging(level: str = "INFO"):
    """
    Sets up root logging to format logs as structured JSON to stdout.
    """
    handler = logging.StreamHandler()
    formatter = CorrelationIdFormatter("%(timestamp)s %(level)s %(name)s %(message)s")
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    # Remove any pre-existing handlers to avoid duplicate logging
    for h in list(root_logger.handlers):
        root_logger.removeHandler(h)

    root_logger.addHandler(handler)
    root_logger.setLevel(level)

    # Silence verbose library logging if needed
    logging.getLogger("aiopika").setLevel(logging.WARNING)
    logging.getLogger("aio_pika").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def get_correlation_id() -> str | None:
    return CORRELATION_ID_VAR.get()


def set_correlation_id(val: str | None):
    CORRELATION_ID_VAR.set(val)
