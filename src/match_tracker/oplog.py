"""The operator's log: what the program says about itself, for whoever runs it.

It goes to `logs/app.log`, which is rotated at midnight with 30 files kept, and
to nothing else: not the console, so not the journal. It holds start-up, reloads
and refusals of team files, saves, errors with their traceback, and the refusals
of the limit on unknown tokens. It never holds a visitor's address or a link: a
request is named by the kind of page, and the one place that sees an address
(`limiter`) keeps it only in memory; tests/test_oplog.py checks it.
"""

import logging
import os
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

NAME = "app.log"
KEEP = 30  # files, one a day
FORMAT = "%(asctime)s %(levelname)s %(message)s"


class PrivateHandler(TimedRotatingFileHandler):
    """A rotating file only its owner can read: a new file after each rotation gets
    the mode too, which the standard handler would leave to the umask."""

    def _open(self):
        stream = super()._open()
        os.chmod(self.baseFilename, 0o600)
        return stream


def setup(logs_dir):
    """Send every record, uvicorn's included, to `logs_dir/app.log`, and none to
    the console. Returns the handler."""
    logs_dir = Path(logs_dir)
    logs_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(logs_dir, 0o700)
    handler = PrivateHandler(
        logs_dir / NAME, when="midnight", backupCount=KEEP, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter(FORMAT))
    root = logging.getLogger()
    for old in root.handlers[:]:  # a console handler would reach the journal
        root.removeHandler(old)
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    return handler
