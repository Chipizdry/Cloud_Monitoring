"""Gunicorn diagnostics for an API worker that stops responding."""

import faulthandler
import os
import signal
import sys


def post_worker_init(worker):
    # UvicornWorker resets SIGABRT to the default action. Restore Gunicorn's
    # handler so worker_abort can capture a traceback when the master times out.
    signal.signal(signal.SIGABRT, worker.handle_abort)


def worker_abort(worker):
    # Gunicorn calls this hook on SIGABRT, usually after WORKER TIMEOUT.
    # Write directly to stderr so the traceback does not depend on app logging.
    os.write(2, f"Gunicorn worker {worker.pid} aborted; Python thread stacks:\n".encode())
    faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
