"""Web mode: an HTML Minesweeper game whose 自动操作 is driven by Jev.

The browser owns the *environment* (rules, board, clicks, visualisation); this
package owns the *agent*. The page posts the observed board to ``POST /decide``
and gets back the move plus everything needed to draw it: the Choice option
probabilities, the per-cell ``is_mine`` answers, the code-side facts, the danger
score, latency and token usage.

Reusing the same ``JevMinesweeperAgent`` the offline simulator uses means the web
game and ``jev-ms sim`` exercise one identical decision path — only the
environment differs.
"""

from .server import JevWebApp, SessionStats, build_handler, serve

__all__ = ["JevWebApp", "SessionStats", "build_handler", "serve"]
