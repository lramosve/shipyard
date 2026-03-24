"""Vercel serverless entry point — re-exports the FastAPI app."""

import os
import sys

# Add project root to path so shipyard package is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shipyard.main import app
