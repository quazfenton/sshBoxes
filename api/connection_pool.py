"""
Connection pooling for sshBox system
Optimizes database connection reuse and performance
"""
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Optional
import os


class SQLiteConnectionPool:
    """
    A simple connection pool for SQLite databases
    """
    def __init__(self, db_path: str, max_connections: int = 10, timeout: int = 30):
        self.db_path = db_path
        self.max_connections = max_connections
        self.timeout = timeout
        self.pool = []
        self.lock = threading.Lock()
        self.active_connections = 0
        
        # Initialize the pool with connections
        self._initialize_pool()
    
    def _initialize_pool(self):
        """Initialize the connection pool with a few connections"""
        for _ in range(min(3, self.max_connections)):  # Start with 3 connections
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self.pool.append(conn)
    
    @contextmanager
    def get_connection(self):
        """Get a connection from the pool"""
        conn = None
        start_time = time.time()
        
        while time.time() - start_time < self.timeout:
            with self.lock:
                if self.pool:
                    conn = self.pool.pop()
                    self.active_connections += 1
                    break
                elif self.active_connections < self.max_connections:
                    # Create a new connection if we're under the limit
                    conn = sqlite3.connect(self.db_path, check_same_thread=False)
                    self.active_connections += 1
                    break
            
            time.sleep(0.1)  # Wait a bit before trying again
        
        if conn is None:
            raise TimeoutError(f"Could not acquire database connection within {self.timeout} seconds")
        
        try:
            yield conn
        finally:
            # Rollback any uncommitted transactions to ensure clean state
            try:
                conn.rollback()
            except:
                # If rollback fails, we'll close the connection instead
                try:
                    conn.close()
                except:
                    pass  # Ignore errors when closing
                with self.lock:
                    self.active_connections -= 1
                return  # Exit early since connection is unusable

            # Return connection to pool
            with self.lock:
                if len(self.pool) < self.max_connections:
                    self.pool.append(conn)
                else:
                    # Close the connection if pool is full
                    conn.close()
                self.active_connections -= 1
    
    def close_all(self):
        """Close all connections in the pool"""
        with self.lock:
            for conn in self.pool:
                conn.close()
            self.pool.clear()


# Global connection pool instance
DB_PATH = os.environ.get('SQLITE_PATH', '/tmp/sshbox_sessions.db')
connection_pool = SQLiteConnectionPool(DB_PATH)


def get_db_connection():
    """Get a database connection from the pool"""
    return connection_pool.get_connection()