# leases/utils/sql_capture.py
import time
from typing import Callable, List, Dict, Any
from django.db import connection, transaction


class _Collector:
    def __init__(self): self.queries: List[Dict[str, Any]] = []

    def __call__(self, execute, sql, params, many, context):
        t0 = time.perf_counter()
        try:
            return execute(sql, params, many, context)
        finally:
            self.queries.append(
                {"sql": sql, "params": params, "ms": round((time.perf_counter()-t0)*1000, 2)})


def run_and_capture(fn: Callable[[], Any]) -> List[Dict[str, Any]]:
    col = _Collector()
    with connection.execute_wrapper(col):
        fn()
    return col.queries
