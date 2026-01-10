import logging
import os
from datetime import datetime
from logging.handlers import RotatingFileHandler

def setup_logging(service_name="sshbox", log_level=logging.INFO):
    """
    Set up logging for the sshBox service
    """
    # Create logs directory if it doesn't exist
    logs_dir = os.environ.get('LOGS_DIR', '/tmp/sshbox_logs')
    os.makedirs(logs_dir, exist_ok=True)
    
    # Create logger
    logger = logging.getLogger(service_name)
    logger.setLevel(log_level)
    
    # Prevent adding multiple handlers if logger already has handlers
    if logger.handlers:
        return logger
    
    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # File handler with rotation
    log_file = os.path.join(logs_dir, f"{service_name}.log")
    file_handler = RotatingFileHandler(
        log_file, 
        maxBytes=10*1024*1024,  # 10MB
        backupCount=5
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    return logger

# Global logger instance
logger = setup_logging()