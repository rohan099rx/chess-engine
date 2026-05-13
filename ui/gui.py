import datetime
import math
import threading
import tkinter as tk
from pathlib import Path

from engine.board import Board
from engine.move_generator import MoveGenerator
from engine.notation import move_to_san
from engine import opening_book

try:
    from PIL import Image, ImageTk
except ImportError:  # Pillow is optional; fallback to vector pieces.
    Image = None
    ImageTk = None

PIECES = {
    "K": "♔", "Q": "♕", "R": "♖",
    "B": "♗", "N": "♘", "P": "♙",
    "k": "♚", "q": "♛", "r": "♜",
    "b": "♝", "n": "♞", "p": "♟",
    ".": "",
}

LIGHT_SQUARE    = "#F3F1E7"
LIGHT_SQUARE_2  = "#ECE8DC"
DARK_SQUARE     = "#6F8F5B"
DARK_SQUARE_2   = "#688654"
SELECTED_SQUARE = "#F7E36D"
CHECK_SQUARE    = "#E04F5F"
MOVE_OUTLINE    = "#4EA1FF"
BEST_FROM_COLOR = "#FFB020"
ARROW_COLOR     = "#E58B2A"
EVAL_WHITE      = "#F5F5F5"
EVAL_BLACK      = "#111111"

SQUARE_SIZE   = 80
EVAL_BAR_W    = 25
HISTORY_W     = 250
SUGGESTIONS_W = 340
PANEL_H       = 200
SEARCH_DEPTH  = 5
TOP_SUGGESTIONS = 10

_UI_BG        = "#111318"
_PANEL_BG     = "#1A1D24"
_PANEL_INNER  = "#202430"
_PANEL_BORDER = "#2B313D"
_PANEL_FG     = "#E7E9EE"
_TEXT_MUTED   = "#9AA3B2"
_TEXT_DIM     = "#6E7684"
_ACCENT       = "#6DD6A8"
_ACCENT_SOFT  = "#C7F0DD"
_WARNING      = "#FFCC4D"
_INFO         = "#8ACBFF"

_PIECE_WHITE_FILL = "#F7F3E8"
_PIECE_WHITE_EDGE = "#CDBFA4"
_PIECE_WHITE_TEXT = "#2B2F38"
_PIECE_BLACK_FILL = "#2A2F3A"
_PIECE_BLACK_EDGE = "#0F1116"
_PIECE_BLACK_TEXT = "#EDE6D6"

_BUTTON_BG    = "#252A35"
_BUTTON_FG    = "#E7E9EE"
_BUTTON_HOVER = "#303748"

_FONT_UI      = ("Helvetica Neue", 10)
_FONT_UI_BOLD = ("Helvetica Neue", 10, "bold")
_FONT_TITLE   = ("Helvetica Neue", 11, "bold")
_FONT_MONO    = ("Menlo", 10)
_FONT_MONO_SM = ("Menlo", 9)

_ASSET_ROOT = Path(__file__).resolve().parent.parent / "assets" / "chesscom"
_BOARD_IMAGE = _ASSET_ROOT / "boards" / "brown.png"
_PIECE_DIR = _ASSET_ROOT / "pieces" / "classic"
_PIECE_FILES = {
    "P": "wp.png", "N": "wn.png", "B": "wb.png", "R": "wr.png", "Q": "wq.png", "K": "wk.png",
    "p": "bp.png", "n": "bn.png", "b": "bb.png", "r": "br.png", "q": "bq.png", "k": "bk.png",
}


def _score_to_ratio(score):
    if score >= 900:
        return 1.0
    if score <= -900:
        return 0.0
    return 0.5 + 0.5 * math.tanh(score / 4.0)


def _board_from_snapshot(snapshot):
    board = Board()
    board.board = [row[:] for row in snapshot["board"]]
    board.turn = snapshot["turn"]
    board.en_passant_target = snapshot["en_passant_target"]
    board.halfmove_clock = snapshot["halfmove_clock"]
    board.castling_rights = snapshot["castling_rights"].copy()
    board.position_counts = snapshot["position_counts"].copy()
    board.refresh_zobrist_hash()
    return board


def _square_to_coord(square):
    row, col = square
    return f"{chr(ord('a') + col)}{8 - row}"


def _move_to_uci_text(move):
    if move is None:
        return "0000"
    start, end = move[0], move[1]
    promo = move[2] if len(move) > 2 else None
    text = _square_to_coord(start) + _square_to_coord(end)
    if promo is not None:
        text += promo.lower()
    return text


# ---------------------------------------------------------------------------
# Game-tree node
# ---------------------------------------------------------------------------

class GameNode:
    _counter = 0

    def __init__(self, position, move=None, move_san="", parent=None):
        GameNode._counter += 1
        self.id = GameNode._counter
        self.position = position   # board snapshot dict
        self.move = move           # (start, end, promo_choice) or None
        self.move_san = move_san   # e.g. "e4", "O-O", "Nf3+"
        self.parent = parent
        self.children = []         # children[0] = main line
        self.eval_score = None     # float (white-perspective), filled by analysis
        self.best_move = None      # engine best move for this position
        self.move_quality = None   # one of: brilliant|best|good|None


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class ChessGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Rohans Engine")
        self.root.configure(bg=_UI_BG)

        self.board = Board()
        self.mg = MoveGenerator(self.board)

        self.selected = None
        self.legal_moves = []
        self.alert_king_square = None
        self.status_var = tk.StringVar()

        # Game tree
        self._node_by_id = {}
        self.root_node = GameNode(self._capture())
        self.current_node = self.root_node

        self.best_move = None
        self._current_eval_score = 0.0
        self._last_root_scores = None
        self._last_pv = None
        self._last_root_tree = None  # list[(move, score, pv)]
        self._last_search_depth = 0
        self._search_generation = 0
        self._suggestion_move_by_tag = {}
        self._hover_suggestion_move = None
        self._nav_pos_var = tk.StringVar(value="")
        self._suggestions_position_key = None
        self._move_feedback_var = tk.StringVar(value="")
        self._book_text_var = tk.StringVar(value="")
        self._board_flipped = False

        board_size = 8 * SQUARE_SIZE
        self._load_assets(board_size)
        self._build_layout(board_size)

        self.canvas.bind("<Button-1>", self.on_click)
        # Global keybinds so navigation works even when focus is in a Text widget.
        self.root.bind_all("<Left>",  self.go_to_previous_position)
        self.root.bind_all("<Right>", self.go_to_next_position)
        self.root.bind_all("<Home>",  self.go_to_start)
        self.root.bind_all("<End>",   self.go_to_end)

        self.draw_board()
        self.draw_eval_bar(0)
        self.update_history_panel()
        self.update_move_suggestions()
        self._update_nav_buttons()
        self._start_engine_search()

    # --- layout construction ----------------------------------------------

    def _build_layout(self, board_size):
        content = tk.Frame(self.root, bg=_UI_BG)
        content.pack()

        # Top: Navigation buttons
        nav_frame = tk.Frame(content, bg=_PANEL_BG, pady=8, padx=12,
                     highlightthickness=1, highlightbackground=_PANEL_BORDER)
        nav_frame.pack(fill="x")

        nav_left = tk.Frame(nav_frame, bg=_PANEL_BG)
        nav_left.pack(side="left", fill="y")

        def _nav_btn(parent, text, cmd, width=3):
            b = tk.Button(
                parent,
                text=text,
                command=cmd,
                font=("Menlo", 11),
                bg=_PANEL_INNER,
                fg=_PANEL_FG,
                activebackground=_BUTTON_HOVER,
                activeforeground=_PANEL_FG,
                relief="flat",
                width=width,
                pady=4,
                cursor="hand2",
                highlightthickness=0,
                borderwidth=0,
                disabledforeground=_TEXT_DIM,
            )
            b.pack(side="left", padx=2)
            return b

        self._start_btn = _nav_btn(nav_left, "⏮", self.go_to_start)
        self._back_btn = _nav_btn(nav_left, "◀", self.go_to_previous_position)
        self._next_btn = _nav_btn(nav_left, "▶", self.go_to_next_position)
        self._end_btn = _nav_btn(nav_left, "⏭", self.go_to_end)

        tk.Frame(nav_frame, bg=_PANEL_BORDER, width=1).pack(
            side="left", fill="y", padx=10, pady=4
        )

        self._nav_pos_label = tk.Label(
            nav_frame,
            textvariable=self._nav_pos_var,
            bg=_PANEL_BG,
            fg=_PANEL_FG,
            font=_FONT_UI,
            anchor="w",
        )
        self._nav_pos_label.config(font=_FONT_MONO)
        self._nav_pos_label.pack(side="left")

        self._flip_btn = tk.Button(
            nav_frame,
            text="⇅ Flip",
            command=self._flip_board,
            font=_FONT_UI,
            bg=_PANEL_INNER,
            fg=_PANEL_FG,
            activebackground=_BUTTON_HOVER,
            activeforeground=_PANEL_FG,
            relief="flat",
            padx=10,
            pady=4,
            cursor="hand2",
            highlightthickness=0,
            borderwidth=0,
        )
        self._flip_btn.pack(side="right", padx=2)

        # Main content frame
        main_frame = tk.Frame(content, bg=_UI_BG)
        main_frame.pack(fill="both", expand=True)

        # Left: Vertical eval bar + board (chess.com-like)
        board_area = tk.Frame(main_frame, bg=_UI_BG)
        board_area.pack(side="left")

        # Vertical eval bar (left of board)
        self.eval_canvas = tk.Canvas(
            board_area,
            width=EVAL_BAR_W,
            height=board_size,
            bg=EVAL_BLACK,
            highlightthickness=1,
            highlightbackground=_PANEL_BORDER,
        )
        self.eval_canvas.pack(side="left")

        # Board canvas (right of the bar)
        self.canvas = tk.Canvas(
            board_area,
            width=board_size,
            height=board_size,
            bg="#111318",
            highlightthickness=0,
        )
        self.canvas.pack(side="left")

        # Center: History panel
        hist_frame = tk.Frame(main_frame, bg=_PANEL_BG, padx=8, pady=8,
                       width=HISTORY_W, highlightthickness=1,
                       highlightbackground=_PANEL_BORDER)
        hist_frame.pack(side="left", fill="both", expand=True)
        hist_frame.pack_propagate(False)

        tk.Label(hist_frame, text="MOVES", bg=_PANEL_BG, fg=_TEXT_DIM,
             font=_FONT_TITLE, anchor="w").pack(fill="x", pady=(0, 6))

        text_frame = tk.Frame(hist_frame, bg=_PANEL_BG)
        text_frame.pack(fill="both", expand=True)

        scrollbar = tk.Scrollbar(text_frame, bg=_PANEL_BG,
                      troughcolor="#2A303C", width=10)
        scrollbar.pack(side="right", fill="y")

        self.history_text = tk.Text(
            text_frame,
            wrap=tk.WORD,
            font=_FONT_UI,
            state="disabled",
            bg=_PANEL_INNER,
            fg=_PANEL_FG,
            insertbackground=_PANEL_FG,
            selectbackground="#343B4C",
            relief="flat",
            padx=6, pady=6,
            cursor="",
            yscrollcommand=scrollbar.set,
        )
        self.history_text.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=self.history_text.yview)

        self.history_text.tag_config("mn",   foreground=_TEXT_DIM)
        self.history_text.tag_config("wm",   foreground=_PANEL_FG)
        self.history_text.tag_config("bm",   foreground="#C9CEDA")
        self.history_text.tag_config("cur",  background="#EBCB7C",
                                      foreground="#1A1D24")
        self.history_text.tag_config("var",  foreground=_TEXT_MUTED,
                                      font=_FONT_UI)
        self.history_text.tag_config("vb",   foreground=_TEXT_DIM)
        self.history_text.tag_config("q_brilliant", foreground=_INFO, font=_FONT_UI_BOLD)
        self.history_text.tag_config("q_best", foreground=_ACCENT, font=_FONT_UI_BOLD)
        self.history_text.tag_config("q_good", foreground=_ACCENT_SOFT, font=_FONT_UI_BOLD)

        self.history_text.bind("<Button-1>", self._on_history_click)
        self.history_text.bind("<Motion>",   self._on_history_motion)

        self._pgn_btn = tk.Button(
            hist_frame, text="Copy PGN", command=self.export_pgn,
            font=_FONT_UI, bg=_BUTTON_BG, fg=_BUTTON_FG,
            activebackground=_BUTTON_HOVER, activeforeground=_BUTTON_FG,
            relief="flat", padx=6, pady=5, highlightthickness=0, borderwidth=0,
        )
        self._pgn_btn.pack(fill="x", pady=(6, 0))

        # Right: Suggestions and position analysis
        right_frame = tk.Frame(main_frame, bg=_PANEL_BG, padx=10, pady=10,
                       width=SUGGESTIONS_W, highlightthickness=1,
                       highlightbackground=_PANEL_BORDER)
        right_frame.pack(side="left", fill="both", expand=True)
        right_frame.pack_propagate(False)

        # Suggestions panel
        tk.Label(right_frame, text="BEST MOVES", bg=_PANEL_BG, fg=_TEXT_DIM,
             font=_FONT_TITLE, anchor="w").pack(fill="x", pady=(0, 6))

        suggestions_text_frame = tk.Frame(right_frame, bg=_PANEL_BG)
        suggestions_text_frame.pack(fill="both", expand=False, pady=(0, 12))

        sugg_scrollbar = tk.Scrollbar(suggestions_text_frame, bg=_PANEL_BG,
                           troughcolor="#2A303C", width=10)
        sugg_scrollbar.pack(side="right", fill="y")

        self.suggestions_text = tk.Text(
            suggestions_text_frame,
            wrap=tk.WORD,
            font=_FONT_UI,
            state="disabled",
            bg=_PANEL_INNER,
            fg=_ACCENT,
            insertbackground=_ACCENT,
            height=8,
            relief="flat",
            padx=8, pady=8,
            cursor="",
            yscrollcommand=sugg_scrollbar.set,
        )
        self.suggestions_text.pack(side="left", fill="both", expand=True)
        sugg_scrollbar.config(command=self.suggestions_text.yview)

        # Configuration for better move display
        self.suggestions_text.tag_config("move",    foreground=_ACCENT, font=_FONT_TITLE)
        self.suggestions_text.tag_config("eval",    foreground=_WARNING, font=_FONT_UI)
        self.suggestions_text.tag_config("cont",    foreground=_INFO, font=_FONT_MONO_SM)

        self.suggestions_text.bind("<Button-1>", self._on_suggestion_click)
        self.suggestions_text.bind("<Motion>",   self._on_suggestion_motion)

        # Move feedback
        tk.Label(right_frame, text="MOVE FEEDBACK", bg=_PANEL_BG, fg=_TEXT_DIM,
             font=_FONT_TITLE, anchor="w").pack(fill="x", pady=(0, 6))

        self._move_feedback_label = tk.Label(
            right_frame,
            textvariable=self._move_feedback_var,
            bg=_PANEL_INNER,
            fg=_TEXT_MUTED,
            font=_FONT_UI_BOLD,
            anchor="w",
            padx=8,
            pady=8,
        )
        self._move_feedback_label.pack(fill="x", pady=(0, 12))

        # Opening explorer
        tk.Label(right_frame, text="OPENING EXPLORER", bg=_PANEL_BG, fg=_TEXT_DIM,
             font=_FONT_TITLE, anchor="w").pack(fill="x", pady=(0, 6))

        book_frame = tk.Frame(right_frame, bg=_PANEL_BG)
        book_frame.pack(fill="both", expand=True)

        book_scroll = tk.Scrollbar(book_frame, bg=_PANEL_BG, troughcolor="#2A303C", width=10)
        book_scroll.pack(side="right", fill="y")

        self.book_text = tk.Text(
            book_frame,
            wrap=tk.WORD,
            font=_FONT_UI,
            state="disabled",
            bg=_PANEL_INNER,
            fg=_PANEL_FG,
            insertbackground=_PANEL_FG,
            height=6,
            relief="flat",
            padx=8,
            pady=8,
            cursor="",
            yscrollcommand=book_scroll.set,
        )
        self.book_text.pack(side="left", fill="both", expand=True)
        book_scroll.config(command=self.book_text.yview)

        self.book_text.tag_config("hdr", foreground=_TEXT_MUTED, font=_FONT_UI_BOLD)
        self.book_text.tag_config("mv", foreground=_ACCENT, font=_FONT_TITLE)
        self.book_text.tag_config("mut", foreground=_TEXT_MUTED, font=_FONT_UI)

        # Status bar
        self.status_label = tk.Label(
            self.root, textvariable=self.status_var,
            font=_FONT_TITLE, anchor="w", padx=10, pady=8,
            bg=_PANEL_BG, fg=_PANEL_FG
        )
        self.status_label.pack(fill="x")

    # --- board snapshots --------------------------------------------------

    def _capture(self):
        return {
            "board": [row[:] for row in self.board.board],
            "turn": self.board.turn,
            "en_passant_target": self.board.en_passant_target,
            "halfmove_clock": self.board.halfmove_clock,
            "castling_rights": self.board.castling_rights.copy(),
            "position_counts": self.board.position_counts.copy(),
        }

    def _restore(self, position):
        self.board.board = [row[:] for row in position["board"]]
        self.board.turn = position["turn"]
        self.board.en_passant_target = position["en_passant_target"]
        self.board.halfmove_clock = position["halfmove_clock"]
        self.board.castling_rights = position["castling_rights"].copy()
        self.board.position_counts = position["position_counts"].copy()
        self.board.refresh_zobrist_hash()

    # --- game-tree navigation ---------------------------------------------

    def navigate_to_node(self, node):
        self.current_node = node
        self._restore(node.position)
        self.clear_selection()
        self.best_move = None
        self.update_history_panel()
        self.update_move_suggestions()
        self._update_nav_buttons()
        self.draw_board()
        self._start_engine_search()

    def _set_move_feedback(self, text, kind):
        self._move_feedback_var.set(text)
        if kind in ("brilliant", "best", "good"):
            fg = _ACCENT
        elif kind in ("inaccuracy",):
            fg = _WARNING
        elif kind in ("mistake", "blunder"):
            fg = CHECK_SQUARE
        else:
            fg = _TEXT_MUTED
        self._move_feedback_label.config(fg=fg)

    def _update_move_feedback(self):
        node = self.current_node
        parent = node.parent
        if parent is None or node.move is None:
            self._set_move_feedback("", "")
            return
        if parent.eval_score is None or node.eval_score is None:
            self._set_move_feedback("Analyzing move…", "")
            return

        mover = parent.position.get("turn")
        delta = node.eval_score - parent.eval_score
        mover_delta = delta if mover == "white" else -delta
        drop = -mover_delta

        is_best = parent.best_move is not None and node.move == parent.best_move

        label, kind = self._classify_move_quality(is_best, mover_delta, drop)

        cp = int(round(drop * 100))
        move_text = node.move_san or _move_to_uci_text(node.move)
        suffix = f" (−{cp}cp)" if cp > 0 else ""
        self._set_move_feedback(f"{move_text}: {label}{suffix}", kind)

        # Store the three requested concepts explicitly on the node.
        node.move_quality = kind if kind in ("brilliant", "best", "good") else None

    def _classify_move_quality(self, is_best, mover_delta, drop):
        """Classify the move.

        This is intentionally simple and deterministic (no tactics detection):
        - Brilliant: best move AND improves eval by >= 1.5 pawns for the mover
        - Best: engine best move
        - Good: small eval drop (<= 0.20 pawns)

        Anything else is categorized but not marked in the move list.
        """
        if is_best and mover_delta >= 1.5:
            return "Brilliant", "brilliant"
        if is_best:
            return "Best Move", "best"
        if drop <= 0.20:
            return "Good", "good"
        if drop <= 0.80:
            return "Inaccuracy", "inaccuracy"
        if drop <= 2.00:
            return "Mistake", "mistake"
        return "Blunder", "blunder"

    def _quality_marker(self, node):
        if node.move_quality == "brilliant":
            return "!!", "q_brilliant"
        if node.move_quality == "best":
            return "!", "q_best"
        if node.move_quality == "good":
            return "!?", "q_good"
        return "", None

    def _update_opening_explorer(self):
        self.book_text.config(state="normal")
        self.book_text.delete("1.0", "end")

        entries = opening_book.get_book_entries(self.board, limit=8)
        if not entries:
            self.book_text.insert("end", "No book moves for this position.\n", "mut")
            self.book_text.insert(
                "end",
                "\nNote: win% requires a games database (not included).\n",
                "mut",
            )
            self.book_text.config(state="disabled")
            return

        is_white = self.board.turn == "white"
        legal = set(self.mg.generate_all_legal_moves(is_white))
        entries = [(m, w) for (m, w) in entries if m in legal]

        total_w = sum(w for _, w in entries) or 1
        self.book_text.insert("end", "Move   Freq   Win%\n", "hdr")

        for move, weight in entries:
            start, end, promo = move
            try:
                san = move_to_san(self.board, self.mg, start, end, promo)
            except Exception:
                san = _move_to_uci_text(move)
            pct = int(round((weight / total_w) * 100))
            self.book_text.insert("end", f"{san}", "mv")
            self.book_text.insert("end", f"   {pct:>3d}%   —\n", "mut")

        self.book_text.insert(
            "end",
            "\nFreq is based on Polyglot book weights.\n",
            "mut",
        )
        self.book_text.config(state="disabled")

    def record_move(self, start, end, san, promotion_choice):
        """Add a move to the tree. If it already exists as a child, just move
        the cursor there (no duplicate nodes)."""
        move_key = (start, end, promotion_choice)
        for child in self.current_node.children:
            if child.move == move_key:
                self.current_node = child
                self.update_history_panel()
                self._update_nav_buttons()
                return
        node = GameNode(
            self._capture(),
            move=move_key,
            move_san=san,
            parent=self.current_node,
        )
        self._node_by_id[node.id] = node
        self.current_node.children.append(node)
        self.current_node = node
        self.update_history_panel()
        self._update_nav_buttons()

    def go_to_previous_position(self, event=None):
        if self.current_node.parent is None:
            return
        self.navigate_to_node(self.current_node.parent)

    def go_to_next_position(self, event=None):
        if not self.current_node.children:
            return
        self.navigate_to_node(self.current_node.children[0])

    def go_to_start(self, event=None):
        node = self.current_node
        while node.parent is not None:
            node = node.parent
        if node is not self.current_node:
            self.navigate_to_node(node)

    def go_to_end(self, event=None):
        node = self.current_node
        while node.children:
            node = node.children[0]
        if node is not self.current_node:
            self.navigate_to_node(node)

    def _flip_board(self):
        self._board_flipped = not self._board_flipped
        self.draw_board()

    def _display_to_board(self, row, col):
        if self._board_flipped:
            return 7 - row, 7 - col
        return row, col

    def _board_to_display(self, row, col):
        if self._board_flipped:
            return 7 - row, 7 - col
        return row, col

    def _update_nav_buttons(self):
        if None in (
            getattr(self, "_back_btn", None),
            getattr(self, "_next_btn", None),
            getattr(self, "_start_btn", None),
            getattr(self, "_end_btn", None),
        ):
            return
        can_back = self.current_node.parent is not None
        can_next = bool(self.current_node.children)
        self._start_btn.config(state=("normal" if can_back else "disabled"))
        self._back_btn.config(state=("normal" if can_back else "disabled"))
        self._next_btn.config(state=("normal" if can_next else "disabled"))
        self._end_btn.config(state=("normal" if can_next else "disabled"))

        ply = 0
        n = self.current_node
        while n is not None and n.parent is not None:
            ply += 1
            n = n.parent
        move_num = ply // 2 + 1
        side = "White" if self.board.turn == "white" else "Black"
        self._nav_pos_var.set(f"Ply {ply}  •  Move {move_num}  •  {side} to move")

    # --- history panel ----------------------------------------------------

    def update_history_panel(self):
        self.history_text.config(state="normal")
        self.history_text.delete("1.0", "end")
        self._render_subtree(self.root_node, 1, True, after_var=False)
        self.history_text.config(state="disabled")
        # Scroll to the current move
        tag = f"nd{self.current_node.id}"
        ranges = self.history_text.tag_ranges(tag)
        if ranges:
            self.history_text.see(ranges[0])

    def _render_subtree(self, node, move_num, is_white, after_var):
        """Recursively write the game tree into self.history_text."""
        if not node.children:
            return

        main = node.children[0]

        # Move-number prefix
        if is_white or after_var:
            self.history_text.insert(
                "end",
                f"{move_num}. " if is_white else f"{move_num}… ",
                "mn",
            )

        # Main move text (clickable, highlighted if current)
        is_cur = main is self.current_node
        ntag = f"nd{main.id}"
        style = "cur" if is_cur else ("wm" if is_white else "bm")
        self.history_text.insert("end", main.move_san, (ntag, style))
        marker, qtag = self._quality_marker(main)
        if marker and qtag:
            self.history_text.insert("end", marker, (ntag, style, qtag))
        self.history_text.insert("end", " ", (ntag, style))

        # Inline variations
        had_var = False
        for var in node.children[1:]:
            had_var = True
            self.history_text.insert("end", "(", "vb")
            self.history_text.insert(
                "end",
                f"{move_num}. " if is_white else f"{move_num}… ",
                "mn",
            )
            is_cur_v = var is self.current_node
            vtag = f"nd{var.id}"
            vstyle = "cur" if is_cur_v else "var"
            self.history_text.insert("end", var.move_san, (vtag, vstyle))
            marker, qtag = self._quality_marker(var)
            if marker and qtag:
                self.history_text.insert("end", marker, (vtag, vstyle, qtag))
            self.history_text.insert("end", " ", (vtag, vstyle))
            next_vn = move_num + (0 if is_white else 1)
            self._render_subtree(var, next_vn, not is_white, after_var=False)
            self.history_text.insert("end", ") ", "vb")

        # Continue down the main line
        next_mn = move_num + (0 if is_white else 1)
        self._render_subtree(
            main, next_mn, not is_white,
            after_var=had_var and is_white,
        )

    def _on_history_click(self, event):
        idx = self.history_text.index(f"@{event.x},{event.y}")
        for tag in self.history_text.tag_names(idx):
            if tag.startswith("nd"):
                node = self._node_by_id.get(int(tag[2:]))
                if node:
                    self.navigate_to_node(node)
                return

    def _on_history_motion(self, event):
        idx = self.history_text.index(f"@{event.x},{event.y}")
        is_move = any(t.startswith("nd") for t in self.history_text.tag_names(idx))
        self.history_text.config(cursor="hand2" if is_move else "")

    # --- PGN export -------------------------------------------------------

    def export_pgn(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self._build_pgn())
        self._pgn_btn.config(text="Copied!")
        self.root.after(2000, lambda: self._pgn_btn.config(text="Copy PGN"))

    def _build_pgn(self):
        date_str = datetime.date.today().strftime("%Y.%m.%d")
        headers = "\n".join([
            '[Event "?"]', '[Site "?"]', f'[Date "{date_str}"]',
            '[White "?"]', '[Black "?"]', '[Result "*"]',
        ])
        tokens = self._pgn_tokens(self.root_node, 1, True, after_var=False)
        tokens.append("*")
        return headers + "\n\n" + " ".join(tokens)

    def _pgn_tokens(self, node, move_num, is_white, after_var):
        if not node.children:
            return []
        tokens = []
        main = node.children[0]
        if is_white or after_var:
            tokens.append(f"{move_num}." if is_white else f"{move_num}...")
        tokens.append(main.move_san)
        had_var = False
        for var in node.children[1:]:
            had_var = True
            tokens.append("(")
            tokens.append(f"{move_num}." if is_white else f"{move_num}...")
            tokens.append(var.move_san)
            next_vn = move_num + (0 if is_white else 1)
            tokens.extend(self._pgn_tokens(var, next_vn, not is_white, False))
            tokens.append(")")
        next_mn = move_num + (0 if is_white else 1)
        tokens.extend(
            self._pgn_tokens(main, next_mn, not is_white,
                              after_var=had_var and is_white)
        )
        return tokens

    # --- selection helpers ------------------------------------------------

    def clear_selection(self):
        self.selected = None
        self.legal_moves = []
        self.alert_king_square = None

    def is_current_turn_piece(self, piece):
        if piece == ".":
            return False
        return piece.isupper() if self.board.turn == "white" else piece.islower()

    def _load_assets(self, board_size):
        self.board_image_tk = None
        self.piece_images = {}

        if Image is None or ImageTk is None:
            return

        if not _BOARD_IMAGE.exists() or not _PIECE_DIR.exists():
            return

        try:
            board_img = Image.open(_BOARD_IMAGE).convert("RGBA")
            board_img = board_img.resize((board_size, board_size), Image.LANCZOS)
            self.board_image_tk = ImageTk.PhotoImage(board_img)

            piece_size = int(SQUARE_SIZE * 0.82)
            for piece, filename in _PIECE_FILES.items():
                path = _PIECE_DIR / filename
                if not path.exists():
                    continue
                img = Image.open(path).convert("RGBA")
                img = img.resize((piece_size, piece_size), Image.LANCZOS)
                self.piece_images[piece] = ImageTk.PhotoImage(img)
        except Exception:
            self.board_image_tk = None
            self.piece_images = {}

    def square_color(self, row, col):
        return LIGHT_SQUARE if (row + col) % 2 == 0 else DARK_SQUARE

    def _square_texture_color(self, row, col):
        if (row + col) % 2 == 0:
            return LIGHT_SQUARE_2
        return DARK_SQUARE_2

    def _draw_piece(self, row, col, piece):
        if piece == ".":
            return
        cx = col * SQUARE_SIZE + SQUARE_SIZE // 2
        cy = row * SQUARE_SIZE + SQUARE_SIZE // 2
        if piece in self.piece_images:
            self.canvas.create_image(cx, cy, image=self.piece_images[piece], anchor="center")
            return

        is_white = piece.isupper()
        kind = piece.upper()

        fill = _PIECE_WHITE_FILL if is_white else _PIECE_BLACK_FILL
        edge = _PIECE_WHITE_EDGE if is_white else _PIECE_BLACK_EDGE
        highlight = _PIECE_WHITE_TEXT if is_white else _PIECE_BLACK_TEXT

        r = SQUARE_SIZE * 0.30
        base_w = r * 1.25
        base_h = r * 0.38
        shadow = SQUARE_SIZE * 0.04

        # Base shadow
        self.canvas.create_oval(
            cx - base_w + shadow, cy + r * 0.7 + shadow,
            cx + base_w + shadow, cy + r * 0.7 + base_h + shadow,
            fill="#0B0D12", outline=""
        )

        # Base plate
        self.canvas.create_oval(
            cx - base_w, cy + r * 0.7,
            cx + base_w, cy + r * 0.7 + base_h,
            fill=fill, outline=edge, width=2
        )

        if kind == "P":
            self.canvas.create_oval(
                cx - r * 0.6, cy - r * 0.1,
                cx + r * 0.6, cy + r * 0.8,
                fill=fill, outline=edge, width=2
            )
            self.canvas.create_oval(
                cx - r * 0.4, cy - r * 0.75,
                cx + r * 0.4, cy - r * 0.05,
                fill=fill, outline=edge, width=2
            )
        elif kind == "R":
            self.canvas.create_rectangle(
                cx - r * 0.8, cy - r * 0.1,
                cx + r * 0.8, cy + r * 0.75,
                fill=fill, outline=edge, width=2
            )
            for i in [-0.6, 0.0, 0.6]:
                self.canvas.create_rectangle(
                    cx + i * r - r * 0.18, cy - r * 0.75,
                    cx + i * r + r * 0.18, cy - r * 0.25,
                    fill=fill, outline=edge, width=2
                )
        elif kind == "B":
            self.canvas.create_oval(
                cx - r * 0.55, cy - r * 0.2,
                cx + r * 0.55, cy + r * 0.9,
                fill=fill, outline=edge, width=2
            )
            self.canvas.create_oval(
                cx - r * 0.35, cy - r * 0.85,
                cx + r * 0.35, cy - r * 0.25,
                fill=fill, outline=edge, width=2
            )
            self.canvas.create_line(
                cx, cy - r * 0.75, cx, cy - r * 0.05,
                fill=highlight, width=2
            )
        elif kind == "N":
            self.canvas.create_oval(
                cx - r * 0.6, cy + r * 0.05,
                cx + r * 0.6, cy + r * 0.8,
                fill=fill, outline=edge, width=2
            )
            points = [
                cx - r * 0.7, cy + r * 0.6,
                cx - r * 0.4, cy - r * 0.7,
                cx + r * 0.1, cy - r * 0.6,
                cx + r * 0.45, cy - r * 0.1,
                cx + r * 0.2, cy + r * 0.5,
            ]
            self.canvas.create_polygon(
                points, fill=fill, outline=edge, width=2, smooth=True
            )
            self.canvas.create_oval(
                cx + r * 0.05, cy - r * 0.45,
                cx + r * 0.2, cy - r * 0.3,
                fill=highlight, outline=""
            )
        elif kind == "Q":
            self.canvas.create_oval(
                cx - r * 0.7, cy - r * 0.05,
                cx + r * 0.7, cy + r * 0.9,
                fill=fill, outline=edge, width=2
            )
            for i in [-0.6, 0.0, 0.6]:
                self.canvas.create_oval(
                    cx + i * r - r * 0.18, cy - r * 0.75,
                    cx + i * r + r * 0.18, cy - r * 0.4,
                    fill=fill, outline=edge, width=2
                )
        elif kind == "K":
            self.canvas.create_rectangle(
                cx - r * 0.6, cy - r * 0.1,
                cx + r * 0.6, cy + r * 0.85,
                fill=fill, outline=edge, width=2
            )
            self.canvas.create_line(
                cx, cy - r * 0.85, cx, cy - r * 0.3,
                fill=edge, width=3
            )
            self.canvas.create_line(
                cx - r * 0.25, cy - r * 0.6, cx + r * 0.25, cy - r * 0.6,
                fill=edge, width=3
            )

    def get_check_highlights(self):
        highlights = set()
        for is_white in (True, False):
            if self.mg.is_in_check(is_white):
                king_sq = self.mg.find_king(is_white)
                if king_sq:
                    highlights.add(king_sq)
        if self.alert_king_square:
            highlights.add(self.alert_king_square)
        return highlights

    def needs_promotion(self, start, end):
        piece = self.board.get_piece(start[0], start[1])
        return (piece == "P" and end[0] == 0) or (piece == "p" and end[0] == 7)

    def ask_promotion_choice(self, piece):
        color = "White" if piece.isupper() else "Black"
        chosen = {"value": None}
        dialog = tk.Toplevel(self.root)
        dialog.title("Pawn Promotion")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)
        tk.Label(dialog, text=f"{color} pawn promotion",
                 font=("Arial", 12, "bold"), padx=16, pady=10).pack()
        row_frame = tk.Frame(dialog, padx=12, pady=8)
        row_frame.pack()
        def choose(c):
            chosen["value"] = c
            dialog.destroy()
        for c in ["Q", "R", "B", "N"]:
            pkey = c if piece.isupper() else c.lower()
            tk.Button(row_frame, text=PIECES[pkey], font=("Arial", 28),
                      width=2, command=lambda v=c: choose(v)).pack(
                side="left", padx=6)
        tk.Button(dialog, text="Cancel", command=dialog.destroy,
                  padx=10, pady=4).pack(pady=(0, 12))
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        dialog.wait_window()
        return chosen["value"]

    # --- drawing ----------------------------------------------------------

    def draw_board(self):
        self.canvas.delete("all")
        check_highlights = self.get_check_highlights()
        game_status = self.mg.get_game_status()
        self.status_var.set(game_status["message"])

        if self.board_image_tk is not None:
            self.canvas.create_image(0, 0, image=self.board_image_tk, anchor="nw")

        for drow in range(8):
            for dcol in range(8):
                row, col = self._display_to_board(drow, dcol)
                x1, y1 = dcol * SQUARE_SIZE, drow * SQUARE_SIZE
                x2, y2 = x1 + SQUARE_SIZE, y1 + SQUARE_SIZE

                if self.board_image_tk is None:
                    base = self.square_color(drow, dcol)
                    tex = self._square_texture_color(drow, dcol)
                    self.canvas.create_rectangle(
                        x1, y1, x2, y2, fill=base, outline="")
                    # Subtle texture: diagonals and soft speckles
                    self.canvas.create_line(
                        x1 + 4, y1 + 6, x2 - 6, y2 - 4, fill=tex, width=1)
                    self.canvas.create_line(
                        x1 + 6, y1 + 12, x2 - 12, y2 - 6, fill=tex, width=1)
                    self.canvas.create_rectangle(
                        x1 + 8, y1 + 8, x1 + 16, y1 + 16, fill=tex, outline="")

                if self.selected == (row, col):
                    self.canvas.create_rectangle(
                        x1, y1, x2, y2, fill=SELECTED_SQUARE,
                        stipple="gray50", outline="")

                if (row, col) in check_highlights:
                    self.canvas.create_rectangle(
                        x1, y1, x2, y2, fill=CHECK_SQUARE,
                        stipple="gray50", outline="")

                if (row, col) in self.legal_moves:
                    target_piece = self.board.get_piece(row, col)
                    if target_piece == ".":
                        r = int(SQUARE_SIZE * 0.12)
                        self.canvas.create_oval(
                            x1 + SQUARE_SIZE // 2 - r,
                            y1 + SQUARE_SIZE // 2 - r,
                            x1 + SQUARE_SIZE // 2 + r,
                            y1 + SQUARE_SIZE // 2 + r,
                            fill=MOVE_OUTLINE, outline="",
                            stipple="gray50"
                        )
                    else:
                        self.canvas.create_oval(
                            x1 + 6, y1 + 6, x2 - 6, y2 - 6,
                            outline=MOVE_OUTLINE, width=4)

                # Board coordinates (files/ranks) like chess.com.
                # Files (a-h) on the bottom row, ranks (8-1) on the left column.
                is_dark = ((drow + dcol) & 1) == 1
                coord_color = _PANEL_FG if is_dark else _TEXT_MUTED
                if drow == 7:
                    file_char = chr(ord('a') + col)
                    self.canvas.create_text(
                        x2 - 6, y2 - 6,
                        text=file_char,
                        font=_FONT_MONO_SM,
                        fill=coord_color,
                        anchor="se",
                    )
                if dcol == 0:
                    rank_char = str(8 - row)
                    self.canvas.create_text(
                        x1 + 6, y1 + 6,
                        text=rank_char,
                        font=_FONT_MONO_SM,
                        fill=coord_color,
                        anchor="nw",
                    )

                piece = self.board.get_piece(row, col)
                self._draw_piece(drow, dcol, piece)

        if self.best_move and not game_status["is_over"]:
            self._draw_arrow(self.best_move[0], self.best_move[1], from_color=_INFO, line_color=_INFO)

        if (
            self._hover_suggestion_move
            and not game_status["is_over"]
            and (self.best_move is None or self._hover_suggestion_move != self.best_move)
        ):
            try:
                self._draw_arrow(self._hover_suggestion_move[0], self._hover_suggestion_move[1], from_color=_ACCENT, line_color=_ACCENT)
            except Exception:
                pass

    def _draw_arrow(self, start, end, from_color=BEST_FROM_COLOR, line_color=ARROW_COLOR):
        sr, sc = self._board_to_display(start[0], start[1])
        er, ec = self._board_to_display(end[0], end[1])
        half = SQUARE_SIZE // 2
        self.canvas.create_rectangle(
            sc * SQUARE_SIZE, sr * SQUARE_SIZE,
            (sc + 1) * SQUARE_SIZE, (sr + 1) * SQUARE_SIZE,
            fill=from_color, stipple="gray25", outline="")
        self.canvas.create_line(
            sc * SQUARE_SIZE + half, sr * SQUARE_SIZE + half,
            ec * SQUARE_SIZE + half, er * SQUARE_SIZE + half,
            fill=line_color, width=5,
            arrow=tk.LAST, arrowshape=(14, 18, 6), capstyle=tk.ROUND)

    def draw_eval_bar(self, score):
        """Draw a vertical evaluation bar.

        Positive score = white advantage (white fills from bottom).
        """
        h = 8 * SQUARE_SIZE
        w = EVAL_BAR_W
        self.eval_canvas.delete("all")

        ratio = _score_to_ratio(score)  # 0.0 black winning, 1.0 white winning
        white_h = int(h * ratio)
        black_h = h - white_h

        # Black fill (top)
        self.eval_canvas.create_rectangle(0, 0, w, black_h, fill=EVAL_BLACK, outline="")
        # White fill (bottom)
        self.eval_canvas.create_rectangle(0, black_h, w, h, fill=EVAL_WHITE, outline="")

        # Centerline (equality)
        mid = h // 2
        self.eval_canvas.create_line(2, mid, w - 2, mid, fill=_TEXT_DIM, width=1)

        # Score label (keep it readable by placing inside the larger section)
        if score >= 900:
            label = "M"
            label_y = black_h + max(10, white_h // 2)
            label_color = _PIECE_BLACK_TEXT
        elif score <= -900:
            label = "M"
            label_y = max(10, black_h // 2)
            label_color = _PIECE_WHITE_TEXT
        else:
            pawns = abs(score)
            label = f"{pawns:.1f}" if pawns < 10 else f"{int(pawns)}"
            if white_h >= black_h:
                label_y = black_h + max(10, white_h // 2)
                label_color = _PIECE_BLACK_TEXT
            else:
                label_y = max(10, black_h // 2)
                label_color = _PIECE_WHITE_TEXT

        self.eval_canvas.create_text(
            w // 2,
            label_y,
            text=label,
            font=("Menlo", 8, "bold"),
            fill=label_color,
            anchor="center",
        )

        # Border
        self.eval_canvas.create_rectangle(0, 0, w, h, outline=_PANEL_BORDER, width=1)

    # --- engine search ----------------------------------------------------

    def _start_engine_search(self):
        self._search_generation += 1
        generation = self._search_generation
        game_status = self.mg.get_game_status()
        if game_status["is_over"]:
            self.best_move = None
            self.draw_eval_bar(self.mg.evaluate_position())
            self.draw_board()
            return

        self._set_suggestions_searching()
        self._set_move_feedback("Analyzing…", "")

        snapshot = self._capture()
        is_white = self.board.turn == "white"
        threading.Thread(
            target=self._engine_thread,
            args=(snapshot, is_white, generation),
            daemon=True,
        ).start()

    def _engine_thread(self, snapshot, is_white, generation):
        mg = MoveGenerator(_board_from_snapshot(snapshot))
        mg.in_opening = False  # show full scored move list, not book-short-circuit

        last_root_scores = None
        last_pv = None
        last_depth = 0

        def on_depth(depth, score, pv, root_scores=None):
            nonlocal last_root_scores, last_pv, last_depth
            last_depth = depth
            last_pv = pv
            if root_scores is not None:
                last_root_scores = root_scores

        best_move, score = mg.find_best_move(
            SEARCH_DEPTH,
            is_white,
            verbose=False,
            on_depth_complete=on_depth,
            use_book=False,
        )

        # Build a "tree": one PV line per root move.
        root_tree = None
        if last_root_scores:
            root_tree = []
            for move, move_score in last_root_scores:
                pv_line = mg._extract_pv(is_white, last_depth, first_move=move) if last_depth else [move]
                root_tree.append((move, move_score, pv_line))

        self.root.after(
            0,
            lambda: self._on_engine_done(best_move, score, generation, last_root_scores, last_pv, last_depth, root_tree),
        )

    def _on_engine_done(self, best_move, score, generation, root_scores, pv, depth, root_tree):
        if generation != self._search_generation:
            return
        self.best_move = best_move
        self._current_eval_score = score
        self._last_root_scores = root_scores
        self._last_pv = pv
        self._last_search_depth = depth
        self._last_root_tree = root_tree
        self._suggestions_position_key = self.board.zobrist_hash

        # Persist analysis on the current node for move-quality comparisons.
        self.current_node.eval_score = score
        self.current_node.best_move = best_move

        self._update_move_feedback()
        self._update_opening_explorer()
        self.draw_eval_bar(score)
        # Move quality is computed after analysis; refresh history to show markers.
        self.update_history_panel()
        self.update_move_suggestions()
        self.draw_board()

    def update_position_analysis(self):
        """Position analysis panels removed; keep method for compatibility."""
        return
    
    def update_move_suggestions(self):
        """Update move suggestions with evaluations and continuations."""
        self.suggestions_text.config(state="normal")
        self.suggestions_text.delete("1.0", "end")
        self._suggestion_move_by_tag.clear()
        self._hover_suggestion_move = None

        root_tree = self._last_root_tree
        pv = self._last_pv

        if pv:
            pv_text = " ".join(_move_to_uci_text(m) for m in pv)
            self.suggestions_text.insert("end", f"Best line (depth {self._last_search_depth}):\n", "cont")
            self.suggestions_text.insert("end", f"  {pv_text}\n\n", "cont")

        if root_tree:
            show_n = min(TOP_SUGGESTIONS, len(root_tree))
            if len(root_tree) > show_n:
                self.suggestions_text.insert(
                    "end",
                    f"Top {show_n} of {len(root_tree)} (hover = arrow, click = play)\n\n",
                    "cont",
                )
            for idx, (move, score, pv_line) in enumerate(root_tree[:show_n], 1):
                tag = f"sg{idx}"
                self._suggestion_move_by_tag[tag] = move
                move_text = _move_to_uci_text(move)
                eval_str = f"+{score:.2f}" if score > 0 else f"{score:.2f}"
                self.suggestions_text.insert("end", f"{idx}. ", "move")
                self.suggestions_text.insert("end", move_text, ("move", tag))
                self.suggestions_text.insert("end", f"  {eval_str}\n", "eval")

                if pv_line:
                    pv_text = " ".join(_move_to_uci_text(m) for m in pv_line)
                    self.suggestions_text.insert("end", f"    └─ {pv_text}\n\n", "cont")
                else:
                    self.suggestions_text.insert("end", "\n")
        else:
            self.suggestions_text.insert("end", "No scored moves yet\n", "cont")
        
        self.suggestions_text.config(state="disabled")

    def _set_suggestions_searching(self):
        self._suggestions_position_key = None
        self._suggestion_move_by_tag.clear()
        self._hover_suggestion_move = None
        self._last_root_tree = None
        self._last_root_scores = None
        self._last_pv = None
        self._last_search_depth = 0
        self.suggestions_text.config(state="normal")
        self.suggestions_text.delete("1.0", "end")
        self.suggestions_text.insert("end", "Searching…\n", "cont")
        self.suggestions_text.config(state="disabled")

    def _on_suggestion_click(self, event):
        idx = self.suggestions_text.index(f"@{event.x},{event.y}")
        for tag in self.suggestions_text.tag_names(idx):
            if tag.startswith("sg"):
                move = self._suggestion_move_by_tag.get(tag)
                if move is not None:
                    self._play_engine_suggestion(move)
                return

    def _on_suggestion_motion(self, event):
        idx = self.suggestions_text.index(f"@{event.x},{event.y}")
        tags = self.suggestions_text.tag_names(idx)
        move_tag = next((t for t in tags if t.startswith("sg")), None)
        is_move = move_tag is not None
        self.suggestions_text.config(cursor="hand2" if is_move else "")

        new_hover = self._suggestion_move_by_tag.get(move_tag) if move_tag else None
        if new_hover != self._hover_suggestion_move:
            self._hover_suggestion_move = new_hover
            self.draw_board()

    def _play_engine_suggestion(self, move):
        if self.mg.get_game_status()["is_over"]:
            return

        # Guard against stale suggestions from a previous position.
        if self._suggestions_position_key is not None and self.board.zobrist_hash != self._suggestions_position_key:
            return

        start, end, promo = move

        # Ensure the move is legal in the current position (prevents applying the wrong side's move).
        is_white = self.board.turn == "white"
        legal = set(self.mg.generate_all_legal_moves(is_white))
        if (start, end, promo) not in legal:
            return

        san = move_to_san(self.board, self.mg, start, end, promo)
        self.board.move_piece(start, end, promo)
        self.clear_selection()
        self.record_move(start, end, san, promo)
        self.best_move = None
        self._set_suggestions_searching()
        self.update_history_panel()
        self._update_nav_buttons()
        self.draw_board()
        self._start_engine_search()

    # --- click handler ----------------------------------------------------

    def select_piece(self, row, col):
        self.selected = (row, col)
        self.legal_moves = self.mg.get_legal_moves(row, col)
        self.alert_king_square = None

    def on_click(self, event):
        # No is_latest_position guard — moves from any node create a new branch
        if self.mg.get_game_status()["is_over"]:
            return

        drow = event.y // SQUARE_SIZE
        dcol = event.x // SQUARE_SIZE
        if not (0 <= drow < 8 and 0 <= dcol < 8):
            return

        row, col = self._display_to_board(drow, dcol)

        piece = self.board.get_piece(row, col)

        if self.selected is None:
            if self.is_current_turn_piece(piece):
                self.select_piece(row, col)
            self.draw_board()
            return

        start = self.selected
        end = (row, col)
        start_piece = self.board.get_piece(start[0], start[1])
        legal_moves = self.legal_moves
        pseudo_moves = self.mg.get_piece_moves(start[0], start[1])

        if end in legal_moves:
            promotion_choice = None
            if self.needs_promotion(start, end):
                promotion_choice = self.ask_promotion_choice(start_piece)
                if promotion_choice is None:
                    self.draw_board()
                    return

            # Compute SAN before the move mutates the board
            san = move_to_san(self.board, self.mg, start, end, promotion_choice)
            self.board.move_piece(start, end, promotion_choice)
            self.clear_selection()
            self.record_move(start, end, san, promotion_choice)
            self.best_move = None
            self._set_suggestions_searching()
            self.draw_board()
            self._start_engine_search()
            return

        if self.is_current_turn_piece(piece):
            self.select_piece(row, col)
        else:
            self.alert_king_square = (
                self.mg.find_king(start_piece.isupper())
                if end in pseudo_moves else None
            )

        self.draw_board()

    def run(self):
        self.root.mainloop()
