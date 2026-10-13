"""`uv run python -m review` (dev) or the cl-review systemd unit (production)."""

from review.app import serve

serve()
