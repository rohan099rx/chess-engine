import math
import threading
import time
from dataclasses import dataclass


from engine.board import Board
from engine.move_generator import MoveGenerator
from engine.opening_book import get_book_entries
from engine.notation import move_to_san


try:
    import pygame
except Exception as exc:  # pragma: no cover
    pygame = None
    _PYGAME_IMPORT_ERROR = exc


PIECE_FILES = {
    "P": "assets/chesscom/pieces/classic/wp.png",
    "N": "assets/chesscom/pieces/classic/wn.png",
    "B": "assets/chesscom/pieces/classic/wb.png",
    "R": "assets/chesscom/pieces/classic/wr.png",
    "Q": "assets/chesscom/pieces/classic/wq.png",
    "K": "assets/chesscom/pieces/classic/wk.png",
    "p": "assets/chesscom/pieces/classic/bp.png",
    "n": "assets/chesscom/pieces/classic/bn.png",
    "b": "assets/chesscom/pieces/classic/bb.png",
    "r": "assets/chesscom/pieces/classic/br.png",
    "q": "assets/chesscom/pieces/classic/bq.png",
    "k": "assets/chesscom/pieces/classic/bk.png",
}


@dataclass
class _UndoRecord:
    start: tuple[int, int]
    end: tuple[int, int]
    move_state: dict
    prev_turn: str
    prev_position_counts: dict
    prev_white_time_left: float | None
    prev_black_time_left: float | None
    prev_last_clock_tick: float
    prev_active_clock_side: str
    prev_review_len: int
    prev_san_len: int
    prev_review_selected_index: int | None
    prev_review_mode: bool
    prev_game_over_reason: str
    prev_game_over_status: str


class PygameChessGUI:
    """A small playable Pygame GUI that lets a human play vs the engine.

    Controls:
      - Click to select and move pieces (legal moves highlighted)
      - U: Undo last full move (player + AI)
      - 1-5: Set difficulty (search depth)
      - F: Flip board
      - R: Reset
    """

    def __init__(
        self,
        *,
        square_size: int = 80,
        human_plays_white: bool = True,
        difficulty: int = 3,
        time_minutes: int = 5,
        increment_seconds: int = 0,
    ):
        if pygame is None:  # pragma: no cover
            raise RuntimeError(
                "pygame is not installed. Install requirements.txt first. "
                f"Import error: {_PYGAME_IMPORT_ERROR}"
            )

        self.square_size = square_size
        self.board_px = 8 * square_size

        self.eval_w = 26
        self.sidebar_w = 380
        self.panel_h = 90

        self.board_origin_x = self.eval_w
        self.board_origin_y = 0

        self.window_w = self.eval_w + self.board_px + self.sidebar_w
        self.window_h = self.board_px + self.panel_h

        self.human_plays_white = human_plays_white
        self.difficulty = max(1, min(5, int(difficulty)))
        self.flipped = False
        self.time_minutes = max(0, int(time_minutes))
        self.increment_seconds = max(0, int(increment_seconds))

        self.board = Board()
        self.mg = MoveGenerator(self.board)

        self.selected: tuple[int, int] | None = None
        self.legal_ends: list[tuple[int, int]] = []
        self.last_move: tuple[tuple[int, int], tuple[int, int]] | None = None
        self.undo_stack: list[_UndoRecord] = []

        self.pending_promotion: tuple[tuple[int, int], tuple[int, int]] | None = None
        self.move_history_review: list[dict] = []
        self.move_san_history: list[str] = []
        self.review_selected_index: int | None = None
        self._review_row_hitboxes: list[tuple[int, object]] = []
        self.review_mode = False
        self.game_over_reason = ""
        self.game_over_status = "ongoing"

        self.white_time_left = float(self.time_minutes * 60) if self.time_minutes > 0 else None
        self.black_time_left = float(self.time_minutes * 60) if self.time_minutes > 0 else None
        self._last_clock_tick = time.perf_counter()
        self._active_clock_side = self.board.turn

        self._position_generation = 0

        self._ai_lock = threading.Lock()
        self._ai_thinking = False
        self._ai_best_move: tuple[tuple[int, int], tuple[int, int], str | None] | None = None

        self._analysis_lock = threading.Lock()
        self._analysis_state_lock = threading.Lock()
        self._analysis_thinking = False
        self._analysis_pending = False
        self._analysis_pending_gen = 0
        self._analysis_pending_snapshot: dict | None = None
        self._analysis_eval = 0.0
        self._analysis_lines: list[tuple[str, float]] = []  # list[(san, score)] white-perspective
        self._analysis_depth = 0
        self._analysis_best_pv_text = ""
        self._analysis_suggested_moves: list[tuple[tuple[int, int], tuple[int, int], str | None, float, str]] = []
        self._analysis_multipv: list[tuple[float, str]] = []  # list[(score, line_text)] white-perspective
        self._opening_theory_label = "Starting position"
        self._opening_theory_detail = "Book coverage unavailable"

        self._anim_active = False
        self._anim_piece: str | None = None
        self._anim_start_sq: tuple[int, int] | None = None
        self._anim_end_sq: tuple[int, int] | None = None
        self._anim_t0 = 0.0
        self._anim_ms = 180

        pygame.init()
        pygame.display.set_caption("Chess (Pygame) — Rohans Engine")
        self.screen = pygame.display.set_mode((self.window_w, self.window_h))
        self.clock = pygame.time.Clock()
        self.font = pygame.font.SysFont("Menlo", 18)
        self.font_small = pygame.font.SysFont("Menlo", 14)
        self.font_tiny = pygame.font.SysFont("Menlo", 12)

        self._load_images()

        # Initial analysis for the starting position
        self._start_analysis()
        self._refresh_opening_theory()

    # ------------------------------------------------------------------
    # PV / formatting helpers
    # ------------------------------------------------------------------

    def _current_fullmove_number(self) -> int:
        # Halfmoves are tracked in undo_stack.
        halfmoves = len(self.undo_stack)
        return halfmoves // 2 + 1

    def _format_pv_with_numbers(self, sans: list[str], start_fullmove: int, start_white: bool) -> str:
        if not sans:
            return ""

        parts: list[str] = []
        mv = start_fullmove
        i = 0
        if start_white:
            while i < len(sans):
                w = sans[i]
                b = sans[i + 1] if i + 1 < len(sans) else None
                if b is None:
                    parts.append(f"{mv}. {w}")
                    break
                parts.append(f"{mv}. {w} {b}")
                mv += 1
                i += 2
        else:
            # Black to move: start with "N... move" then "N+1. w b" etc.
            parts.append(f"{mv}... {sans[0]}")
            i = 1
            mv += 1
            while i < len(sans):
                w = sans[i]
                b = sans[i + 1] if i + 1 < len(sans) else None
                if b is None:
                    parts.append(f"{mv}. {w}")
                    break
                parts.append(f"{mv}. {w} {b}")
                mv += 1
                i += 2

        return " ".join(parts)

    def _pv_moves_to_sans(self, snapshot: dict, pv: list[tuple], max_plies: int = 10) -> list[str]:
        b = self._board_from_snapshot(snapshot)
        mg = MoveGenerator(b)

        out: list[str] = []
        start_is_white = b.turn == "white"
        cur_white = start_is_white
        for (start, end, promo) in pv[:max_plies]:
            b.turn = "white" if cur_white else "black"
            try:
                san = move_to_san(b, mg, start, end, promo)
            except Exception:
                san = f"{b.square_to_algebraic(start[0], start[1])}{b.square_to_algebraic(end[0], end[1])}"
                if promo:
                    san += promo.lower()
            out.append(san)
            state = b.make_move(start, end, promo)
            b.undo_move(start, end, state)  # ensure make_move doesn't drift mg caches
            # Actually apply move for next SAN generation.
            b.make_move(start, end, promo)
            cur_white = not cur_white

        return out

    @staticmethod
    def _format_cp(score: float) -> str:
        if abs(score) >= 900:
            return "#"
        return f"{score:+.2f}"

    @staticmethod
    def _clamp01(value: float) -> float:
        return max(0.0, min(1.0, value))

    def _score_label(self, score: float) -> str:
        if abs(score) >= 900:
            return "#"
        return f"{score:+.2f}"

    def _score_strength(self, score: float) -> float:
        # Convert a score to a 0..1 confidence-style meter.
        if abs(score) >= 900:
            return 1.0
        return self._clamp01(abs(self._score_to_norm(score)))

    def _draw_card(self, rect, fill, border=(70, 78, 92), radius=16, border_width=1):
        pygame.draw.rect(self.screen, fill, rect, border_radius=radius)
        pygame.draw.rect(self.screen, border, rect, border_width, border_radius=radius)

    def _draw_section_title(self, x, y, text, accent=(112, 169, 255)):
        dot = pygame.Rect(x, y + 6, 8, 8)
        pygame.draw.ellipse(self.screen, accent, dot)
        title = self.font.render(text, True, (236, 239, 245))
        self.screen.blit(title, (x + 14, y))

    def _draw_eval_meter(self, x, y, w, h, score):
        ratio = self._score_to_ratio(score)
        track = pygame.Rect(x, y, w, h)
        white_w = int(round(w * ratio))
        pygame.draw.rect(self.screen, (27, 30, 38), track, border_radius=8)
        pygame.draw.rect(self.screen, (236, 236, 240), pygame.Rect(x, y, white_w, h), border_radius=8)
        if white_w < w:
            pygame.draw.rect(self.screen, (11, 14, 18), pygame.Rect(x + white_w, y, w - white_w, h), border_radius=8)
        pygame.draw.rect(self.screen, (80, 86, 98), track, 1, border_radius=8)

    def _draw_move_row(self, x, y, w, move_text, score, rank, selected=False):
        row_h = 34
        fill = (31, 35, 43) if not selected else (37, 45, 58)
        border = (75, 90, 120) if selected else (58, 64, 76)
        rect = pygame.Rect(x, y, w, row_h)
        self._draw_card(rect, fill, border=border, radius=10)

        rank_surf = self.font_small.render(f"{rank}.", True, (171, 177, 190))
        self.screen.blit(rank_surf, (x + 10, y + 8))

        move_surf = self.font_small.render(move_text, True, (244, 246, 250))
        self.screen.blit(move_surf, (x + 32, y + 8))

        score_txt = self._score_label(score)
        score_surf = self.font_small.render(score_txt, True, (255, 217, 119) if score >= 0 else (255, 156, 156))
        self.screen.blit(score_surf, (x + w - score_surf.get_width() - 10, y + 8))

        bar_w = int((w - 20) * self._score_strength(score))
        bar_color = (88, 196, 130) if score >= 0 else (255, 130, 130)
        pygame.draw.rect(self.screen, (45, 50, 61), pygame.Rect(x + 10, y + 23, w - 20, 4), border_radius=2)
        pygame.draw.rect(self.screen, bar_color, pygame.Rect(x + 10, y + 23, bar_w, 4), border_radius=2)
        return row_h

    def _draw_arrow(self, start_sq: tuple[int, int], end_sq: tuple[int, int], *, rgba: tuple[int, int, int, int], width: int = 10):
        # Draw an arrow from start to end.
        sx, sy = self._square_to_screen(*start_sq)
        ex, ey = self._square_to_screen(*end_sq)
        s = (sx + self.square_size / 2.0, sy + self.square_size / 2.0)
        e = (ex + self.square_size / 2.0, ey + self.square_size / 2.0)

        dx = e[0] - s[0]
        dy = e[1] - s[1]
        dist = math.hypot(dx, dy)
        if dist < 1e-6:
            return

        ux = dx / dist
        uy = dy / dist

        head_len = max(14.0, width * 1.8)
        head_w = max(10.0, width * 1.2)

        # Shorten line so arrow head doesn't overshoot.
        end_line = (e[0] - ux * head_len, e[1] - uy * head_len)

        surf = pygame.Surface((self.window_w, self.window_h), pygame.SRCALPHA)
        pygame.draw.line(surf, rgba, s, end_line, width)

        # Triangle head
        px = -uy
        py = ux
        left = (end_line[0] + px * head_w / 2.0, end_line[1] + py * head_w / 2.0)
        right = (end_line[0] - px * head_w / 2.0, end_line[1] - py * head_w / 2.0)
        pygame.draw.polygon(surf, rgba, [e, left, right])
        self.screen.blit(surf, (0, 0))

    # ------------------------------------------------------------------
    # Assets
    # ------------------------------------------------------------------

    def _load_images(self):
        self.piece_images = {}
        for piece, rel_path in PIECE_FILES.items():
            try:
                img = pygame.image.load(rel_path)
                img = pygame.transform.smoothscale(img, (self.square_size, self.square_size))
                self.piece_images[piece] = img
            except Exception:
                # Missing asset shouldn't crash the game; pieces simply won't render.
                pass

    # ------------------------------------------------------------------
    # Coordinate helpers
    # ------------------------------------------------------------------

    def _screen_to_square(self, x: int, y: int) -> tuple[int, int] | None:
        x -= self.board_origin_x
        y -= self.board_origin_y
        if x < 0 or y < 0 or x >= self.board_px or y >= self.board_px:
            return None
        col = x // self.square_size
        row = y // self.square_size
        if self.flipped:
            row = 7 - row
            col = 7 - col
        return int(row), int(col)

    def _square_to_screen(self, row: int, col: int) -> tuple[int, int]:
        if self.flipped:
            row = 7 - row
            col = 7 - col
        return (
            self.board_origin_x + col * self.square_size,
            self.board_origin_y + row * self.square_size,
        )

    # ------------------------------------------------------------------
    # Snapshot/cloning (thread-safe engine work)
    # ------------------------------------------------------------------

    def _capture_position(self):
        return {
            "board": [row[:] for row in self.board.board],
            "turn": self.board.turn,
            "en_passant_target": self.board.en_passant_target,
            "halfmove_clock": self.board.halfmove_clock,
            "castling_rights": self.board.castling_rights.copy(),
            "position_counts": self.board.position_counts.copy(),
        }

    @staticmethod
    def _board_from_snapshot(snapshot) -> Board:
        b = Board()
        b.board = [row[:] for row in snapshot["board"]]
        b.turn = snapshot["turn"]
        b.en_passant_target = snapshot["en_passant_target"]
        b.halfmove_clock = snapshot["halfmove_clock"]
        b.castling_rights = snapshot["castling_rights"].copy()
        b.position_counts = snapshot["position_counts"].copy()
        b.refresh_zobrist_hash()
        return b

    # ------------------------------------------------------------------
    # Turn / roles
    # ------------------------------------------------------------------

    def _side_to_move_is_white(self) -> bool:
        return self.board.turn == "white"

    def _human_to_move(self) -> bool:
        return self.game_over_status == "ongoing" and self._side_to_move_is_white() == self.human_plays_white

    def _remaining_time_for_side(self, side: str) -> float | None:
        return self.white_time_left if side == "white" else self.black_time_left

    @staticmethod
    def _format_clock(seconds_left: float | None) -> str:
        if seconds_left is None:
            return "--:--"
        seconds_left = max(0.0, float(seconds_left))
        whole = int(seconds_left)
        minutes = whole // 60
        seconds = whole % 60
        return f"{minutes}:{seconds:02d}"

    def _tick_clocks(self):
        if self.white_time_left is None or self.black_time_left is None:
            return
        now = time.perf_counter()
        elapsed = max(0.0, now - self._last_clock_tick)
        self._last_clock_tick = now

        if self.game_over_status != "ongoing":
            return

        if self._active_clock_side == "white":
            self.white_time_left = max(0.0, self.white_time_left - elapsed)
            if self.white_time_left <= 0.0:
                self._finalize_game("time_forfeit", "White ran out of time")
        else:
            self.black_time_left = max(0.0, self.black_time_left - elapsed)
            if self.black_time_left <= 0.0:
                self._finalize_game("time_forfeit", "Black ran out of time")

    def _apply_clock_increment(self, side: str):
        if side == "white" and self.white_time_left is not None:
            self.white_time_left += float(self.increment_seconds)
        elif side == "black" and self.black_time_left is not None:
            self.black_time_left += float(self.increment_seconds)

    def _opening_theory_from_history(self) -> tuple[str, str]:
        moves = self.move_san_history[:6]
        if not moves:
            return "Starting position", "Open with 1.e4, 1.d4, 1.c4 or 1.Nf3 to enter common theory."

        joined = " ".join(moves)
        prefixes = [
            (("e4", "e5", "Nf3", "Nc6", "Bb5"), "Ruy Lopez"),
            (("e4", "e5", "Nf3", "Nc6", "Bc4"), "Italian Game"),
            (("e4", "c5"), "Sicilian Defence"),
            (("e4", "e6"), "French Defence"),
            (("e4", "c6"), "Caro-Kann Defence"),
            (("d4", "d5", "c4"), "Queen's Gambit"),
            (("d4", "Nf6", "c4", "g6"), "King's Indian Defence"),
            (("c4",), "English Opening"),
            (("Nf3",), "Reti Opening"),
        ]

        for seq, name in prefixes:
            if all(token in joined for token in seq):
                return f"Opening theory · {name}", f"The current line matches a common {name} setup."

        book_entries = get_book_entries(self.board, limit=3)
        if book_entries:
            best_move, weight = book_entries[0]
            start, end, promo = best_move
            move_text = f"{self.board.square_to_algebraic(start[0], start[1])}{self.board.square_to_algebraic(end[0], end[1])}"
            if promo:
                move_text += promo.lower()
            return "Opening theory · In book", f"Book move suggestion: {move_text} (weight {weight})."

        return "Opening theory · Out of book", "This position is no longer in the opening book; engine evaluation is driving the line."

    def _refresh_opening_theory(self):
        self._opening_theory_label, self._opening_theory_detail = self._opening_theory_from_history()

    def _classify_review_loss(self, loss: float) -> str:
        if loss <= 0.05:
            return "Best"
        if loss <= 0.15:
            return "Excellent"
        if loss <= 0.30:
            return "Good"
        if loss <= 0.60:
            return "Inaccuracy"
        if loss <= 1.00:
            return "Mistake"
        if loss <= 1.50:
            return "Miss"
        return "Blunder"

    def _review_accuracy(self, side: str) -> float:
        entries = [entry for entry in self.move_history_review if entry["side"] == side]
        if not entries:
            return 0.0
        avg_loss = sum(entry["loss_cp"] for entry in entries) / float(len(entries))
        return max(0.0, min(100.0, 100.0 - avg_loss * 0.42))

    def _review_counts(self, side: str) -> dict[str, int]:
        counts = {key: 0 for key in ("Brilliant", "Great", "Best", "Excellent", "Good", "Inaccuracy", "Mistake", "Miss", "Blunder")}
        for entry in self.move_history_review:
            if entry["side"] != side:
                continue
            counts[entry["classification"]] = counts.get(entry["classification"], 0) + 1
        return counts

    def _review_rows(self) -> list[dict]:
        rows = []
        for idx, entry in enumerate(self.move_history_review):
            rows.append({"index": idx, **entry})
        return rows

    def _review_row_rect(self, sidebar_x: int, y: int, width: int):
        return pygame.Rect(sidebar_x, y, width, 28)

    def _select_review_entry(self, index: int | None):
        if index is None:
            self.review_selected_index = None
            return
        if 0 <= index < len(self.move_history_review):
            self.review_selected_index = index

    def _selected_review_entry(self):
        if self.review_selected_index is None:
            return None
        if not (0 <= self.review_selected_index < len(self.move_history_review)):
            return None
        return self.move_history_review[self.review_selected_index]

    def _finalize_game(self, reason: str, message: str):
        self.game_over_status = reason
        self.game_over_reason = message
        self.review_mode = True

    def _make_review_entry(self, start, end, promo, move_san: str, mover_side: str, snapshot: dict):
        if self._analysis_suggested_moves:
            best_entry = self._analysis_suggested_moves[0]
            best_start, best_end, best_promo, _, best_san = best_entry
            best_score_white = float(best_entry[3])
        else:
            best_start, best_end, best_promo = start, end, promo
            best_score_white = float(self._analysis_eval)

        mover_sign = 1.0 if mover_side == "white" else -1.0
        best_stm = best_score_white * mover_sign

        b_after = self._board_from_snapshot(snapshot)
        mg_after = MoveGenerator(b_after)
        after_white = float(mg_after.evaluate_position())
        played_stm = after_white * mover_sign
        loss = max(0.0, best_stm - played_stm)
        loss_cp = int(round(loss * 100))

        if (start, end, promo) == (best_start, best_end, best_promo):
            category = "Best"
        else:
            category = self._classify_review_loss(loss)

        return {
            "side": mover_side,
            "start": start,
            "end": end,
            "promo": promo,
            "san": move_san,
            "best_start": best_start,
            "best_end": best_end,
            "best_promo": best_promo,
            "best_san": best_san if self._analysis_suggested_moves else move_san,
            "best_score_white": best_score_white,
            "played_score_white": after_white,
            "loss": loss,
            "loss_cp": loss_cp,
            "classification": category,
        }

    def _append_review_entry(self, entry: dict):
        self.move_history_review.append(entry)
        self._refresh_opening_theory()

    # ------------------------------------------------------------------
    # Moves / undo
    # ------------------------------------------------------------------

    def _push_undo(self, start, end, move_state, prev_turn, prev_position_counts):
        self.undo_stack.append(
            _UndoRecord(
                start=start,
                end=end,
                move_state=move_state,
                prev_turn=prev_turn,
                prev_position_counts=prev_position_counts,
                prev_white_time_left=self.white_time_left,
                prev_black_time_left=self.black_time_left,
                prev_last_clock_tick=self._last_clock_tick,
                prev_active_clock_side=self._active_clock_side,
                prev_review_len=len(self.move_history_review),
                prev_san_len=len(self.move_san_history),
                prev_review_selected_index=self.review_selected_index,
                prev_review_mode=self.review_mode,
                prev_game_over_reason=self.game_over_reason,
                prev_game_over_status=self.game_over_status,
            )
        )

    def _apply_move(self, start, end, promo: str | None = None):
        moving_piece = self.board.get_piece(start[0], start[1])
        prev_turn = self.board.turn
        prev_counts = self.board.position_counts.copy()
        mover_side = prev_turn
        try:
            move_san = move_to_san(self.board, self.mg, start, end, promo)
        except Exception:
            move_san = f"{self.board.square_to_algebraic(start[0], start[1])}{self.board.square_to_algebraic(end[0], end[1])}"
            if promo:
                move_san += promo.lower()
        move_state = self.board.make_move(start, end, promo)

        # Update turn + repetition counts for gameplay (engine search doesn't use this).
        self.board.turn = "black" if self.board.turn == "white" else "white"
        self.board.record_current_position()

        self._apply_clock_increment(mover_side)
        self._active_clock_side = self.board.turn
        self._last_clock_tick = time.perf_counter()

        self._push_undo(start, end, move_state, prev_turn, prev_counts)
        self.last_move = (start, end)

        self.move_san_history.append(move_san)
        review_entry = self._make_review_entry(start, end, promo, move_san, mover_side, self._capture_position())
        self._append_review_entry(review_entry)

        # Start animation (visual only)
        self._anim_active = True
        self._anim_piece = moving_piece
        self._anim_start_sq = start
        self._anim_end_sq = end
        self._anim_t0 = time.perf_counter()

        # Bump generation and refresh analysis
        self._position_generation += 1
        self._start_analysis()

        status = self.mg.get_game_status()
        if status["is_over"]:
            self._finalize_game(status["result"], status["message"])

        # Clear selection/highlights after making a move.
        self.selected = None
        self.legal_ends = []

    def _undo_halfmove(self):
        if not self.undo_stack:
            return
        rec = self.undo_stack.pop()
        self.board.turn = rec.prev_turn
        self.board.undo_move(rec.start, rec.end, rec.move_state)
        self.board.position_counts = rec.prev_position_counts
        self.last_move = None
        self.white_time_left = rec.prev_white_time_left
        self.black_time_left = rec.prev_black_time_left
        self._last_clock_tick = rec.prev_last_clock_tick
        self._active_clock_side = rec.prev_active_clock_side
        self.review_mode = rec.prev_review_mode
        self.game_over_reason = rec.prev_game_over_reason
        self.game_over_status = rec.prev_game_over_status
        self.review_selected_index = rec.prev_review_selected_index
        self.move_history_review = self.move_history_review[: rec.prev_review_len]
        self.move_san_history = self.move_san_history[: rec.prev_san_len]
        self._refresh_opening_theory()

        self._position_generation += 1
        self._start_analysis()

    def undo_full_move(self):
        # Undo AI move (if any) then player move.
        if not self.undo_stack:
            return
        self._undo_halfmove()
        if self.undo_stack and not self._human_to_move():
            # If after one undo it is still not the human turn, undo one more ply.
            self._undo_halfmove()

        self.selected = None
        self.legal_ends = []

    # ------------------------------------------------------------------
    # Promotion handling
    # ------------------------------------------------------------------

    def _needs_promotion(self, start, end) -> bool:
        return self.board.is_promotion_move(start, end)

    def _promotion_from_key(self, key) -> str | None:
        if key == pygame.K_q:
            return "Q"
        if key == pygame.K_r:
            return "R"
        if key == pygame.K_b:
            return "B"
        if key == pygame.K_n:
            return "N"
        return None

    # ------------------------------------------------------------------
    # AI
    # ------------------------------------------------------------------

    def _difficulty_to_depth(self) -> int:
        # Level 1 is intentionally weak/random.
        if self.difficulty <= 1:
            return 1
        # Map human-friendly 1-5 difficulty into meaningful search depths.
        # Level 2: low depth; 3: moderate; 4: deep; 5: very deep.
        return {2: 2, 3: 4, 4: 8, 5: 12}.get(self.difficulty, self.difficulty)

    # ------------------------------------------------------------------
    # Engine analysis (suggested moves + eval)
    # ------------------------------------------------------------------

    @staticmethod
    def _score_to_norm(score: float) -> float:
        """Normalize a pawn eval to [-1, 1] for UI.

        Uses tanh so large evals saturate smoothly, and mates clamp.
        """
        if score >= 900:
            return 1.0
        if score <= -900:
            return -1.0
        return float(math.tanh(score / 4.0))

    @classmethod
    def _score_to_ratio(cls, score: float) -> float:
        # Convert normalized [-1,1] to bar ratio [0,1] where 1=white.
        n = cls._score_to_norm(score)
        return 0.5 + 0.5 * n

    @staticmethod
    def _format_score(score: float) -> str:
        # Display normalized eval in [-1,1]
        if abs(score) >= 900:
            return "#"
        n = float(math.tanh(score / 4.0))
        n = max(-1.0, min(1.0, n))
        return f"{n:+.2f}"

    def _start_analysis(self):
        if self.pending_promotion is not None:
            return

        gen = self._position_generation
        snapshot = self._capture_position()
        with self._analysis_state_lock:
            if self._analysis_thinking:
                self._analysis_pending = True
                self._analysis_pending_gen = gen
                self._analysis_pending_snapshot = snapshot
                return

            self._analysis_thinking = True

        t = threading.Thread(target=self._analysis_worker, args=(gen, snapshot), daemon=True)
        t.start()

    def _analysis_worker(self, gen: int, snapshot: dict):
        with self._analysis_lock:
            b = self._board_from_snapshot(snapshot)
            mg = MoveGenerator(b)
            eval_now = float(mg.evaluate_position())

            # Defensive: only show moves that are legal in this position.
            is_white_to_move = b.turn == "white"
            legal_set = set(mg.generate_all_legal_moves(is_white_to_move))

            root_scores_latest: list[tuple[tuple, float]] | None = None
            best_pv_latest: list[tuple] | None = None
            best_score_latest: float | None = None
            depth_latest = 0

            def _on_depth_complete(depth, score_for_display, pv, root_scores_display=None):
                nonlocal root_scores_latest
                nonlocal best_pv_latest, best_score_latest, depth_latest
                if root_scores_display is None:
                    return
                root_scores_latest = list(root_scores_display)
                best_pv_latest = list(pv) if pv else []
                best_score_latest = float(score_for_display)
                depth_latest = int(depth)

            depth = max(4, self._difficulty_to_depth() + 2)

            # Analysis can think a bit longer than the move-playing engine since it
            # runs in the background thread.
            analysis_time = 0.75 + 0.9 * max(0, (self.difficulty - 1))
            analysis_time = min(8.0, analysis_time)

            try:
                mg.find_best_move(
                    depth=depth,
                    is_white_turn=(b.turn == "white"),
                    max_time=analysis_time,
                    verbose=False,
                    on_depth_complete=_on_depth_complete,
                    use_book=True,
                )
            except Exception:
                pass

            lines: list[tuple[str, float]] = []
            suggested_moves: list[tuple[tuple[int, int], tuple[int, int], str | None, float, str]] = []
            if root_scores_latest:
                # Keep top 10 moves so the review panel can show richer lines.
                for (move, score) in root_scores_latest[:10]:
                    if move not in legal_set:
                        continue
                    start, end, promo = move
                    try:
                        san = move_to_san(b, mg, start, end, promo)
                    except Exception:
                        san = f"{b.square_to_algebraic(start[0], start[1])}{b.square_to_algebraic(end[0], end[1])}"
                        if promo:
                            san += promo.lower()
                    lines.append((san, float(score)))
                    suggested_moves.append((start, end, promo, float(score), san))
            else:
                # Fallback: score a larger batch of legal moves quickly (still multi-move suggestions).
                moves = list(legal_set)
                scored = []
                for (start, end, promo) in moves[:30]:
                    state = b.make_move(start, end, promo)
                    try:
                        s = float(mg.evaluate_position())
                    finally:
                        b.undo_move(start, end, state)

                    scored.append(((start, end, promo), s))

                scored.sort(key=lambda ms: ms[1] if is_white_to_move else -ms[1], reverse=True)
                for (move, s) in scored[:8]:
                    start, end, promo = move
                    try:
                        san = move_to_san(b, mg, start, end, promo)
                    except Exception:
                        san = f"{b.square_to_algebraic(start[0], start[1])}{b.square_to_algebraic(end[0], end[1])}"
                        if promo:
                            san += promo.lower()
                    lines.append((san, float(s)))
                    suggested_moves.append((start, end, promo, float(s), san))

            # Build best PV text (chess.com-like)
            best_pv_text = ""
            if best_pv_latest:
                try:
                    sans = []
                    b2 = self._board_from_snapshot(snapshot)
                    mg2 = MoveGenerator(b2)
                    cur_white = b2.turn == "white"
                    for (start, end, promo) in best_pv_latest[:10]:
                        b2.turn = "white" if cur_white else "black"
                        try:
                            sans.append(move_to_san(b2, mg2, start, end, promo))
                        except Exception:
                            u = f"{b2.square_to_algebraic(start[0], start[1])}{b2.square_to_algebraic(end[0], end[1])}"
                            if promo:
                                u += promo.lower()
                            sans.append(u)
                        st = b2.make_move(start, end, promo)
                        # Keep progressing position
                        cur_white = not cur_white
                    start_fullmove = self._current_fullmove_number()
                    start_white = (snapshot["turn"] == "white")
                    best_pv_text = self._format_pv_with_numbers(sans, start_fullmove, start_white)
                except Exception:
                    best_pv_text = ""

            # Apply results if still current
            if gen == self._position_generation:
                self._analysis_eval = eval_now
                self._analysis_lines = lines
                self._analysis_depth = depth_latest
                self._analysis_best_pv_text = best_pv_text
                self._analysis_suggested_moves = suggested_moves
                # Keep multipv display simple/fast/reliable: score + first move SAN.
                self._analysis_multipv = [(sc, san) for (san, sc) in self._analysis_lines[:8]]

            with self._analysis_state_lock:
                self._analysis_thinking = False
                pending = self._analysis_pending
                pending_gen = self._analysis_pending_gen
                pending_snapshot = self._analysis_pending_snapshot
                self._analysis_pending = False
                self._analysis_pending_snapshot = None

            if pending and pending_snapshot is not None and pending_gen != gen:
                with self._analysis_state_lock:
                    if not self._analysis_thinking:
                        self._analysis_thinking = True
                        t = threading.Thread(
                            target=self._analysis_worker,
                            args=(pending_gen, pending_snapshot),
                            daemon=True,
                        )
                        t.start()

    def _is_brilliant_best(self) -> bool:
        # Heuristic: best move is "brilliant" if it stands out clearly from #2.
        if len(self._analysis_suggested_moves) < 2:
            return False
        (s1, e1, p1, sc1, _san1) = self._analysis_suggested_moves[0]
        (_s2, _e2, _p2, sc2, _san2) = self._analysis_suggested_moves[1]
        # Compare from side-to-move perspective.
        stm = 1.0 if self.board.turn == "white" else -1.0
        diff = (sc1 * stm) - (sc2 * stm)
        return diff >= 0.60

    def _start_ai_if_needed(self):
        if self._ai_thinking:
            return
        if self.game_over_status != "ongoing":
            return
        if self._human_to_move():
            return
        if self.mg.get_game_status()["is_over"]:
            return

        self._ai_thinking = True
        self._ai_best_move = None
        gen = self._position_generation
        snapshot = self._capture_position()
        t = threading.Thread(target=self._ai_worker, args=(gen, snapshot), daemon=True)
        t.start()

    def _ai_worker(self, gen: int, snapshot: dict):
        with self._ai_lock:
            b = self._board_from_snapshot(snapshot)
            mg = MoveGenerator(b)

            ai_is_white = b.turn == "white"
            legal = mg.generate_all_legal_moves(ai_is_white)
            if not legal:
                self._ai_best_move = None
                self._ai_thinking = False
                return

            # Difficulty 1: random legal move.
            if self.difficulty <= 1:
                import random

                self._ai_best_move = random.choice(legal)
                self._ai_thinking = False
                return

            depth = self._difficulty_to_depth()
            # Time cap keeps the UI responsive while still letting iterative deepening find tactics.
            max_time = 0.25 + 0.75 * max(0, (self.difficulty - 1))
            if self.difficulty >= 5:
                max_time = max(max_time, 4.0)
            clock_side = "white" if ai_is_white else "black"
            remaining = self._remaining_time_for_side(clock_side)
            if remaining is not None:
                clock_budget = max(0.05, remaining / 25.0 + float(self.increment_seconds) * 0.5)
                max_time = min(max_time, clock_budget)
            best_move, _score = mg.find_best_move(
                depth=depth,
                is_white_turn=ai_is_white,
                max_time=max_time,
                verbose=False,
                use_book=True,
            )

            if gen == self._position_generation:
                self._ai_best_move = best_move
            self._ai_thinking = False

    def _consume_ai_move_if_ready(self):
        if self._ai_thinking:
            return
        if self.game_over_status != "ongoing":
            return
        if self._ai_best_move is None:
            return
        if self._human_to_move():
            # Human moved while engine was thinking; discard.
            self._ai_best_move = None
            return

        start, end, promo = self._ai_best_move
        self._ai_best_move = None

        # Defensive: validate move is legal in the current position.
        is_white_to_move = self.board.turn == "white"
        if (start, end, promo) not in set(self.mg.generate_all_legal_moves(is_white_to_move)):
            return
        self._apply_move(start, end, promo)
        self.selected = None

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _draw_board(self):
        light = (237, 214, 176)
        dark = (173, 126, 86)
        sel = (255, 224, 102)
        last = (132, 191, 125)
        legal = (102, 157, 255)
        best_legal = (255, 203, 71)
        check = (232, 93, 104)

        # Background wash
        self.screen.fill((10, 13, 18))
        bg_top = pygame.Rect(0, 0, self.window_w, self.window_h)
        pygame.draw.rect(self.screen, (13, 17, 24), bg_top)
        pygame.draw.circle(self.screen, (28, 41, 64), (self.window_w - 120, 70), 170)
        pygame.draw.circle(self.screen, (20, 28, 40), (self.window_w - 20, self.board_px + 10), 180)

        # Main board/card container
        container = pygame.Rect(8, 8, self.window_w - 16, self.board_px - 16)
        self._draw_card(container, (18, 21, 28), border=(52, 59, 72), radius=24)

        # Header strip
        header = pygame.Rect(18, 14, self.window_w - 36, 36)
        pygame.draw.rect(self.screen, (24, 28, 37), header, border_radius=14)
        title = self.font.render("Rohans Engine", True, (244, 246, 250))
        self.screen.blit(title, (28, 23))
        subtitle = self.font_small.render("Analysis dashboard · human vs engine", True, (153, 161, 176))
        self.screen.blit(subtitle, (28 + title.get_width() + 10, 25))
        if self.white_time_left is not None and self.black_time_left is not None:
            clock_text = f"W {self._format_clock(self.white_time_left)}   B {self._format_clock(self.black_time_left)}"
            clock_surf = self.font_small.render(clock_text, True, (205, 214, 228))
            self.screen.blit(clock_surf, (self.window_w - clock_surf.get_width() - 28, 25))

        # Eval bar
        ratio = self._score_to_ratio(self._analysis_eval)
        eval_rect = pygame.Rect(0, 0, self.eval_w, self.board_px)
        pygame.draw.rect(self.screen, (18, 21, 28), eval_rect)
        white_h = int(round(self.board_px * ratio))
        pygame.draw.rect(self.screen, (241, 241, 243), pygame.Rect(0, 0, self.eval_w, white_h))
        pygame.draw.rect(
            self.screen,
            (8, 8, 10),
            pygame.Rect(0, white_h, self.eval_w, self.board_px - white_h),
        )
        pygame.draw.rect(self.screen, (58, 65, 77), eval_rect, 1)

        # Board squares
        for row in range(8):
            for col in range(8):
                x, y = self._square_to_screen(row, col)
                rect = pygame.Rect(x, y, self.square_size, self.square_size)
                base = light if (row + col) % 2 == 0 else dark
                pygame.draw.rect(self.screen, base, rect)

                # Coordinate labels (like chess.com): files on the visual bottom rank, ranks on the visual left file.
                show_file = (not self.flipped and row == 7) or (self.flipped and row == 0)
                show_rank = (not self.flipped and col == 0) or (self.flipped and col == 7)
                if show_file:
                    file_ch = chr(ord('a') + col)
                    txt = self.font_small.render(file_ch, True, (90, 95, 110))
                    self.screen.blit(txt, (x + self.square_size - txt.get_width() - 4, y + self.square_size - txt.get_height() - 2))
                if show_rank:
                    rank_ch = str(8 - row)
                    txt = self.font_small.render(rank_ch, True, (90, 95, 110))
                    self.screen.blit(txt, (x + 4, y + 2))

        # Last move highlight
        if self.last_move is not None:
            (sr, sc), (er, ec) = self.last_move
            for (r, c) in ((sr, sc), (er, ec)):
                x, y = self._square_to_screen(r, c)
                rect = pygame.Rect(x, y, self.square_size, self.square_size)
                s = pygame.Surface((self.square_size, self.square_size), pygame.SRCALPHA)
                s.fill((*last, 90))
                self.screen.blit(s, rect.topleft)

        # Selected square
        if self.selected is not None:
            x, y = self._square_to_screen(*self.selected)
            rect = pygame.Rect(x, y, self.square_size, self.square_size)
            pygame.draw.rect(self.screen, sel, rect, 4)

        # Legal move dots (shown only for current selection)
        best_move = self._analysis_suggested_moves[0] if self._analysis_suggested_moves else None
        best_from_selected = None
        if best_move is not None and self.selected is not None:
            s, e, p, sc, san = best_move
            if s == self.selected:
                best_from_selected = e

        for (r, c) in self.legal_ends:
            x, y = self._square_to_screen(r, c)
            center = (x + self.square_size // 2, y + self.square_size // 2)
            color = best_legal if best_from_selected == (r, c) else legal
            pygame.draw.circle(self.screen, color, center, self.square_size // 8)

        # Check highlight
        stm_is_white = self._side_to_move_is_white()
        if self.mg.is_in_check(stm_is_white):
            kr, kc = self.board.white_king_pos if stm_is_white else self.board.black_king_pos
            x, y = self._square_to_screen(kr, kc)
            rect = pygame.Rect(x, y, self.square_size, self.square_size)
            pygame.draw.rect(self.screen, check, rect, 4)

        # Suggested move arrow (only best; gold if "brilliant")
        review_pick = self._selected_review_entry()
        if review_pick is not None:
            s, e, _p = review_pick["start"], review_pick["end"], review_pick["promo"]
            col = (110, 210, 160, 195)
            self._draw_arrow(s, e, rgba=col, width=10)
        elif self._analysis_suggested_moves:
            s, e, _p, _sc, _san = self._analysis_suggested_moves[0]
            if self._is_brilliant_best():
                col = (245, 200, 60, 200)
            else:
                col = (90, 140, 220, 190)
            self._draw_arrow(s, e, rgba=col, width=10)

        # Pieces (with optional animation overlay)
        anim_piece = None
        anim_end_sq = None
        anim_pos = None

        if self._anim_active and self._anim_piece and self._anim_start_sq and self._anim_end_sq:
            dt_ms = (time.perf_counter() - self._anim_t0) * 1000.0
            t = min(1.0, max(0.0, dt_ms / float(self._anim_ms)))
            t_smooth = t * t * (3 - 2 * t)  # smoothstep
            sx, sy = self._square_to_screen(*self._anim_start_sq)
            ex, ey = self._square_to_screen(*self._anim_end_sq)
            anim_pos = (sx + (ex - sx) * t_smooth, sy + (ey - sy) * t_smooth)
            anim_piece = self._anim_piece
            anim_end_sq = self._anim_end_sq
            if t >= 1.0:
                self._anim_active = False

        for row in range(8):
            for col in range(8):
                piece = self.board.board[row][col]
                if piece == ".":
                    continue
                img = self.piece_images.get(piece)
                if img is None:
                    continue

                # If animating, skip drawing the moving piece on its destination square.
                if anim_end_sq is not None and (row, col) == anim_end_sq and piece == anim_piece:
                    continue

                x, y = self._square_to_screen(row, col)
                self.screen.blit(img, (x, y))

        if anim_piece is not None and anim_pos is not None:
            img = self.piece_images.get(anim_piece)
            if img is not None:
                self.screen.blit(img, anim_pos)

        # Sidebar: analysis
        sidebar_x = self.eval_w + self.board_px
        sidebar_rect = pygame.Rect(sidebar_x, 0, self.sidebar_w, self.board_px)
        self._draw_card(sidebar_rect, (16, 19, 26), border=(52, 59, 72), radius=24)
        inner = pygame.Rect(sidebar_x + 12, 12, self.sidebar_w - 24, self.board_px - 24)
        self._draw_card(inner, (20, 24, 32), border=(44, 51, 63), radius=18)

        self._draw_section_title(sidebar_x + 18, 18, "Analysis")

        depth_text = self.font_small.render(f"depth {self._analysis_depth}", True, (154, 162, 177))
        self.screen.blit(depth_text, (sidebar_x + self.sidebar_w - depth_text.get_width() - 18, 20))

        eval_norm = self._score_to_norm(self._analysis_eval)
        eval_norm = max(-1.0, min(1.0, eval_norm))
        eval_label = f"Eval {eval_norm:+.2f}"
        eval_line = self.font_small.render(eval_label, True, (233, 236, 241))
        self.screen.blit(eval_line, (sidebar_x + 18, 44))
        if self._analysis_thinking:
            note = self.font_small.render("Analyzing…", True, (124, 174, 255))
            self.screen.blit(note, (sidebar_x + 120, 44))

        self._draw_eval_meter(sidebar_x + 18, 70, self.sidebar_w - 36, 14, self._analysis_eval)

        # Opening theory / book guidance
        theory_card = pygame.Rect(sidebar_x + 18, 92, self.sidebar_w - 36, 60)
        self._draw_card(theory_card, (24, 29, 38), border=(62, 70, 84), radius=14)
        self._draw_section_title(sidebar_x + 28, 100, self._opening_theory_label, accent=(245, 200, 60))
        theory_lines = [self._opening_theory_detail]
        if self.move_san_history:
            theory_lines.append(f"Plies played: {len(self.move_san_history)}")
        for idx, line_text in enumerate(theory_lines[:2]):
            surf = self.font_tiny.render(line_text, True, (210, 216, 226))
            self.screen.blit(surf, (sidebar_x + 28, 120 + idx * 14))

        # Best principal variation card
        pv_card = pygame.Rect(sidebar_x + 18, 160, self.sidebar_w - 36, 86)
        self._draw_card(pv_card, (27, 32, 41), border=(65, 74, 89), radius=14)
        self._draw_section_title(sidebar_x + 28, 178, "Best line", accent=(255, 203, 71))

        pv_text = self._analysis_best_pv_text if self._analysis_best_pv_text else "Waiting for engine line…"
        words = pv_text.split(" ")
        wrapped: list[str] = []
        line_words: list[str] = []
        max_w = self.sidebar_w - 56
        for w in words:
            cand = (" ".join(line_words + [w])).strip()
            if line_words and self.font_small.size(cand)[0] > max_w:
                wrapped.append(" ".join(line_words))
                line_words = [w]
            else:
                line_words.append(w)
        if line_words:
            wrapped.append(" ".join(line_words))
        for idx, line_text in enumerate(wrapped[:3]):
            surf = self.font_tiny.render(line_text, True, (227, 231, 239))
            self.screen.blit(surf, (sidebar_x + 28, 190 + idx * 14))

        # Review or move suggestions
        sugg_top = 252
        sugg_card = pygame.Rect(sidebar_x + 18, sugg_top, self.sidebar_w - 36, self.board_px - sugg_top - 18)
        self._draw_card(sugg_card, (25, 29, 37), border=(63, 72, 87), radius=14)
        self._review_row_hitboxes = []

        game_status = self.mg.get_game_status()
        if self.review_mode or game_status["is_over"]:
            review_card = pygame.Rect(sidebar_x + 18, sugg_top + 8, self.sidebar_w - 36, self.board_px - sugg_top - 26)
            self._draw_card(review_card, (24, 28, 36), border=(63, 72, 87), radius=14)
            self._draw_section_title(sidebar_x + 28, sugg_top + 18, "Game review", accent=(112, 255, 189))

            white_acc = self._review_accuracy("white")
            black_acc = self._review_accuracy("black")
            result_msg = self.game_over_reason or game_status["message"]
            result_surf = self.font_tiny.render(result_msg, True, (235, 239, 245))
            self.screen.blit(result_surf, (sidebar_x + 28, sugg_top + 38))

            counts_white = self._review_counts("white")
            counts_black = self._review_counts("black")

            stat_box = pygame.Rect(sidebar_x + 24, sugg_top + 56, self.sidebar_w - 48, 70)
            self._draw_card(stat_box, (20, 24, 32), border=(56, 64, 76), radius=12)
            stat_lines = [
                f"Accuracy   W {white_acc:0.1f}   B {black_acc:0.1f}",
                f"Best {counts_white.get('Best', 0):>2}/{counts_black.get('Best', 0):>2}  Good {counts_white.get('Good', 0):>2}/{counts_black.get('Good', 0):>2}  Miss {counts_white.get('Miss', 0):>2}/{counts_black.get('Miss', 0):>2}",
                f"Blunder {counts_white.get('Blunder', 0):>2}/{counts_black.get('Blunder', 0):>2}  Inacc {counts_white.get('Inaccuracy', 0):>2}/{counts_black.get('Inaccuracy', 0):>2}",
            ]
            for idx, line in enumerate(stat_lines):
                surf = self.font_tiny.render(line, True, (212, 218, 228))
                self.screen.blit(surf, (sidebar_x + 34, sugg_top + 70 + idx * 16))

            rows = self._review_rows()
            list_y = sugg_top + 138
            list_h = self.board_px - list_y - 20
            list_card = pygame.Rect(sidebar_x + 24, list_y, self.sidebar_w - 48, list_h)
            self._draw_card(list_card, (22, 26, 34), border=(56, 64, 76), radius=12)
            self._draw_section_title(sidebar_x + 34, list_y + 8, "Move by move", accent=(112, 255, 189))

            row_y = list_y + 28
            row_width = self.sidebar_w - 56
            visible_rows = rows[-8:]
            for row in visible_rows:
                entry = row
                label = f"{entry['index'] + 1:02d}. {entry['san']}"
                detail = f"{entry['classification']} {entry['loss_cp']}cp"
                row_rect = pygame.Rect(sidebar_x + 28, row_y, row_width, 24)
                self._review_row_hitboxes.append((entry['index'], row_rect))
                selected = entry['index'] == self.review_selected_index
                fill = (41, 50, 63) if selected else (29, 33, 41)
                border = (110, 210, 160) if selected else (58, 64, 76)
                self._draw_card(row_rect, fill, border=border, radius=8)
                label_surf = self.font_tiny.render(label, True, (240, 243, 248))
                detail_surf = self.font_tiny.render(detail, True, (173, 181, 194))
                self.screen.blit(label_surf, (row_rect.x + 8, row_rect.y + 4))
                self.screen.blit(detail_surf, (row_rect.right - detail_surf.get_width() - 8, row_rect.y + 4))
                row_y += 26

            hint = self.font_tiny.render("Click any move row to show it on the board", True, (148, 157, 170))
            self.screen.blit(hint, (sidebar_x + 28, self.board_px - 20))
        else:
            self._draw_section_title(sidebar_x + 28, sugg_top + 8, "Move suggestions", accent=(112, 255, 189))

            lines = self._analysis_multipv if self._analysis_multipv else [(sc, san) for (san, sc) in self._analysis_lines[:8]]
            if not lines:
                empty = self.font_small.render("No legal lines available yet.", True, (150, 157, 171))
                self.screen.blit(empty, (sidebar_x + 28, sugg_top + 34))
            else:
                y = sugg_top + 34
                for idx, (score, move_text) in enumerate(lines[:8], 1):
                    row_h = self._draw_move_row(sidebar_x + 24, y, self.sidebar_w - 48, move_text, score, idx, selected=(idx == 1))
                    y += row_h + 8
                    if y + 34 > self.board_px - 18:
                        break

        # Bottom panel
        panel_rect = pygame.Rect(0, self.board_px, self.window_w, self.panel_h)
        pygame.draw.rect(self.screen, (12, 15, 20), panel_rect)
        pygame.draw.line(self.screen, (58, 65, 77), (0, self.board_px), (self.window_w, self.board_px), 1)

        status = self.game_over_reason or self.mg.get_game_status()["message"]
        if self._ai_thinking:
            status = status + " — AI thinking…"
        if self.pending_promotion is not None:
            status = "Promotion: press Q/R/B/N"

        role = "Human=White" if self.human_plays_white else "Human=Black"
        help_text = f"{role} | Difficulty {self.difficulty} (1-5) | U=undo | F=flip | R=reset | 1-5 switch strength"

        s1 = self.font.render(status, True, (240, 242, 246))
        s2 = self.font_small.render(help_text, True, (158, 165, 180))
        self.screen.blit(s1, (16, self.board_px + 10))
        self.screen.blit(s2, (16, self.board_px + 42))

    # ------------------------------------------------------------------
    # Input
    # ------------------------------------------------------------------

    def _refresh_selection(self):
        if self.selected is None:
            self.legal_ends = []
            return
        r, c = self.selected
        piece = self.board.get_piece(r, c)
        if piece == ".":
            self.selected = None
            self.legal_ends = []
            return
        if self._side_to_move_is_white() != piece.isupper():
            self.selected = None
            self.legal_ends = []
            return
        self.legal_ends = self.mg.get_legal_moves(r, c)

    def _handle_click(self, pos):
        if self.pending_promotion is not None:
            return
        if self.review_mode or self.game_over_status != "ongoing":
            for index, rect in self._review_row_hitboxes:
                if rect.collidepoint(pos):
                    self._select_review_entry(index)
                    return
            if self.review_mode:
                return
        if self.game_over_status != "ongoing":
            return
        if not self._human_to_move():
            return
        if self.mg.get_game_status()["is_over"]:
            return

        sq = self._screen_to_square(*pos)
        if sq is None:
            return
        r, c = sq

        if self.selected is None:
            piece = self.board.get_piece(r, c)
            if piece == ".":
                return
            if piece.isupper() != self._side_to_move_is_white():
                return
            self.selected = (r, c)
            self._refresh_selection()
            return

        # Attempt move (we do NOT precompute highlights; only validate here).
        start = self.selected
        if (r, c) in self.legal_ends:
            end = (r, c)
            if self._needs_promotion(start, end):
                self.pending_promotion = (start, end)
                return
            self._apply_move(start, end, None)
            return

        # Otherwise update selection.
        piece = self.board.get_piece(r, c)
        if piece != "." and piece.isupper() == self._side_to_move_is_white():
            self.selected = (r, c)
            self._refresh_selection()
        else:
            self.selected = None
            self.legal_ends = []

    def _handle_key(self, key):
        if key in (pygame.K_1, pygame.K_2, pygame.K_3, pygame.K_4, pygame.K_5):
            self.difficulty = int(pygame.key.name(key))
            self._start_analysis()
            return

        if key == pygame.K_v:
            self.review_mode = not self.review_mode
            if self.review_mode and self.move_history_review:
                self.review_selected_index = len(self.move_history_review) - 1
            return

        if key == pygame.K_u:
            if not self._ai_thinking:
                self.undo_full_move()
            return

        if key == pygame.K_f:
            self.flipped = not self.flipped
            return

        if key == pygame.K_r:
            if self._ai_thinking:
                return
            self.__init__(
                square_size=self.square_size,
                human_plays_white=self.human_plays_white,
                difficulty=self.difficulty,
                time_minutes=self.time_minutes,
                increment_seconds=self.increment_seconds,
            )
            return

        # Promotion choice
        if self.pending_promotion is not None:
            promo = self._promotion_from_key(key)
            if promo is None:
                return
            start, end = self.pending_promotion
            self.pending_promotion = None
            self._apply_move(start, end, promo)
            self.selected = None
            return

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self):
        running = True
        while running:
            self._tick_clocks()
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    self._handle_click(event.pos)
                elif event.type == pygame.KEYDOWN:
                    self._handle_key(event.key)

            self._consume_ai_move_if_ready()
            self._start_ai_if_needed()

            self._draw_board()
            pygame.display.flip()
            self.clock.tick(60)

        pygame.quit()
