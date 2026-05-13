import math
import threading
import time
from dataclasses import dataclass


from engine.board import Board
from engine.move_generator import MoveGenerator
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
    ):
        if pygame is None:  # pragma: no cover
            raise RuntimeError(
                "pygame is not installed. Install requirements.txt first. "
                f"Import error: {_PYGAME_IMPORT_ERROR}"
            )

        self.square_size = square_size
        self.board_px = 8 * square_size

        self.eval_w = 26
        self.sidebar_w = 320
        self.panel_h = 90

        self.board_origin_x = self.eval_w
        self.board_origin_y = 0

        self.window_w = self.eval_w + self.board_px + self.sidebar_w
        self.window_h = self.board_px + self.panel_h

        self.human_plays_white = human_plays_white
        self.difficulty = max(1, min(5, int(difficulty)))
        self.flipped = False

        self.board = Board()
        self.mg = MoveGenerator(self.board)

        self.selected: tuple[int, int] | None = None
        self.last_move: tuple[tuple[int, int], tuple[int, int]] | None = None
        self.undo_stack: list[_UndoRecord] = []

        self.pending_promotion: tuple[tuple[int, int], tuple[int, int]] | None = None

        self._position_generation = 0

        self._ai_lock = threading.Lock()
        self._ai_thinking = False
        self._ai_best_move: tuple[tuple[int, int], tuple[int, int], str | None] | None = None

        self._analysis_lock = threading.Lock()
        self._analysis_thinking = False
        self._analysis_eval = 0.0
        self._analysis_lines: list[tuple[str, float]] = []  # list[(san, score)] white-perspective

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

        self._load_images()

        # Initial analysis for the starting position
        self._start_analysis()

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
        return self._side_to_move_is_white() == self.human_plays_white

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
            )
        )

    def _apply_move(self, start, end, promo: str | None = None):
        moving_piece = self.board.get_piece(start[0], start[1])
        prev_turn = self.board.turn
        prev_counts = self.board.position_counts.copy()
        move_state = self.board.make_move(start, end, promo)

        # Update turn + repetition counts for gameplay (engine search doesn't use this).
        self.board.turn = "black" if self.board.turn == "white" else "white"
        self.board.record_current_position()

        self._push_undo(start, end, move_state, prev_turn, prev_counts)
        self.last_move = (start, end)

        # Start animation (visual only)
        self._anim_active = True
        self._anim_piece = moving_piece
        self._anim_start_sq = start
        self._anim_end_sq = end
        self._anim_t0 = time.perf_counter()

        # Bump generation and refresh analysis
        self._position_generation += 1
        self._start_analysis()

    def _undo_halfmove(self):
        if not self.undo_stack:
            return
        rec = self.undo_stack.pop()
        self.board.turn = rec.prev_turn
        self.board.undo_move(rec.start, rec.end, rec.move_state)
        self.board.position_counts = rec.prev_position_counts
        self.last_move = None

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
        return self.difficulty

    # ------------------------------------------------------------------
    # Engine analysis (suggested moves + eval)
    # ------------------------------------------------------------------

    @staticmethod
    def _score_to_ratio(score: float) -> float:
        # Map pawns to a 0..1 bar. Clamp mates to extremes.
        if score >= 900:
            return 1.0
        if score <= -900:
            return 0.0
        return 0.5 + 0.5 * math.tanh(score / 4.0)

    @staticmethod
    def _format_score(score: float) -> str:
        if score >= 900:
            return "#"
        if score <= -900:
            return "#"
        return f"{score:+.2f}"

    def _start_analysis(self):
        if self._analysis_thinking:
            return
        if self.pending_promotion is not None:
            return

        gen = self._position_generation
        snapshot = self._capture_position()
        self._analysis_thinking = True
        t = threading.Thread(target=self._analysis_worker, args=(gen, snapshot), daemon=True)
        t.start()

    def _analysis_worker(self, gen: int, snapshot: dict):
        with self._analysis_lock:
            b = self._board_from_snapshot(snapshot)
            mg = MoveGenerator(b)
            eval_now = float(mg.evaluate_position())

            root_scores_latest: list[tuple[tuple, float]] | None = None

            def _on_depth_complete(depth, score_for_display, pv, root_scores_display=None):
                nonlocal root_scores_latest
                if root_scores_display is None:
                    return
                root_scores_latest = list(root_scores_display)

            depth = max(2, self._difficulty_to_depth())

            try:
                mg.find_best_move(
                    depth=depth,
                    is_white_turn=(b.turn == "white"),
                    max_time=0.6,
                    verbose=False,
                    on_depth_complete=_on_depth_complete,
                    use_book=True,
                )
            except Exception:
                pass

            lines: list[tuple[str, float]] = []
            if root_scores_latest:
                # Keep top 8 moves
                for (move, score) in root_scores_latest[:8]:
                    start, end, promo = move
                    try:
                        san = move_to_san(b, mg, start, end, promo)
                    except Exception:
                        san = f"{b.square_to_algebraic(start[0], start[1])}{b.square_to_algebraic(end[0], end[1])}"
                        if promo:
                            san += promo.lower()
                    lines.append((san, float(score)))

            # Apply results if still current
            if gen == self._position_generation:
                self._analysis_eval = eval_now
                self._analysis_lines = lines

            self._analysis_thinking = False

    def _start_ai_if_needed(self):
        if self._ai_thinking:
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
            best_move, _score = mg.find_best_move(
                depth=depth,
                is_white_turn=ai_is_white,
                max_time=None,
                verbose=False,
                use_book=True,
            )

            if gen == self._position_generation:
                self._ai_best_move = best_move
            self._ai_thinking = False

    def _consume_ai_move_if_ready(self):
        if self._ai_thinking:
            return
        if self._ai_best_move is None:
            return
        if self._human_to_move():
            # Human moved while engine was thinking; discard.
            self._ai_best_move = None
            return

        start, end, promo = self._ai_best_move
        self._ai_best_move = None
        self._apply_move(start, end, promo)
        self.selected = None

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _draw_board(self):
        light = (240, 217, 181)
        dark = (181, 136, 99)
        sel = (246, 246, 105)
        last = (170, 205, 100)
        check = (220, 80, 90)

        self.screen.fill((20, 20, 24))

        # Eval bar
        ratio = self._score_to_ratio(self._analysis_eval)
        eval_rect = pygame.Rect(0, 0, self.eval_w, self.board_px)
        pygame.draw.rect(self.screen, (20, 20, 24), eval_rect)
        white_h = int(round(self.board_px * ratio))
        pygame.draw.rect(self.screen, (245, 245, 245), pygame.Rect(0, 0, self.eval_w, white_h))
        pygame.draw.rect(
            self.screen,
            (10, 10, 10),
            pygame.Rect(0, white_h, self.eval_w, self.board_px - white_h),
        )
        pygame.draw.rect(self.screen, (45, 48, 56), eval_rect, 1)

        # Board squares
        for row in range(8):
            for col in range(8):
                x, y = self._square_to_screen(row, col)
                rect = pygame.Rect(x, y, self.square_size, self.square_size)
                base = light if (row + col) % 2 == 0 else dark
                pygame.draw.rect(self.screen, base, rect)

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

        # Check highlight
        stm_is_white = self._side_to_move_is_white()
        if self.mg.is_in_check(stm_is_white):
            kr, kc = self.board.white_king_pos if stm_is_white else self.board.black_king_pos
            x, y = self._square_to_screen(kr, kc)
            rect = pygame.Rect(x, y, self.square_size, self.square_size)
            pygame.draw.rect(self.screen, check, rect, 4)

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

        # Sidebar: suggested moves
        sidebar_x = self.eval_w + self.board_px
        sidebar_rect = pygame.Rect(sidebar_x, 0, self.sidebar_w, self.board_px)
        pygame.draw.rect(self.screen, (16, 18, 22), sidebar_rect)
        pygame.draw.rect(self.screen, (45, 48, 56), sidebar_rect, 1)

        title = self.font.render("SUGGESTED MOVES", True, (230, 230, 236))
        self.screen.blit(title, (sidebar_x + 12, 10))

        if self._analysis_thinking:
            note = self.font_small.render("Analyzing…", True, (170, 175, 190))
            self.screen.blit(note, (sidebar_x + 12, 38))

        y = 66
        for idx, (san, score) in enumerate(self._analysis_lines[:8], 1):
            line = f"{idx}. {san:<8}  {self._format_score(score):>6}"
            surf = self.font_small.render(line, True, (200, 205, 220))
            self.screen.blit(surf, (sidebar_x + 12, y))
            y += 22

        # Bottom panel
        panel_rect = pygame.Rect(0, self.board_px, self.window_w, self.panel_h)
        pygame.draw.rect(self.screen, (16, 18, 22), panel_rect)

        status = self.mg.get_game_status()["message"]
        if self._ai_thinking:
            status = status + " — AI thinking…"
        if self.pending_promotion is not None:
            status = "Promotion: press Q/R/B/N"

        role = "Human=White" if self.human_plays_white else "Human=Black"
        help_text = f"{role} | Difficulty {self.difficulty} (1-5) | U=undo | F=flip | R=reset"

        s1 = self.font.render(status, True, (235, 235, 240))
        s2 = self.font_small.render(help_text, True, (170, 175, 190))
        self.screen.blit(s1, (10, self.board_px + 10))
        self.screen.blit(s2, (10, self.board_px + 44))

    # ------------------------------------------------------------------
    # Input
    # ------------------------------------------------------------------

    def _handle_click(self, pos):
        if self.pending_promotion is not None:
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
            return

        # Attempt move (we do NOT precompute highlights; only validate here).
        start = self.selected
        legal_ends = self.mg.get_legal_moves(start[0], start[1])
        if (r, c) in legal_ends:
            end = (r, c)
            if self._needs_promotion(start, end):
                self.pending_promotion = (start, end)
                return
            self._apply_move(start, end, None)
            self.selected = None
            return

        # Otherwise update selection.
        piece = self.board.get_piece(r, c)
        if piece != "." and piece.isupper() == self._side_to_move_is_white():
            self.selected = (r, c)
        else:
            self.selected = None

    def _handle_key(self, key):
        if key in (pygame.K_1, pygame.K_2, pygame.K_3, pygame.K_4, pygame.K_5):
            self.difficulty = int(pygame.key.name(key))
            self._start_analysis()
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
