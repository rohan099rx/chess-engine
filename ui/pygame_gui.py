import threading
from dataclasses import dataclass


from engine.board import Board
from engine.move_generator import MoveGenerator


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
        self.panel_h = 80
        self.window_w = self.board_px
        self.window_h = self.board_px + self.panel_h

        self.human_plays_white = human_plays_white
        self.difficulty = max(1, min(5, int(difficulty)))
        self.flipped = False

        self.board = Board()
        self.mg = MoveGenerator(self.board)

        self.selected: tuple[int, int] | None = None
        self.legal_ends: list[tuple[int, int]] = []
        self.last_move: tuple[tuple[int, int], tuple[int, int]] | None = None
        self.undo_stack: list[_UndoRecord] = []

        self.pending_promotion: tuple[tuple[int, int], tuple[int, int]] | None = None

        self._ai_lock = threading.Lock()
        self._ai_thinking = False
        self._ai_best_move: tuple[tuple[int, int], tuple[int, int], str | None] | None = None
        self._status = ""

        pygame.init()
        pygame.display.set_caption("Chess (Pygame) — Rohans Engine")
        self.screen = pygame.display.set_mode((self.window_w, self.window_h))
        self.clock = pygame.time.Clock()
        self.font = pygame.font.SysFont("Menlo", 18)
        self.font_small = pygame.font.SysFont("Menlo", 14)

        self._load_images()

    # ------------------------------------------------------------------
    # Assets
    # ------------------------------------------------------------------

    def _load_images(self):
        self.piece_images: dict[str, pygame.Surface] = {}
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
        return col * self.square_size, row * self.square_size

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
        prev_turn = self.board.turn
        prev_counts = self.board.position_counts.copy()
        move_state = self.board.make_move(start, end, promo)

        # Update turn + repetition counts for gameplay (engine search doesn't use this).
        self.board.turn = "black" if self.board.turn == "white" else "white"
        self.board.record_current_position()

        self._push_undo(start, end, move_state, prev_turn, prev_counts)
        self.last_move = (start, end)

    def _undo_halfmove(self):
        if not self.undo_stack:
            return
        rec = self.undo_stack.pop()
        self.board.turn = rec.prev_turn
        self.board.undo_move(rec.start, rec.end, rec.move_state)
        self.board.position_counts = rec.prev_position_counts
        self.last_move = None

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
        return self.difficulty

    def _start_ai_if_needed(self):
        if self._ai_thinking:
            return
        if self._human_to_move():
            return
        if self.mg.get_game_status()["is_over"]:
            return

        self._ai_thinking = True
        self._ai_best_move = None
        t = threading.Thread(target=self._ai_worker, daemon=True)
        t.start()

    def _ai_worker(self):
        with self._ai_lock:
            ai_is_white = self._side_to_move_is_white()
            legal = self.mg.generate_all_legal_moves(ai_is_white)
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
            best_move, _score = self.mg.find_best_move(
                depth=depth,
                is_white_turn=ai_is_white,
                max_time=None,
                verbose=False,
                use_book=True,
            )
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
        self.legal_ends = []

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _draw_board(self):
        light = (240, 217, 181)
        dark = (181, 136, 99)
        sel = (246, 246, 105)
        last = (170, 205, 100)
        legal = (90, 140, 220)
        check = (220, 80, 90)

        self.screen.fill((20, 20, 24))

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

        # Legal move dots
        for (r, c) in self.legal_ends:
            x, y = self._square_to_screen(r, c)
            center = (x + self.square_size // 2, y + self.square_size // 2)
            pygame.draw.circle(self.screen, legal, center, self.square_size // 8)

        # Check highlight
        stm_is_white = self._side_to_move_is_white()
        if self.mg.is_in_check(stm_is_white):
            kr, kc = self.board.white_king_pos if stm_is_white else self.board.black_king_pos
            x, y = self._square_to_screen(kr, kc)
            rect = pygame.Rect(x, y, self.square_size, self.square_size)
            pygame.draw.rect(self.screen, check, rect, 4)

        # Pieces
        for row in range(8):
            for col in range(8):
                piece = self.board.board[row][col]
                if piece == ".":
                    continue
                img = self.piece_images.get(piece)
                if img is None:
                    continue
                x, y = self._square_to_screen(row, col)
                self.screen.blit(img, (x, y))

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
        self.screen.blit(s2, (10, self.board_px + 42))

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

        # If clicked a legal destination, move.
        if (r, c) in self.legal_ends:
            start = self.selected
            end = (r, c)
            if self._needs_promotion(start, end):
                self.pending_promotion = (start, end)
                return
            self._apply_move(start, end, None)
            self.selected = None
            self.legal_ends = []
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
            self.legal_ends = []
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
