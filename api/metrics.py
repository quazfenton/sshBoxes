"""
Metrics collection for sshBox system
Provides monitoring and observability capabilities
"""
import time
import threading
from datetime import datetime
from typing import Dict, Any, Callable
import json
import os
from pathlib import Path


class MetricsCollector:
    """
    Collects and stores metrics for the sshBox system
    """
    def __init__(self, metrics_file: str = "/tmp/sshbox_metrics.json"):
        self.metrics_file = Path(metrics_file)
        self.metrics = {
            "start_time": datetime.utcnow().isoformat(),
            "requests": {
                "total": 0,
                "successful": 0,
                "failed": 0,
                "by_endpoint": {}
            },
            "sessions": {
                "created": 0,
                "destroyed": 0,
                "active": 0,
                "by_profile": {}
            },
            "performance": {
                "avg_provision_time": 0,
                "avg_response_time": 0,
                "provision_times": [],
                "response_times": []
            },
            "errors": {
                "total": 0,
                "by_type": {}
            },
            "system": {
                "last_updated": datetime.utcnow().isoformat()
            }
        }
        self.lock = threading.Lock()
        self._save_metrics()
    
    def increment_counter(self, category: str, subcategory: str = None, amount: int = 1):
        """Increment a counter metric"""
        with self.lock:
            if subcategory:
                if subcategory in self.metrics[category]:
                    self.metrics[category][subcategory] += amount
                else:
                    self.metrics[category][subcategory] = amount
            else:
                self.metrics[category] += amount
            self._update_timestamp()
            self._save_metrics()
    
    def record_timing(self, metric_name: str, value: float):
        """Record a timing metric"""
        with self.lock:
            if metric_name in self.metrics["performance"]:
                if isinstance(self.metrics["performance"][metric_name], list):
                    self.metrics["performance"][metric_name].append(value)
                    # Keep only the last 1000 values to prevent memory issues
                    if len(self.metrics["performance"][metric_name]) > 1000:
                        self.metrics["performance"][metric_name] = \
                            self.metrics["performance"][metric_name][-1000:]
                else:
                    # Calculate running average
                    current_avg = self.metrics["performance"][metric_name]
                    count = len(self.metrics["performance"].get(f"{metric_name}_list", [])) + 1
                    new_avg = ((current_avg * (count - 1)) + value) / count
                    self.metrics["performance"][metric_name] = new_avg
            else:
                self.metrics["performance"][metric_name] = value
            
            self._update_timestamp()
            self._save_metrics()
    
    def record_session_profile(self, profile: str):
        """Record a session creation by profile"""
        with self.lock:
            if profile in self.metrics["sessions"]["by_profile"]:
                self.metrics["sessions"]["by_profile"][profile] += 1
            else:
                self.metrics["sessions"]["by_profile"][profile] = 1
            self._update_timestamp()
            self._save_metrics()
    
    def record_error(self, error_type: str):
        """Record an error occurrence"""
        with self.lock:
            self.metrics["errors"]["total"] += 1
            if error_type in self.metrics["errors"]["by_type"]:
                self.metrics["errors"]["by_type"][error_type] += 1
            else:
                self.metrics["errors"]["by_type"][error_type] = 1
            self._update_timestamp()
            self._save_metrics()
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get current metrics"""
        with self.lock:
            return self.metrics.copy()
    
    def _update_timestamp(self):
        """Update the last updated timestamp"""
        self.metrics["system"]["last_updated"] = datetime.utcnow().isoformat()
    
    def _save_metrics(self):
        """Save metrics to file"""
        try:
            with open(self.metrics_file, 'w') as f:
                json.dump(self.metrics, f, indent=2)
        except Exception as e:
            print(f"Error saving metrics to {self.metrics_file}: {e}")
    
    def load_metrics(self):
        """Load metrics from file"""
        try:
            if self.metrics_file.exists():
                with open(self.metrics_file, 'r') as f:
                    loaded_metrics = json.load(f)
                    with self.lock:
                        self.metrics.update(loaded_metrics)
        except Exception as e:
            print(f"Error loading metrics from {self.metrics_file}: {e}")


# Global metrics collector instance
metrics = MetricsCollector()


def record_request(endpoint: str, success: bool = True):
    """Record an API request"""
    metrics.increment_counter("requests", "total")
    if success:
        metrics.increment_counter("requests", "successful")
    else:
        metrics.increment_counter("requests", "failed")
    
    # Record by endpoint
    if endpoint in metrics.metrics["requests"]["by_endpoint"]:
        metrics.metrics["requests"]["by_endpoint"][endpoint] += 1
    else:
        metrics.metrics["requests"]["by_endpoint"][endpoint] = 1


def record_session_creation(profile: str = "unknown"):
    """Record a session creation"""
    metrics.increment_counter("sessions", "created")
    metrics.record_session_profile(profile)


def record_session_destruction():
    """Record a session destruction"""
    metrics.increment_counter("sessions", "destroyed")


def record_error(error_type: str):
    """Record an error"""
    metrics.record_error(error_type)


def record_timing(metric_name: str, value: float):
    """Record a timing metric"""
    metrics.record_timing(metric_name, value)