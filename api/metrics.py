"""
Metrics collection for sshBox system
Provides monitoring and observability capabilities with Prometheus export
"""
import time
import threading
from datetime import datetime
from typing import Dict, Any, Optional, List
import json
import os
from pathlib import Path
from collections import defaultdict


class MetricsCollector:
    """
    Collects and stores metrics for the sshBox system
    Supports both JSON file storage and Prometheus exposition format
    """
    def __init__(self, metrics_file: str = "/tmp/sshbox_metrics.json"):
        self.metrics_file = Path(metrics_file)
        self.start_time = datetime.utcnow()
        self.lock = threading.Lock()
        
        # Counter metrics
        self.requests_total = 0
        self.requests_successful = 0
        self.requests_failed = 0
        self.requests_by_endpoint: Dict[str, int] = defaultdict(int)
        self.requests_by_status: Dict[int, int] = defaultdict(int)
        
        # Session metrics
        self.sessions_created = 0
        self.sessions_destroyed = 0
        self.sessions_by_profile: Dict[str, int] = defaultdict(int)
        
        # Error metrics
        self.errors_total = 0
        self.errors_by_type: Dict[str, int] = defaultdict(int)
        
        # Timing metrics (histogram-like)
        self.provision_times: List[float] = []
        self.response_times: List[float] = []
        self.request_durations: Dict[str, List[float]] = defaultdict(list)
        
        # Gauge metrics
        self.active_sessions = 0
        self.db_connections_active = 0
        self.db_connections_idle = 0
        
        # Save initial metrics
        self._save_metrics()
    
    def record_request(
        self,
        endpoint: str,
        success: bool = True,
        status_code: int = 200,
        process_time: float = 0.0
    ):
        """Record an API request with detailed metrics"""
        with self.lock:
            self.requests_total += 1
            if success:
                self.requests_successful += 1
            else:
                self.requests_failed += 1
            
            self.requests_by_endpoint[endpoint] += 1
            self.requests_by_status[status_code] += 1
            
            # Record response time
            if process_time > 0:
                self.response_times.append(process_time)
                self.request_durations[endpoint].append(process_time)
                
                # Keep only last 1000 values
                if len(self.response_times) > 1000:
                    self.response_times = self.response_times[-1000:]
                if len(self.request_durations[endpoint]) > 1000:
                    self.request_durations[endpoint] = self.request_durations[endpoint][-1000:]
            
            self._save_metrics()
    
    def record_session_creation(self, profile: str = "unknown"):
        """Record a session creation"""
        with self.lock:
            self.sessions_created += 1
            self.sessions_by_profile[profile] += 1
            self.active_sessions += 1
            self._save_metrics()
    
    def record_session_destruction(self):
        """Record a session destruction"""
        with self.lock:
            self.sessions_destroyed += 1
            self.active_sessions = max(0, self.active_sessions - 1)
            self._save_metrics()
    
    def record_error(self, error_type: str):
        """Record an error occurrence"""
        with self.lock:
            self.errors_total += 1
            self.errors_by_type[error_type] += 1
            self._save_metrics()
    
    def record_timing(self, metric_name: str, value: float):
        """Record a timing metric"""
        with self.lock:
            if metric_name == "provision_time":
                self.provision_times.append(value)
                if len(self.provision_times) > 1000:
                    self.provision_times = self.provision_times[-1000:]
            elif metric_name == "response_time":
                self.response_times.append(value)
                if len(self.response_times) > 1000:
                    self.response_times = self.response_times[-1000:]
            self._save_metrics()
    
    def update_db_connections(self, active: int = 0, idle: int = 0):
        """Update database connection pool metrics"""
        with self.lock:
            self.db_connections_active = active
            self.db_connections_idle = idle
            self._save_metrics()
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get current metrics as dictionary"""
        with self.lock:
            # Calculate averages and percentiles
            def calc_stats(values: List[float]) -> Dict[str, float]:
                if not values:
                    return {"count": 0, "avg": 0, "min": 0, "max": 0, "p50": 0, "p95": 0, "p99": 0}
                
                sorted_values = sorted(values)
                count = len(sorted_values)
                total = sum(sorted_values)
                
                return {
                    "count": count,
                    "avg": total / count,
                    "min": sorted_values[0],
                    "max": sorted_values[-1],
                    "p50": sorted_values[int(count * 0.5)],
                    "p95": sorted_values[int(count * 0.95)] if count > 20 else sorted_values[-1],
                    "p99": sorted_values[int(count * 0.99)] if count > 100 else sorted_values[-1]
                }
            
            return {
                "start_time": self.start_time.isoformat(),
                "uptime_seconds": (datetime.utcnow() - self.start_time).total_seconds(),
                "requests": {
                    "total": self.requests_total,
                    "successful": self.requests_successful,
                    "failed": self.requests_failed,
                    "by_endpoint": dict(self.requests_by_endpoint),
                    "by_status": {str(k): v for k, v in self.requests_by_status.items()},
                    "response_time": calc_stats(self.response_times)
                },
                "sessions": {
                    "created": self.sessions_created,
                    "destroyed": self.sessions_destroyed,
                    "active": self.active_sessions,
                    "by_profile": dict(self.sessions_by_profile),
                    "provision_time": calc_stats(self.provision_times)
                },
                "errors": {
                    "total": self.errors_total,
                    "by_type": dict(self.errors_by_type)
                },
                "database": {
                    "connections_active": self.db_connections_active,
                    "connections_idle": self.db_connections_idle
                }
            }
    
    def get_prometheus_metrics(self) -> str:
        """Get metrics in Prometheus exposition format"""
        with self.lock:
            lines = []
            lines.append("# HELP sshbox_uptime_seconds Time since service started")
            lines.append("# TYPE sshbox_uptime_seconds counter")
            lines.append(f"sshbox_uptime_seconds {(datetime.utcnow() - self.start_time).total_seconds()}")
            lines.append("")
            
            # Request metrics
            lines.append("# HELP sshbox_requests_total Total number of requests")
            lines.append("# TYPE sshbox_requests_total counter")
            lines.append(f"sshbox_requests_total {self.requests_total}")
            lines.append("")
            
            lines.append("# HELP sshbox_requests_successful Total number of successful requests")
            lines.append("# TYPE sshbox_requests_successful counter")
            lines.append(f"sshbox_requests_successful {self.requests_successful}")
            lines.append("")
            
            lines.append("# HELP sshbox_requests_failed Total number of failed requests")
            lines.append("# TYPE sshbox_requests_failed counter")
            lines.append(f"sshbox_requests_failed {self.requests_failed}")
            lines.append("")
            
            lines.append("# HELP sshbox_requests_by_endpoint Requests by endpoint")
            lines.append("# TYPE sshbox_requests_by_endpoint counter")
            for endpoint, count in self.requests_by_endpoint.items():
                lines.append(f'sshbox_requests_by_endpoint{{endpoint="{endpoint}"}} {count}')
            lines.append("")
            
            lines.append("# HELP sshbox_requests_by_status Requests by status code")
            lines.append("# TYPE sshbox_requests_by_status counter")
            for status_code, count in self.requests_by_status.items():
                lines.append(f'sshbox_requests_by_status{{status="{status_code}"}} {count}')
            lines.append("")
            
            # Session metrics
            lines.append("# HELP sshbox_sessions_created Total sessions created")
            lines.append("# TYPE sshbox_sessions_created counter")
            lines.append(f"sshbox_sessions_created {self.sessions_created}")
            lines.append("")
            
            lines.append("# HELP sshbox_sessions_destroyed Total sessions destroyed")
            lines.append("# TYPE sshbox_sessions_destroyed counter")
            lines.append(f"sshbox_sessions_destroyed {self.sessions_destroyed}")
            lines.append("")
            
            lines.append("# HELP sshbox_active_sessions Current active sessions")
            lines.append("# TYPE sshbox_active_sessions gauge")
            lines.append(f"sshbox_active_sessions {self.active_sessions}")
            lines.append("")
            
            lines.append("# HELP sshbox_sessions_by_profile Sessions by profile")
            lines.append("# TYPE sshbox_sessions_by_profile counter")
            for profile, count in self.sessions_by_profile.items():
                lines.append(f'sshbox_sessions_by_profile{{profile="{profile}"}} {count}')
            lines.append("")
            
            # Error metrics
            lines.append("# HELP sshbox_errors_total Total errors")
            lines.append("# TYPE sshbox_errors_total counter")
            lines.append(f"sshbox_errors_total {self.errors_total}")
            lines.append("")
            
            lines.append("# HELP sshbox_errors_by_type Errors by type")
            lines.append("# TYPE sshbox_errors_by_type counter")
            for error_type, count in self.errors_by_type.items():
                lines.append(f'sshbox_errors_by_type{{type="{error_type}"}} {count}')
            lines.append("")
            
            # Timing metrics
            if self.response_times:
                avg_response = sum(self.response_times) / len(self.response_times)
                lines.append("# HELP sshbox_response_time_avg Average response time in seconds")
                lines.append("# TYPE sshbox_response_time_avg gauge")
                lines.append(f"sshbox_response_time_avg {avg_response:.6f}")
                lines.append("")
            
            if self.provision_times:
                avg_provision = sum(self.provision_times) / len(self.provision_times)
                lines.append("# HELP sshbox_provision_time_avg Average provision time in seconds")
                lines.append("# TYPE sshbox_provision_time_avg gauge")
                lines.append(f"sshbox_provision_time_avg {avg_provision:.6f}")
                lines.append("")
            
            # Database metrics
            lines.append("# HELP sshbox_db_connections_active Active database connections")
            lines.append("# TYPE sshbox_db_connections_active gauge")
            lines.append(f"sshbox_db_connections_active {self.db_connections_active}")
            lines.append("")
            
            lines.append("# HELP sshbox_db_connections_idle Idle database connections")
            lines.append("# TYPE sshbox_db_connections_idle gauge")
            lines.append(f"sshbox_db_connections_idle {self.db_connections_idle}")
            
            return "\n".join(lines)
    
    def _save_metrics(self):
        """Save metrics to file"""
        try:
            # Ensure directory exists
            self.metrics_file.parent.mkdir(parents=True, exist_ok=True)
            
            with open(self.metrics_file, 'w') as f:
                json.dump(self.get_metrics(), f, indent=2)
        except Exception as e:
            print(f"Error saving metrics to {self.metrics_file}: {e}")


# Global metrics collector instance
_metrics_instance: Optional[MetricsCollector] = None


def get_metrics_collector(metrics_file: Optional[str] = None) -> MetricsCollector:
    """Get or create global metrics collector instance"""
    global _metrics_instance
    if _metrics_instance is None:
        if metrics_file is None:
            metrics_file = os.environ.get('SSHBOX_STORAGE_METRICS_FILE', '/tmp/sshbox_metrics.json')
        _metrics_instance = MetricsCollector(metrics_file)
    return _metrics_instance


# Convenience functions for backward compatibility
def record_request(endpoint: str, success: bool = True, status_code: int = 200, process_time: float = 0.0):
    """Record an API request"""
    get_metrics_collector().record_request(endpoint, success, status_code, process_time)


def record_session_creation(profile: str = "unknown"):
    """Record a session creation"""
    get_metrics_collector().record_session_creation(profile)


def record_session_destruction():
    """Record a session destruction"""
    get_metrics_collector().record_session_destruction()


def record_error(error_type: str):
    """Record an error"""
    get_metrics_collector().record_error(error_type)


def record_timing(metric_name: str, value: float):
    """Record a timing metric"""
    get_metrics_collector().record_timing(metric_name, value)