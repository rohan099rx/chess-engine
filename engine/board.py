import random
from enum import Enum


_ZOBRIST_RNG = random.Random(20260501)


# ============================================================================
# STOCKFISH-INSPIRED OPTIMIZATION: Pre-computed Attack Lookup Tables
# ============================================================================
# Like Stockfish's magic bitboards, we pre-compute knight and king attacks
# to avoid repeated calculation. This speeds up check detection significantly.

def _compute_knight_attacks():
    """Pre-compute all possible knight attacks from each square (Stockfish-inspired)."""
    attacks = {}
    knight_deltas = [(-2, -1), (-2, 1), (-1, -2), (-1, 2), (1, -2), (1, 2), (2, -1), (2, 1)]
    for row in range(8):
        for col in range(8):
            attacks[(row, col)] = []
            for dr, dc in knight_deltas:
                nr, nc = row + dr, col + dc
                if 0 <= nr < 8 and 0 <= nc < 8:
                    attacks[(row, col)].append((nr, nc))
    return attacks

def _compute_king_attacks():
    """Pre-compute all possible king attacks from each square (Stockfish-inspired)."""
    attacks = {}
    king_deltas = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
    for row in range(8):
        for col in range(8):
            attacks[(row, col)] = []
            for dr, dc in king_deltas:
                nr, nc = row + dr, col + dc
                if 0 <= nr < 8 and 0 <= nc < 8:
                    attacks[(row, col)].append((nr, nc))
    return attacks

def _compute_pawn_attacks():
    """Pre-compute all possible pawn attacks from each square (Stockfish-inspired)."""
    attacks = {"white": {}, "black": {}}
    for row in range(8):
        for col in range(8):
            # White pawns attack diagonally upward (toward row 0)
            if row > 0:
                if col > 0:
                    attacks["white"][(row, col)] = attacks["white"].get((row, col), []) + [(row - 1, col - 1)]
                if col < 7:
                    attacks["white"][(row, col)] = attacks["white"].get((row, col), []) + [(row - 1, col + 1)]
            # Black pawns attack diagonally downward (toward row 7)
            if row < 7:
                if col > 0:
                    attacks["black"][(row, col)] = attacks["black"].get((row, col), []) + [(row + 1, col - 1)]
                if col < 7:
                    attacks["black"][(row, col)] = attacks["black"].get((row, col), []) + [(row + 1, col + 1)]
    return attacks

# Global lookup tables (computed once at module load, like Stockfish)
_KNIGHT_ATTACKS = _compute_knight_attacks()
_KING_ATTACKS = _compute_king_attacks()
_PAWN_ATTACKS = _compute_pawn_attacks()


class Board:
    # Unicode chess pieces for chess.com-style display
    PIECE_UNICODE = {
        "K": "♔", "Q": "♕", "R": "♖", "B": "♗", "N": "♘", "P": "♙",
        "k": "♚", "q": "♛", "r": "♜", "b": "♝", "n": "♞", "p": "♟",
        ".": " ",
    }
    
    PIECE_TO_ZOBRIST_INDEX = {
        "P": 0, "N": 1, "B": 2, "R": 3, "Q": 4, "K": 5,
        "p": 6, "n": 7, "b": 8, "r": 9, "q": 10, "k": 11,
    }
    ZOBRIST_PIECES = [
        [
            [_ZOBRIST_RNG.getrandbits(64) for _ in range(12)]
            for _ in range(8)
        ]
        for _ in range(8)
    ]
    ZOBRIST_SIDE_TO_MOVE = _ZOBRIST_RNG.getrandbits(64)
    ZOBRIST_CASTLING = [_ZOBRIST_RNG.getrandbits(64) for _ in range(16)]
    ZOBRIST_EN_PASSANT = [_ZOBRIST_RNG.getrandbits(64) for _ in range(8)]

    def __init__(self):
        self.board = self.create_starting_position()
        self.turn = "white"
        self.en_passant_target = None
        self.halfmove_clock = 0
        self.castling_rights = {
            "white_kingside": True,
            "white_queenside": True,
            "black_kingside": True,
            "black_queenside": True,
        }
        # Cached king positions — updated incrementally in make_move/undo_move
        self.white_king_pos = (7, 4)
        self.black_king_pos = (0, 4)
        # Efficiency: cache castling state index (needed for zobrist_hash)
        self._cached_castling_index = self.get_castling_state_index()
        self.piece_positions = self.compute_piece_positions()
        self._piece_hash = self.compute_piece_hash()
        self.position_counts = {self.get_position_key(): 1}
        # Move history stack for undo/redo
        self.move_history = []
        # Cached material count for evaluation
        self._material_count = {"white": 0, "black": 0}
        self._compute_material_count()

    def create_starting_position(self):
        return [
            ["r", "n", "b", "q", "k", "b", "n", "r"],
            ["p", "p", "p", "p", "p", "p", "p", "p"],
            [".", ".", ".", ".", ".", ".", ".", "."],
            [".", ".", ".", ".", ".", ".", ".", "."],
            [".", ".", ".", ".", ".", ".", ".", "."],
            [".", ".", ".", ".", ".", ".", ".", "."],
            ["P", "P", "P", "P", "P", "P", "P", "P"],
            ["R", "N", "B", "Q", "K", "B", "N", "R"],
        ]

    def print_board(self, highlight_squares=None):
        """Print board in chess.com style with Unicode pieces and alternating colors."""
        if highlight_squares is None:
            highlight_squares = set()
        
        # ANSI color codes
        LIGHT_BG = "\033[48;5;230m"  # Light square
        DARK_BG = "\033[48;5;94m"    # Dark square
        HIGHLIGHT_BG = "\033[48;5;226m"  # Yellow highlight
        RESET = "\033[0m"
        
        print("\n" + " " * 4 + "  a   b   c   d   e   f   g   h")
        
        for row_idx in range(8):
            rank = 8 - row_idx
            print(f" {rank} ", end="")
            
            for col_idx in range(8):
                is_light_square = (row_idx + col_idx) % 2 == 0
                bg_color = LIGHT_BG if is_light_square else DARK_BG
                
                if (row_idx, col_idx) in highlight_squares:
                    bg_color = HIGHLIGHT_BG
                
                piece = self.board[row_idx][col_idx]
                unicode_piece = self.PIECE_UNICODE.get(piece, " ")
                
                print(f"{bg_color} {unicode_piece}  {RESET}", end="")
            
            print(f" {rank}")
        
        print(" " * 4 + "  a   b   c   d   e   f   g   h")
        print(f"\n Turn: {self.turn.upper()}\n")
    
    def print_board_simple(self, highlight_squares=None):
        """Print board in simple text format with algebraic coordinates (a-h, 1-8)."""
        if highlight_squares is None:
            highlight_squares = set()
        
        print("\n  +---+---+---+---+---+---+---+---+")
        
        for row_idx in range(8):
            rank = 8 - row_idx
            print(f"{rank} |", end="")
            
            for col_idx in range(8):
                piece = self.board[row_idx][col_idx]
                unicode_piece = self.PIECE_UNICODE.get(piece, " ")
                
                if (row_idx, col_idx) in highlight_squares:
                    print(f"*{unicode_piece}*", end="|")
                else:
                    print(f" {unicode_piece} ", end="|")
            
            print(f" {rank}")
            print("  +---+---+---+---+---+---+---+---+")
        
        print("    a   b   c   d   e   f   g   h\n")
        print(f"Turn: {self.turn.upper()}")
        
        # Show status
        in_check = self.is_in_check("white" if self.turn == "white" else "black" if self.turn == "black" else True)
        if in_check:
            print(f"⚠️  {self.turn.upper()} IS IN CHECK!")
        print()

    def get_piece(self, row, col):
        return self.board[row][col]
    
    def is_square_attacked_fast(self, row, col, by_white):
        """
        OPTIMIZED: Check if a square is attacked using pre-computed lookup tables (Stockfish-inspired).
        Uses O(1) lookup for knights, kings, pawns and early-exit for sliding pieces.
        """
        # Check pawns (O(1) - lookup table)
        pawn = "P" if by_white else "p"
        pawn_sources = _PAWN_ATTACKS["white" if by_white else "black"].get((row, col), [])
        for pawn_row, pawn_col in pawn_sources:
            if self.board[pawn_row][pawn_col] == pawn:
                return True
        
        # Check knights (O(1) - pre-computed)
        knight = "N" if by_white else "n"
        for nr, nc in _KNIGHT_ATTACKS.get((row, col), []):
            if self.board[nr][nc] == knight:
                return True
        
        # Check kings (O(1) - pre-computed)
        king = "K" if by_white else "k"
        for kr, kc in _KING_ATTACKS.get((row, col), []):
            if self.board[kr][kc] == king:
                return True
        
        # Check bishops and queens (diagonals) - early exit
        bishop = "B" if by_white else "b"
        queen = "Q" if by_white else "q"
        for dr, dc in [(-1, -1), (-1, 1), (1, -1), (1, 1)]:
            nr, nc = row + dr, col + dc
            while 0 <= nr < 8 and 0 <= nc < 8:
                piece = self.board[nr][nc]
                if piece != ".":
                    if piece in (bishop, queen):
                        return True
                    break  # Blocked - early exit
                nr += dr
                nc += dc
        
        # Check rooks and queens (straight lines) - early exit
        rook = "R" if by_white else "r"
        for dr, dc in [(0, 1), (0, -1), (1, 0), (-1, 0)]:
            nr, nc = row + dr, col + dc
            while 0 <= nr < 8 and 0 <= nc < 8:
                piece = self.board[nr][nc]
                if piece != ".":
                    if piece in (rook, queen):
                        return True
                    break  # Blocked - early exit
                nr += dr
                nc += dc
        
        return False
    
    def is_square_attacked(self, row, col, by_white):
        """Check if a square is attacked by the given side (uses optimized lookup tables)."""
        return self.is_square_attacked_fast(row, col, by_white)
    
    def is_in_check(self, is_white):
        """Check if the given side is in check."""
        king_pos = self.white_king_pos if is_white else self.black_king_pos
        enemy_is_white = not is_white
        return self.is_square_attacked(king_pos[0], king_pos[1], enemy_is_white)
    
    @staticmethod
    def get_piece_value(piece):
        """Return material value of a piece. Used for evaluation."""
        values = {"P": 1, "N": 3, "B": 3, "R": 5, "Q": 9, "K": 0}
        return values.get(piece.upper(), 0)
    
    def _compute_material_count(self):
        """Compute total material count for both sides."""
        self._material_count = {"white": 0, "black": 0}
        for row in range(8):
            for col in range(8):
                piece = self.board[row][col]
                if piece != ".":
                    side = "white" if piece.isupper() else "black"
                    self._material_count[side] += self.get_piece_value(piece)
    
    def get_material_count(self, is_white):
        """Get cached material count for a side (faster than recomputing)."""
        side = "white" if is_white else "black"
        return self._material_count[side]
    
    def material_imbalance(self):
        """Return material imbalance (positive = white advantage, negative = black advantage)."""
        return self._material_count["white"] - self._material_count["black"]
    
    @staticmethod
    def encode_move(start, end, promotion=""):
        """Encode move as compact tuple for efficient move handling."""
        return (start[0] << 6 | start[1], end[0] << 6 | end[1], promotion)
    
    @staticmethod
    def decode_move(encoded_move):
        """Decode compact move representation back to (start, end, promotion)."""
        start_enc, end_enc, promotion = encoded_move
        start = (start_enc >> 6, start_enc & 0x3F)
        end = (end_enc >> 6, end_enc & 0x3F)
        return start, end, promotion

    def _xor_piece_hash(self, piece, row, col):
        if piece != ".":
            piece_index = self.PIECE_TO_ZOBRIST_INDEX[piece]
            self._piece_hash ^= self.ZOBRIST_PIECES[row][col][piece_index]

    def compute_piece_positions(self):
        positions = {"white": set(), "black": set()}
        for row in range(8):
            for col in range(8):
                piece = self.board[row][col]
                if piece == ".":
                    continue
                side = "white" if piece.isupper() else "black"
                positions[side].add((row, col))
        return positions

    def _remove_piece_position(self, piece, row, col):
        if piece == ".":
            return
        side = "white" if piece.isupper() else "black"
        self.piece_positions[side].discard((row, col))

    def _add_piece_position(self, piece, row, col):
        if piece == ".":
            return
        side = "white" if piece.isupper() else "black"
        self.piece_positions[side].add((row, col))

    def iter_side_pieces(self, is_white):
        side = "white" if is_white else "black"
        return tuple(self.piece_positions[side])

    def compute_piece_hash(self):
        piece_hash = 0
        for row in range(8):
            for col in range(8):
                piece = self.board[row][col]
                if piece != ".":
                    piece_index = self.PIECE_TO_ZOBRIST_INDEX[piece]
                    piece_hash ^= self.ZOBRIST_PIECES[row][col][piece_index]
        return piece_hash

    def refresh_zobrist_hash(self):
        self._piece_hash = self.compute_piece_hash()
        self._cached_castling_index = self.get_castling_state_index()
        self.piece_positions = self.compute_piece_positions()
        for row in range(8):
            for col in range(8):
                piece = self.board[row][col]
                if piece == "K":
                    self.white_king_pos = (row, col)
                elif piece == "k":
                    self.black_king_pos = (row, col)

    def get_castling_state_index(self):
        cr = self.castling_rights
        return (
            (1 if cr["white_kingside"] else 0)
            | (2 if cr["white_queenside"] else 0)
            | (4 if cr["black_kingside"] else 0)
            | (8 if cr["black_queenside"] else 0)
        )

    @property
    def zobrist_hash(self):
        """Zobrist hash for position (optimized using cached castling index)."""
        value = self._piece_hash
        if self.turn == "black":
            value ^= self.ZOBRIST_SIDE_TO_MOVE
        value ^= self.ZOBRIST_CASTLING[self._cached_castling_index]
        if self.en_passant_target is not None:
            value ^= self.ZOBRIST_EN_PASSANT[self.en_passant_target[1]]
        return value

    def to_fen(self, halfmove_clock=None, fullmove_number=1):
        rows = []
        for row in self.board:
            empty_count = 0
            fen_row = []

            for piece in row:
                if piece == ".":
                    empty_count += 1
                else:
                    if empty_count:
                        fen_row.append(str(empty_count))
                        empty_count = 0
                    fen_row.append(piece)

            if empty_count:
                fen_row.append(str(empty_count))

            rows.append("".join(fen_row))

        active_color = "w" if self.turn == "white" else "b"

        castling = []
        if self.castling_rights["white_kingside"]:
            castling.append("K")
        if self.castling_rights["white_queenside"]:
            castling.append("Q")
        if self.castling_rights["black_kingside"]:
            castling.append("k")
        if self.castling_rights["black_queenside"]:
            castling.append("q")
        castling_field = "".join(castling) if castling else "-"

        if self.en_passant_target is None:
            en_passant = "-"
        else:
            ep_row, ep_col = self.en_passant_target
            en_passant = f"{chr(ord('a') + ep_col)}{8 - ep_row}"

        if halfmove_clock is None:
            halfmove_clock = self.halfmove_clock

        return (
            f"{'/'.join(rows)} {active_color} {castling_field} "
            f"{en_passant} {halfmove_clock} {fullmove_number}"
        )

    def set_fen(self, fen):
        fields = fen.strip().split()
        if len(fields) < 4:
            raise ValueError("FEN must include board, turn, castling, and en-passant fields")

        board_field, active_color, castling_field, ep_field = fields[:4]
        halfmove_clock = int(fields[4]) if len(fields) > 4 else 0

        rows = board_field.split("/")
        if len(rows) != 8:
            raise ValueError("FEN board must contain 8 ranks")

        board = []
        white_king = None
        black_king = None
        for row_index, fen_row in enumerate(rows):
            row = []
            for char in fen_row:
                if char.isdigit():
                    row.extend(["."] * int(char))
                elif char in self.PIECE_TO_ZOBRIST_INDEX:
                    if char == "K":
                        white_king = (row_index, len(row))
                    elif char == "k":
                        black_king = (row_index, len(row))
                    row.append(char)
                else:
                    raise ValueError(f"Invalid FEN piece: {char}")
            if len(row) != 8:
                raise ValueError("Each FEN rank must contain 8 squares")
            board.append(row)

        if white_king is None or black_king is None:
            raise ValueError("FEN must contain both kings")

        self.board = board
        self.turn = "white" if active_color == "w" else "black"
        self.castling_rights = {
            "white_kingside": "K" in castling_field,
            "white_queenside": "Q" in castling_field,
            "black_kingside": "k" in castling_field,
            "black_queenside": "q" in castling_field,
        }
        if ep_field == "-":
            self.en_passant_target = None
        else:
            self.en_passant_target = (8 - int(ep_field[1]), ord(ep_field[0]) - ord("a"))
        self.halfmove_clock = halfmove_clock
        self.white_king_pos = white_king
        self.black_king_pos = black_king
        self.refresh_zobrist_hash()
        self.position_counts = {self.get_position_key(): 1}

    def get_position_key(self):
        return self.zobrist_hash

    def record_current_position(self):
        position_key = self.get_position_key()
        self.position_counts[position_key] = self.position_counts.get(position_key, 0) + 1

    def get_promotion_piece(self, piece, promotion_choice=None):
        if promotion_choice is None:
            return "Q" if piece.isupper() else "q"

        normalized_choice = promotion_choice.upper()
        if normalized_choice not in {"Q", "R", "B", "N"}:
            normalized_choice = "Q"

        return normalized_choice if piece.isupper() else normalized_choice.lower()

    def move_piece(self, start, end, promotion_choice=None):
        self.make_move(start, end, promotion_choice)
        self.turn = "black" if self.turn == "white" else "white"
        self.record_current_position()

    def update_castling_rights_for_rook(self, piece, row, col):
        if piece == "R":
            if (row, col) == (7, 0):
                self.castling_rights["white_queenside"] = False
            elif (row, col) == (7, 7):
                self.castling_rights["white_kingside"] = False
        elif piece == "r":
            if (row, col) == (0, 0):
                self.castling_rights["black_queenside"] = False
            elif (row, col) == (0, 7):
                self.castling_rights["black_kingside"] = False

    def make_move(self, start, end, promotion_choice=None):
        sr, sc = start
        er, ec = end

        piece = self.board[sr][sc]
        captured = self.board[er][ec]
        previous_castling_rights = self.castling_rights.copy()
        previous_en_passant_target = self.en_passant_target
        previous_halfmove_clock = self.halfmove_clock
        previous_piece_hash = self._piece_hash
        rook_move = None
        en_passant_capture = None
        promoted_from = None

        self._xor_piece_hash(piece, sr, sc)
        self._remove_piece_position(piece, sr, sc)
        if captured != ".":
            self._xor_piece_hash(captured, er, ec)
            self._remove_piece_position(captured, er, ec)

        if piece == "K":
            self.castling_rights["white_kingside"] = False
            self.castling_rights["white_queenside"] = False
        elif piece == "k":
            self.castling_rights["black_kingside"] = False
            self.castling_rights["black_queenside"] = False

        self.update_castling_rights_for_rook(piece, sr, sc)
        self.update_castling_rights_for_rook(captured, er, ec)

        self.en_passant_target = None

        is_en_passant = (
            piece.lower() == "p"
            and ec != sc
            and captured == "."
            and previous_en_passant_target == (er, ec)
        )
        if is_en_passant:
            capture_row = sr
            captured = self.board[capture_row][ec]
            self._xor_piece_hash(captured, capture_row, ec)
            self._remove_piece_position(captured, capture_row, ec)
            self.board[capture_row][ec] = "."
            en_passant_capture = ((capture_row, ec), captured)

        self.board[sr][sc] = "."
        self.board[er][ec] = piece

        if piece == "P" and er == 0:
            self.board[er][ec] = self.get_promotion_piece(piece, promotion_choice)
            promoted_from = "P"
        elif piece == "p" and er == 7:
            self.board[er][ec] = self.get_promotion_piece(piece, promotion_choice)
            promoted_from = "p"

        self._xor_piece_hash(self.board[er][ec], er, ec)
        self._add_piece_position(self.board[er][ec], er, ec)

        if piece.lower() == "p" or captured != ".":
            self.halfmove_clock = 0
        else:
            self.halfmove_clock += 1

        if piece.lower() == "p" and abs(er - sr) == 2:
            self.en_passant_target = ((sr + er) // 2, sc)

        is_castle = piece in ("K", "k") and abs(ec - sc) == 2
        if is_castle:
            rook_start_col = 7 if ec > sc else 0
            rook_end_col = 5 if ec > sc else 3
            rook_piece = self.board[er][rook_start_col]
            self._xor_piece_hash(rook_piece, er, rook_start_col)
            self._remove_piece_position(rook_piece, er, rook_start_col)
            self.board[er][rook_start_col] = "."
            self.board[er][rook_end_col] = rook_piece
            self._xor_piece_hash(rook_piece, er, rook_end_col)
            self._add_piece_position(rook_piece, er, rook_end_col)
            rook_move = ((er, rook_start_col), (er, rook_end_col))

        # Incrementally update cached king positions (O(1) vs O(64) scan)
        prev_white_king_pos = self.white_king_pos
        prev_black_king_pos = self.black_king_pos
        if piece == "K":
            self.white_king_pos = (er, ec)
        elif piece == "k":
            self.black_king_pos = (er, ec)

        # Update cached castling state index (faster for zobrist calculation)
        self._cached_castling_index = self.get_castling_state_index()
        
        # Update material count if capture or promotion occurred
        if captured != ".":
            side = "white" if captured.isupper() else "black"
            self._material_count[side] -= self.get_piece_value(captured)
        
        if promoted_from is not None:
            side = "white" if piece.isupper() else "black"
            self._material_count[side] -= self.get_piece_value(promoted_from)
            self._material_count[side] += self.get_piece_value(self.board[er][ec])

        move_state = {
            "captured": captured,
            "en_passant_capture": en_passant_capture,
            "previous_castling_rights": previous_castling_rights,
            "previous_en_passant_target": previous_en_passant_target,
            "previous_halfmove_clock": previous_halfmove_clock,
            "previous_piece_hash": previous_piece_hash,
            "promoted_from": promoted_from,
            "rook_move": rook_move,
            "prev_white_king_pos": prev_white_king_pos,
            "prev_black_king_pos": prev_black_king_pos,
        }
        
        # Track move in history for undo/redo
        self.move_history.append((start, end, move_state))
        
        return move_state

    def undo_move(self, start, end, move_state):
        sr, sc = start
        er, ec = end

        piece = self.board[er][ec]
        captured = move_state["captured"]
        en_passant_capture = move_state["en_passant_capture"]
        promoted_from = move_state["promoted_from"]
        rook_move = move_state["rook_move"]

        if promoted_from is not None:
            piece = promoted_from

        if rook_move is not None:
            rook_start, rook_end = rook_move
            rsr, rsc = rook_start
            rer, rec = rook_end
            rook_piece = self.board[rer][rec]
            self._remove_piece_position(rook_piece, rer, rec)
            self.board[rer][rec] = "."
            self.board[rsr][rsc] = rook_piece
            self._add_piece_position(rook_piece, rsr, rsc)

        self._remove_piece_position(self.board[er][ec], er, ec)
        self.board[sr][sc] = piece
        self.board[er][ec] = captured
        self._add_piece_position(piece, sr, sc)
        self._add_piece_position(captured, er, ec)
        if en_passant_capture is not None:
            (capture_row, capture_col), captured_piece = en_passant_capture
            self._remove_piece_position(captured, er, ec)
            self.board[er][ec] = "."
            self.board[capture_row][capture_col] = captured_piece
            self._add_piece_position(captured_piece, capture_row, capture_col)
        self.castling_rights = move_state["previous_castling_rights"]
        self.en_passant_target = move_state["previous_en_passant_target"]
        self.halfmove_clock = move_state["previous_halfmove_clock"]
        self._piece_hash = move_state["previous_piece_hash"]
        self.white_king_pos = move_state["prev_white_king_pos"]
        self.black_king_pos = move_state["prev_black_king_pos"]
        
        # Update cached castling state index
        self._cached_castling_index = self.get_castling_state_index()
        
        # Recompute material count (simpler than tracking all changes)
        self._compute_material_count()
        
        # Remove from move history
        if self.move_history and self.move_history[-1][:2] == (start, end):
            self.move_history.pop()
    
    def get_last_move(self):
        """Get the last move made (start, end, promotion) or None."""
        if not self.move_history:
            return None
        start, end, move_state = self.move_history[-1]
        promotion = move_state.get("promoted_from", "")
        return (start, end, promotion)
    
    def get_move_count(self):
        """Get total number of moves made."""
        return len(self.move_history)
    
    def reset(self):
        """Reset board to starting position."""
        self.__init__()
    
    def get_fen_with_move_number(self, move_number=1):
        """Get FEN string with move number for PGN export."""
        halfmove = self.halfmove_clock
        fullmove = move_number
        return self.to_fen(halfmove, fullmove)
    
    def is_promotion_move(self, start, end):
        """Check if a move is a promotion move."""
        sr, sc = start
        er, ec = end
        piece = self.board[sr][sc]
        return piece.upper() == 'P' and ((piece.isupper() and er == 0) or (piece.islower() and er == 7))
    
    def square_to_algebraic(self, row, col):
        """Convert board coordinates (row, col) to algebraic notation (e.g., 'e4')."""
        file = chr(ord('a') + col)
        rank = str(8 - row)
        return file + rank
    
    def algebraic_to_square(self, algebraic):
        """Convert algebraic notation (e.g., 'e4') to board coordinates (row, col)."""
        if len(algebraic) != 2:
            return None
        file = algebraic[0]
        rank = algebraic[1]
        if file < 'a' or file > 'h' or rank < '1' or rank > '8':
            return None
        col = ord(file) - ord('a')
        row = 8 - int(rank)
        return (row, col)
    
    def evaluate_position(self):
        """
        OPTIMIZED: Fast position evaluation (positive = white advantage, negative = black advantage).
        Uses cached material count (O(1)) instead of recomputing.
        Based on material count and piece positioning (Stockfish-inspired evaluation).
        """
        # Material count is already cached and updated incrementally - O(1) lookup!
        score = self.material_imbalance()
        
        # Positional bonuses for piece control (faster computation)
        # Pre-computed center distance lookup would be even faster, but this is good enough
        for row in range(8):
            for col in range(8):
                piece = self.board[row][col]
                if piece == ".":
                    continue
                
                # Center control bonus (optimized: avoid float operations)
                center_distance = abs(row - 3) + abs(col - 3)  # Faster than 3.5
                
                if piece.upper() != 'K':  # Don't count king for positioning
                    # Integer arithmetic instead of floats (Stockfish trick)
                    center_bonus = max(0, 8 - center_distance)  # 0-8 range
                    if piece.isupper():
                        score += center_bonus
                    else:
                        score -= center_bonus
        
        return score
    
    def estimate_best_moves(self, generated_moves, max_moves=5):
        """
        OPTIMIZED: Estimate best moves using move ordering heuristics (Stockfish-inspired).
        Uses capture moves first (MVV/LVA - Most Valuable Victim / Least Valuable Attacker).
        Returns list of (move, score) tuples sorted by score.
        """
        # Separate moves into captures and quiet moves for better move ordering
        captures = []
        quiet_moves = []
        
        for start, end, promo in generated_moves[:min(len(generated_moves), 20)]:
            target_piece = self.board[end[0]][end[1]]
            if target_piece != ".":
                # Capture move - prioritize by victim value (MVV)
                victim_value = self.get_piece_value(target_piece)
                attacker_value = self.get_piece_value(self.board[start[0]][start[1]])
                # Higher MVV/LVA score = better move ordering
                mvv_lva = (victim_value << 4) - attacker_value
                captures.append(((start, end, promo), mvv_lva))
            else:
                quiet_moves.append((start, end, promo))
        
        # Sort captures by MVV/LVA score (best captures first)
        captures.sort(key=lambda x: x[1], reverse=True)
        
        # Evaluate moves in priority order (captures first)
        move_scores = []
        for move, _ in captures:
            start, end, promo = move
            move_state = self.make_move(start, end, promo)
            self.turn = "black" if self.turn == "white" else "white"
            eval_score = self.evaluate_position()
            move_scores.append((move, eval_score))
            self.turn = "black" if self.turn == "white" else "white"
            self.undo_move(start, end, move_state)
        
        # Evaluate quiet moves (if needed to fill top moves list)
        for start, end, promo in quiet_moves[:min(len(quiet_moves), max_moves * 2)]:
            move_state = self.make_move(start, end, promo)
            self.turn = "black" if self.turn == "white" else "white"
            eval_score = self.evaluate_position()
            move_scores.append(((start, end, promo), eval_score))
            self.turn = "black" if self.turn == "white" else "white"
            self.undo_move(start, end, move_state)
        
        # Sort by score (best for current player at top)
        if self.turn == "white":
            move_scores.sort(key=lambda x: x[1], reverse=True)
        else:
            move_scores.sort(key=lambda x: x[1])
        
        return move_scores[:max_moves]
    
    def get_best_move_suggestions(self, move_list, max_suggestions=3):
        """
        Get best move suggestions with evaluation.
        Returns formatted strings showing move and evaluation.
        """
        suggestions = []
        
        if not move_list:
            return suggestions
        
        best_moves = self.estimate_best_moves(move_list, max_suggestions)
        
        for idx, (move, score) in enumerate(best_moves, 1):
            start, end, promo = move
            from_sq = self.square_to_algebraic(start[0], start[1])
            to_sq = self.square_to_algebraic(end[0], end[1])
            
            move_str = f"{from_sq}{to_sq}"
            if promo:
                move_str += promo.lower()
            
            # Format evaluation
            if abs(score) > 5:
                if score > 0:
                    eval_str = f"+{score:.1f}"
                else:
                    eval_str = f"{score:.1f}"
            else:
                eval_str = f"{score:.2f}"
            
            suggestion = f"  {idx}. {move_str:6} (eval: {eval_str:>6})"
            suggestions.append(suggestion)
        
        return suggestions
    
    def get_move_continuation(self, start, end, promo="", depth=2):
        """
        Get the best continuation after a move.
        Shows what happens after the suggested move.
        Returns list of move continuations with evaluations.
        """
        continuations = []
        
        # Make the move
        move_state = self.make_move(start, end, promo)
        self.turn = "black" if self.turn == "white" else "white"
        
        try:
            # Get legal moves for the next player
            next_moves = []
            for row in range(8):
                for col in range(8):
                    piece = self.board[row][col]
                    if piece != "." and (piece.isupper() if self.turn == "white" else piece.islower()):
                        moves = []
                        for er in range(8):
                            for ec in range(8):
                                # Simplified - just collect all pseudo-legal moves
                                if self.board[er][ec] == "." or (self.board[er][ec].isupper() != self.turn == "white"):
                                    next_moves.append(((row, col), (er, ec), ""))
            
            # Get best moves for next position
            best_next = self.estimate_best_moves(next_moves[:10], max_moves=3)
            
            for move, score in best_next[:2]:  # Show top 2 continuations
                m_start, m_end, m_promo = move
                from_sq = self.square_to_algebraic(m_start[0], m_start[1])
                to_sq = self.square_to_algebraic(m_end[0], m_end[1])
                move_str = f"{from_sq}{to_sq}"
                
                if abs(score) > 5:
                    eval_str = f"+{score:.1f}" if score > 0 else f"{score:.1f}"
                else:
                    eval_str = f"{score:.2f}"
                
                continuations.append(f"{move_str} ({eval_str})")
        finally:
            # Undo the move
            self.turn = "black" if self.turn == "white" else "white"
            self.undo_move(start, end, move_state)
        
        return continuations
    
    def print_position_analysis(self):
        """Print detailed analysis of current position."""
        print("\n" + "=" * 50)
        print("POSITION ANALYSIS")
        print("=" * 50)
        
        # Material count
        white_mat = self.get_material_count(True)
        black_mat = self.get_material_count(False)
        imbalance = self.material_imbalance()
        
        print(f"\nMaterial:")
        print(f"  White: {white_mat}")
        print(f"  Black: {black_mat}")
        print(f"  Imbalance: {'+' if imbalance > 0 else ''}{imbalance:.1f}\n")
        
        # Position evaluation
        eval_score = self.evaluate_position()
        print(f"Position Evaluation: {'+' if eval_score > 0 else ''}{eval_score:.2f}")
        
        # Check status
        is_in_check = self.is_in_check(self.turn == "white")
        if is_in_check:
            print(f"⚠️  {self.turn.upper()} is in CHECK\n")
        else:
            print(f"✓ {self.turn.upper()} is NOT in check\n")
        
        # King positions
        white_king_sq = self.square_to_algebraic(self.white_king_pos[0], self.white_king_pos[1])
        black_king_sq = self.square_to_algebraic(self.black_king_pos[0], self.black_king_pos[1])
        print(f"King Positions:")
        print(f"  ♔ White King: {white_king_sq}")
        print(f"  ♚ Black King: {black_king_sq}\n")
        
        print("=" * 50 + "\n")

