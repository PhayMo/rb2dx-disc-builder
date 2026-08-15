"""Entry point for a packaged build: open the interface."""

import multiprocessing

from rb2dx.gui import app

if __name__ == "__main__":
    multiprocessing.freeze_support()
    app.main()
