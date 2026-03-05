from contextlib import contextmanager
import logging
from api.connection_pool import get_db_connection
from api.exceptions import DatabaseError

logger = logging.getLogger("db_utils")

@contextmanager
def get_db_cursor():
    """Context manager for database cursor with error handling"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                yield cursor
                conn.commit()
            except Exception as e:
                conn.rollback()
                raise
            finally:
                cursor.close()
    except Exception as e:
        logger.error(f"Database error: {e}")
        if not isinstance(e, DatabaseError):
            raise DatabaseError(
                reason=str(e),
                operation="database_query"
            )
        raise
