#
# Copyright (c) 2021 Airbyte, Inc., all rights reserved.
#

import logging
import logging.config
import sys
import traceback
from functools import partial
from typing import List

from airbyte_cdk.models import AirbyteLogMessage, AirbyteMessage

TRACE_LEVEL_NUM = 5

LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "airbyte": {"()": "airbyte_cdk.logger.AirbyteLogFormatter", "format": "%(message)s"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
            "formatter": "airbyte",
        },
    },
    "root": {
        "handlers": ["console"],
    },
}


def init_unhandled_exception_output_filtering(logger: logging.Logger) -> None:
    """
    Make sure unhandled exceptions are not printed to the console without passing through the Airbyte logger and having
    secrets removed.
    """

    def hook_fn(_logger, exception_type, exception_value, traceback_):
        # For developer ergonomics, we want to see the stack trace in the logs when we do a ctrl-c
        if issubclass(exception_type, KeyboardInterrupt):
            sys.__excepthook__(exception_type, exception_value, traceback_)
            return

        logger.critical(str(exception_value))

    sys.excepthook = partial(hook_fn, logger)


def init_logger(name: str = None):
    """Initial set up of logger"""
    logging.setLoggerClass(AirbyteNativeLogger)
    logging.addLevelName(TRACE_LEVEL_NUM, "TRACE")
    logger = logging.getLogger(name)
    logger.setLevel(TRACE_LEVEL_NUM)
    logging.config.dictConfig(LOGGING_CONFIG)
    init_unhandled_exception_output_filtering(logger)
    return logger


class AirbyteLogFormatter(logging.Formatter):
    """Output log records using AirbyteMessage"""

    _secrets = []

    @classmethod
    def update_secrets(cls, secrets: List[str]):
        """Update the list of secrets to be replaced in the log message"""
        cls._secrets = secrets

    # Transforming Python log levels to Airbyte protocol log levels
    level_mapping = {
        logging.FATAL: "FATAL",
        logging.ERROR: "ERROR",
        logging.WARNING: "WARN",
        logging.INFO: "INFO",
        logging.DEBUG: "DEBUG",
        TRACE_LEVEL_NUM: "TRACE",
    }

    def format(self, record: logging.LogRecord) -> str:
        """Return a JSON representation of the log message"""
        message = super().format(record)
        airbyte_level = self.level_mapping.get(record.levelno, "INFO")
        for secret in AirbyteLogFormatter._secrets:
            message = message.replace(secret, "****")
        log_message = AirbyteMessage(type="LOG", log=AirbyteLogMessage(level=airbyte_level, message=message))
        return log_message.json(exclude_unset=True)


class AirbyteNativeLogger(logging.Logger):
    """Using native logger with implementing all AirbyteLogger features"""

    def __init__(self, name):
        super().__init__(name)
        self.valid_log_types = ["FATAL", "ERROR", "WARN", "INFO", "DEBUG", "TRACE"]

    def log_by_prefix(self, msg, default_level):
        """Custom method, which takes log level from first word of message"""
        split_line = msg.split()
        first_word = next(iter(split_line), None)
        if first_word in self.valid_log_types:
            log_level = logging.getLevelName(first_word)
            rendered_message = " ".join(split_line[1:])
        else:
            default_level = default_level if default_level in self.valid_log_types else "INFO"
            log_level = logging.getLevelName(default_level)
            rendered_message = msg
        self.log(log_level, rendered_message)

    def trace(self, msg, *args, **kwargs):
        self._log(TRACE_LEVEL_NUM, msg, args, **kwargs)


class AirbyteLogger:
    def __init__(self):
        self.valid_log_types = ["FATAL", "ERROR", "WARN", "INFO", "DEBUG", "TRACE"]

    def log_by_prefix(self, message, default_level):
        """Custom method, which takes log level from first word of message"""
        split_line = message.split()
        first_word = next(iter(split_line), None)
        if first_word in self.valid_log_types:
            log_level = first_word
            rendered_message = " ".join(split_line[1:])
        else:
            log_level = default_level
            rendered_message = message
        self.log(log_level, rendered_message)

    def log(self, level, message):
        log_record = AirbyteLogMessage(level=level, message=message)
        log_message = AirbyteMessage(type="LOG", log=log_record)
        print(log_message.json(exclude_unset=True))

    def fatal(self, message):
        self.log("FATAL", message)

    def exception(self, message):
        message = f"{message}\n{traceback.format_exc()}"
        self.error(message)

    def error(self, message):
        self.log("ERROR", message)

    def warn(self, message):
        self.log("WARN", message)

    def info(self, message):
        self.log("INFO", message)

    def debug(self, message):
        self.log("DEBUG", message)

    def trace(self, message):
        self.log("TRACE", message)
