#!/usr/bin/env python3
"""
Enhanced structured logging for sshBox
Provides JSON-formatted logs for production and human-readable logs for development
"""
import logging
import os
import json
import sys
import traceback
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from logging.handlers import RotatingFileHandler
from pathlib import Path


class StructuredFormatter(logging.Formatter):
    """JSON structured logging formatter for production"""
    
    def __init__(self, service_name: str = "sshbox", include_extra: bool = True):
        super().__init__()
        self.service_name = service_name
        self.include_extra = include_extra
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON"""
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "service": self.service_name,
            "logger": record.name,
            "message": record.getMessage(),
            "thread": record.thread,
            "process": record.process,
        }
        
        # Add location info for WARNING and above
        if record.levelno >= logging.WARNING:
            log_entry["location"] = {
                "file": record.pathname,
                "line": record.lineno,
                "function": record.funcName
            }
        
        # Add extra fields from record
        if self.include_extra and hasattr(record, '__dict__'):
            extra_fields = {
                k: v for k, v in record.__dict__.items()
                if k not in {
                    'name', 'msg', 'args', 'created', 'filename', 'funcName',
                    'levelname', 'levelno', 'lineno', 'module', 'msecs',
                    'pathname', 'process', 'processName', 'relativeCreated',
                    'stack_info', 'exc_info', 'exc_text', 'thread', 'threadName',
                    'message', 'asctime'
                } and not k.startswith('_')
            }
            if extra_fields:
                log_entry["extra"] = extra_fields
        
        # Add exception info if present
        if record.exc_info:
            log_entry["exception"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else "Unknown",
                "message": str(record.exc_info[1]) if record.exc_info[1] else "",
                "traceback": traceback.format_exception(*record.exc_info)
            }
        
        return json.dumps(log_entry, default=str)


class ConsoleFormatter(logging.Formatter):
    """Human-readable console formatter with colors"""
    
    # ANSI color codes
    COLORS = {
        'DEBUG': '\033[36m',      # Cyan
        'INFO': '\033[32m',       # Green
        'WARNING': '\033[33m',    # Yellow
        'ERROR': '\033[31m',      # Red
        'CRITICAL': '\033[35m',   # Magenta
    }
    RESET = '\033[0m'
    
    def __init__(self, service_name: str = "sshbox", use_colors: bool = None):
        # Auto-detect color support
        if use_colors is None:
            use_colors = sys.stderr.isatty()
        
        self.use_colors = use_colors
        self.service_name = service_name
        
        fmt = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        if use_colors:
            fmt = self._colorize_fmt(fmt)
        
        super().__init__(fmt=fmt, datefmt="%Y-%m-%d %H:%M:%S")
    
    def _colorize_fmt(self, fmt: str) -> str:
        """Add color codes to format string"""
        for level, color in self.COLORS.items():
            fmt = fmt.replace(f"%(levelname)s", f"%(levelname)s")
        return fmt
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record with optional colors"""
        if self.use_colors:
            levelname = record.levelname
            color = self.COLORS.get(levelname, self.RESET)
            record.levelname = f"{color}{levelname}{self.RESET}"
        
        result = super().format(record)
        
        # Restore original levelname
        record.levelname = record.levelname.replace(self.RESET, '').replace('\033[', '').split('m')[-1]
        for level in self.COLORS:
            if level in record.levelname:
                record.levelname = level
                break
        
        return result


def setup_logging(
    service_name: str = "sshbox",
    log_level: int = logging.INFO,
    log_format: str = "auto",
    logs_dir: str = None,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
    include_console: bool = True
) -> logging.Logger:
    """
    Set up logging for sshBox service
    
    Args:
        service_name: Name of the service for logging
        log_level: Logging level (default: INFO)
        log_format: Log format - "json", "console", or "auto"
                     "auto" uses JSON for files, console for stdout
        logs_dir: Directory for log files (default: /var/log/sshbox or /tmp/sshbox_logs)
        max_bytes: Max size per log file before rotation (default: 10MB)
        backup_count: Number of backup log files to keep (default: 5)
        include_console: Whether to include console handler (default: True)
    
    Returns:
        Configured logger instance
    """
    # Determine logs directory
    if logs_dir is None:
        logs_dir = os.environ.get('LOGS_DIR', '/var/log/sshbox')
        if not os.access(logs_dir, os.W_OK):
            logs_dir = os.environ.get('LOGS_DIR', '/tmp/sshbox_logs')
    
    try:
        Path(logs_dir).mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print(f"Warning: Could not create logs directory {logs_dir}: {e}", file=sys.stderr)
        logs_dir = "/tmp"
    
    # Create logger
    logger = logging.getLogger(service_name)
    logger.setLevel(log_level)
    
    # Prevent duplicate handlers
    if logger.handlers:
        return logger
    
    # Determine format for file handler
    if log_format == "auto":
        file_format = "json"
        console_format = "console"
    else:
        file_format = log_format
        console_format = log_format
    
    # File handler with rotation
    log_file = os.path.join(logs_dir, f"{service_name}.log")
    try:
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=max_bytes,
            backupCount=backup_count
        )
        file_handler.setLevel(log_level)
        
        if file_format == "json":
            file_handler.setFormatter(StructuredFormatter(service_name))
        else:
            file_handler.setFormatter(ConsoleFormatter(service_name, use_colors=False))
        
        logger.addHandler(file_handler)
    except Exception as e:
        print(f"Warning: Could not create log file {log_file}: {e}", file=sys.stderr)
    
    # Console handler
    if include_console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(log_level)
        
        if console_format == "json":
            console_handler.setFormatter(StructuredFormatter(service_name))
        else:
            console_handler.setFormatter(ConsoleFormatter(service_name))
        
        logger.addHandler(console_handler)
    
    # Log startup message
    logger.info(f"Logging initialized for {service_name} (level={logging.getLevelName(log_level)})")
    
    return logger


class LogContext:
    """
    Context manager for adding structured context to logs
    
    Usage:
        logger = logging.getLogger("gateway")
        
        with LogContext(logger, session_id="123", user_id="user@example.com"):
            logger.info("Processing request")
            # Log will include session_id and user_id
    """
    
    def __init__(self, logger: logging.Logger, **context):
        self.logger = logger
        self.context = context
        self.old_factory = None
    
    def __enter__(self):
        self.old_factory = logging.getLogRecordFactory()
        
        def record_factory(*args, **kwargs):
            record = self.old_factory(*args, **kwargs)
            for key, value in self.context.items():
                setattr(record, key, value)
            return record
        
        logging.setLogRecordFactory(record_factory)
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        logging.setLogRecordFactory(self.old_factory)
        return False


def add_log_context(logger: logging.Logger, **kwargs) -> logging.LoggerAdapter:
    """
    Add context to log messages using LoggerAdapter
    
    Usage:
        logger = logging.getLogger("gateway")
        context_logger = add_log_context(logger, session_id="123", user_id="user@example.com")
        context_logger.info("Processing request")  # Will include context
    """
    class ContextAdapter(logging.LoggerAdapter):
        def process(self, msg, kwargs):
            extra = kwargs.get('extra', {})
            extra.update(self.extra)
            kwargs['extra'] = extra
            return msg, kwargs
    
    return ContextAdapter(logger, kwargs)


# Global logger instance for the logging module itself
logger = setup_logging("logging_config")


# Convenience functions
def get_logger(name: str) -> logging.Logger:
    """Get a logger with the sshBox prefix"""
    return logging.getLogger(f"sshbox.{name}")


def log_execution_time(logger: logging.Logger, operation: str):
    """
    Decorator to log function execution time
    
    Usage:
        @log_execution_time(logger, "provision_container")
        def provision_container(...):
            ...
    """
    import time
    from functools import wraps
    
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start_time = time.time()
            try:
                result = func(*args, **kwargs)
                elapsed = time.time() - start_time
                logger.debug(f"{operation} completed in {elapsed:.3f}s")
                return result
            except Exception as e:
                elapsed = time.time() - start_time
                logger.error(f"{operation} failed after {elapsed:.3f}s: {e}")
                raise
        return wrapper
    
    return decorator