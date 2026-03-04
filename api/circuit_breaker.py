#!/usr/bin/env python3
"""
Circuit Breaker implementation for sshBox
Prevents cascade failures and provides resilience
"""
import time
import threading
import logging
from typing import Callable, Any, Optional, Type, Tuple
from enum import Enum
from dataclasses import dataclass, field
from functools import wraps
import traceback

from api.exceptions import CircuitBreakerOpenError

logger = logging.getLogger("circuit_breaker")


class CircuitState(Enum):
    """Circuit breaker states"""
    CLOSED = "closed"      # Normal operation, requests flow through
    OPEN = "open"          # Failing, reject all requests immediately
    HALF_OPEN = "half_open"  # Testing if service recovered


@dataclass
class CircuitStats:
    """Statistics for circuit breaker"""
    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    rejected_calls: int = 0
    last_failure_time: Optional[float] = None
    last_success_time: Optional[float] = None
    last_state_change: float = field(default_factory=time.time)
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    
    def record_success(self):
        """Record a successful call"""
        self.total_calls += 1
        self.successful_calls += 1
        self.last_success_time = time.time()
        self.consecutive_successes += 1
        self.consecutive_failures = 0
    
    def record_failure(self):
        """Record a failed call"""
        self.total_calls += 1
        self.failed_calls += 1
        self.last_failure_time = time.time()
        self.consecutive_failures += 1
        self.consecutive_successes = 0
    
    def record_rejection(self):
        """Record a rejected call (circuit open)"""
        self.rejected_calls += 1
        self.total_calls += 1
    
    @property
    def success_rate(self) -> float:
        """Calculate success rate"""
        if self.total_calls == 0:
            return 1.0
        return self.successful_calls / self.total_calls


class CircuitBreaker:
    """
    Circuit Breaker pattern implementation for fault tolerance
    
    The circuit breaker monitors calls to a service and prevents calls
    when the failure rate exceeds a threshold. After a recovery timeout,
    it allows a test call to check if the service has recovered.
    
    Usage:
        # As a decorator
        @CircuitBreaker(failure_threshold=5, recovery_timeout=30)
        def provision_container(...):
            # provisioning logic
        
        # Or manually
        breaker = CircuitBreaker()
        try:
            result = breaker.call(provision_container, *args, **kwargs)
        except CircuitBreakerOpenError:
            # Handle circuit open
    """
    
    def __init__(
        self,
        name: str = "default",
        failure_threshold: int = 5,
        success_threshold: int = 3,
        recovery_timeout: int = 30,
        expected_exceptions: Tuple[Type[Exception], ...] = (Exception,),
        fallback: Optional[Callable] = None
    ):
        """
        Initialize circuit breaker
        
        Args:
            name: Name for identification in logs
            failure_threshold: Number of failures before opening circuit
            success_threshold: Number of successes in half-open to close circuit
            recovery_timeout: Seconds to wait before trying again (open -> half-open)
            expected_exceptions: Exception types that trigger circuit breaker
            fallback: Optional fallback function when circuit is open
        """
        self.name = name
        self.failure_threshold = failure_threshold
        self.success_threshold = success_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exceptions = expected_exceptions
        self.fallback = fallback
        
        self._state = CircuitState.CLOSED
        self._stats = CircuitStats()
        self._lock = threading.RLock()
        
        logger.info(f"Circuit breaker '{name}' initialized: "
                   f"failure_threshold={failure_threshold}, "
                   f"recovery_timeout={recovery_timeout}s")
    
    def __call__(self, func: Callable) -> Callable:
        """Decorator to wrap function with circuit breaker"""
        
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            return self.call(func, *args, **kwargs)
        
        return wrapper
    
    def call(self, func: Callable, *args, **kwargs) -> Any:
        """
        Execute function with circuit breaker logic
        
        Args:
            func: Function to execute
            *args: Positional arguments for function
            **kwargs: Keyword arguments for function
        
        Returns:
            Function result
        
        Raises:
            CircuitBreakerOpenError: If circuit is open
        """
        with self._lock:
            if self._state == CircuitState.OPEN:
                if self._should_attempt_reset():
                    self._transition_to(CircuitState.HALF_OPEN)
                    logger.info(f"Circuit '{self.name}': entering HALF_OPEN state")
                else:
                    self._stats.record_rejection()
                    retry_after = int(self.recovery_timeout - 
                                     (time.time() - self._stats.last_failure_time))
                    logger.warning(
                        f"Circuit '{self.name}': OPEN, rejecting call. "
                        f"Retry after {retry_after}s"
                    )
                    
                    if self.fallback:
                        logger.info(f"Circuit '{self.name}': using fallback")
                        return self.fallback(*args, **kwargs)
                    
                    raise CircuitBreakerOpenError(
                        operation=self.name,
                        retry_after=retry_after
                    )
        
        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
            
        except self.expected_exceptions as e:
            self._on_failure(e)
            raise
        except Exception as e:
            # Unexpected exception - also record as failure
            self._on_failure(e)
            raise
    
    def _should_attempt_reset(self) -> bool:
        """Check if enough time has passed to attempt reset"""
        if self._stats.last_failure_time is None:
            return True
        
        elapsed = time.time() - self._stats.last_failure_time
        return elapsed >= self.recovery_timeout
    
    def _on_success(self):
        """Handle successful execution"""
        with self._lock:
            self._stats.record_success()
            
            if self._state == CircuitState.HALF_OPEN:
                if self._stats.consecutive_successes >= self.success_threshold:
                    self._transition_to(CircuitState.CLOSED)
                    logger.info(
                        f"Circuit '{self.name}': recovered after "
                        f"{self._stats.consecutive_successes} successes"
                    )
    
    def _on_failure(self, exception: Exception):
        """Handle failed execution"""
        with self._lock:
            self._stats.record_failure()
            
            logger.warning(
                f"Circuit '{self.name}': failure {self._stats.consecutive_failures}/"
                f"{self.failure_threshold} - {type(exception).__name__}: {exception}"
            )
            
            if self._state == CircuitState.HALF_OPEN:
                # Any failure in half-open immediately opens circuit
                self._transition_to(CircuitState.OPEN)
                logger.warning(f"Circuit '{self.name}': failure in HALF_OPEN, reopening")
            elif self._stats.consecutive_failures >= self.failure_threshold:
                self._transition_to(CircuitState.OPEN)
                logger.warning(
                    f"Circuit '{self.name}': OPEN after "
                    f"{self._stats.consecutive_failures} consecutive failures"
                )
    
    def _transition_to(self, new_state: CircuitState):
        """Transition to a new state"""
        old_state = self._state
        self._state = new_state
        self._stats.last_state_change = time.time()
        
        logger.debug(f"Circuit '{self.name}': {old_state.value} -> {new_state.value}")
    
    @property
    def state(self) -> CircuitState:
        """Get current circuit state"""
        with self._lock:
            return self._state
    
    @property
    def stats(self) -> CircuitStats:
        """Get circuit statistics"""
        with self._lock:
            return self._stats.copy() if hasattr(self._stats, 'copy') else self._stats
    
    def reset(self):
        """Manually reset circuit breaker to closed state"""
        with self._lock:
            old_state = self._state
            self._transition_to(CircuitState.CLOSED)
            self._stats.consecutive_failures = 0
            self._stats.consecutive_successes = 0
            logger.info(f"Circuit '{self.name}': manually reset from {old_state.value}")
    
    def get_state_dict(self) -> dict:
        """Get circuit state as dictionary"""
        with self._lock:
            return {
                "name": self.name,
                "state": self._state.value,
                "stats": {
                    "total_calls": self._stats.total_calls,
                    "successful_calls": self._stats.successful_calls,
                    "failed_calls": self._stats.failed_calls,
                    "rejected_calls": self._stats.rejected_calls,
                    "success_rate": round(self._stats.success_rate, 3),
                    "consecutive_failures": self._stats.consecutive_failures,
                    "consecutive_successes": self._stats.consecutive_successes,
                },
                "last_failure_time": self._stats.last_failure_time,
                "last_success_time": self._stats.last_success_time,
                "last_state_change": self._stats.last_state_change,
                "config": {
                    "failure_threshold": self.failure_threshold,
                    "success_threshold": self.success_threshold,
                    "recovery_timeout": self.recovery_timeout,
                }
            }


# Global circuit breakers for different operations
# These can be imported and used throughout the application

provisioning_breaker = CircuitBreaker(
    name="provisioning",
    failure_threshold=5,
    success_threshold=3,
    recovery_timeout=60,
    expected_exceptions=(Exception,)
)

database_breaker = CircuitBreaker(
    name="database",
    failure_threshold=3,
    success_threshold=2,
    recovery_timeout=30,
    expected_exceptions=(Exception,)
)

redis_breaker = CircuitBreaker(
    name="redis",
    failure_threshold=3,
    success_threshold=2,
    recovery_timeout=15,
    expected_exceptions=(Exception,)
)

external_api_breaker = CircuitBreaker(
    name="external_api",
    failure_threshold=5,
    success_threshold=3,
    recovery_timeout=45,
    expected_exceptions=(Exception,)
)


class CircuitBreakerRegistry:
    """
    Registry for managing multiple circuit breakers
    """
    
    def __init__(self):
        self._breakers: dict[str, CircuitBreaker] = {}
        self._lock = threading.Lock()
    
    def register(self, breaker: CircuitBreaker):
        """Register a circuit breaker"""
        with self._lock:
            self._breakers[breaker.name] = breaker
            logger.info(f"Registered circuit breaker: {breaker.name}")
    
    def get(self, name: str) -> Optional[CircuitBreaker]:
        """Get a circuit breaker by name"""
        with self._lock:
            return self._breakers.get(name)
    
    def get_or_create(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: int = 30
    ) -> CircuitBreaker:
        """Get existing or create new circuit breaker"""
        with self._lock:
            if name not in self._breakers:
                breaker = CircuitBreaker(
                    name=name,
                    failure_threshold=failure_threshold,
                    recovery_timeout=recovery_timeout
                )
                self._breakers[name] = breaker
            return self._breakers[name]
    
    def get_all_states(self) -> dict:
        """Get state of all circuit breakers"""
        with self._lock:
            return {
                name: breaker.get_state_dict()
                for name, breaker in self._breakers.items()
            }
    
    def reset_all(self):
        """Reset all circuit breakers"""
        with self._lock:
            for breaker in self._breakers.values():
                breaker.reset()


# Global registry instance
registry = CircuitBreakerRegistry()

# Register default breakers
registry.register(provisioning_breaker)
registry.register(database_breaker)
registry.register(redis_breaker)
registry.register(external_api_breaker)
